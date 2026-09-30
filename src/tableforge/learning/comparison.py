"""Comparing models (and baselines) that play the same tournament.

A comparison plays every ordered pair of competitors: for (A, B), A takes one seat and every
other seat is B, over the same seeds with A's seat rotated (the usual benchmark protocol).
The work is split into one job per row (one A against every B), so rows run in parallel on
different workers; `summarize` joins the rows. The result is a matrix of A's ROI against a
field of B, with 95 % confidence intervals, and each competitor's **average ROI** over all
opponents (each opponent weighted equally, averaged per seed so the interval is paired).

There is deliberately no Elo-style rating: "beat another seat" counts mostly reward not
busting, while tournament money is concentrated at the top, so such a rating can rank a
tight strategy with a clearly negative ROI above profitable ones.

Strategies can counter each other, so a single matchup is information, not a verdict.
Competitors are named ``model:<id>`` (with a play mode) or ``rule:<bot>``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tableforge.domain.artifacts import ArtifactKind
from tableforge.learning.opponents import PoolMember, model_member, rule_members
from tableforge.poker.tournament import Tournament, TournamentConfig
from tableforge.poker.tournament_benchmark import Estimate

COMPARISON_PAYLOAD_TYPE = "tableforge.v1.ModelComparisonJobPayload"
COMPARISON_SCHEMA = "tableforge.model-comparison.v1"
ROW_SCHEMA = "tableforge.model-comparison-row.v1"
MAX_COMPETITORS = 10


@dataclass(frozen=True, slots=True)
class Competitor:
    name: str
    """``model:<id>`` or ``rule:<bot>``."""
    artifact_id: str = ""
    sha256: str = ""
    mode: str = ""

    def __post_init__(self) -> None:
        kind, _, rest = self.name.partition(":")
        if kind not in ("model", "rule") or not rest:
            raise ValueError("a competitor is model:<id> or rule:<bot>")
        if kind == "model" and (not self.artifact_id or len(self.sha256) != 64):
            raise ValueError("a model competitor needs its checkpoint artifact and sha256")

    @property
    def label(self) -> str:
        if self.name.startswith("model:") and self.mode and self.mode != "average":
            return f"{self.name} ({self.mode})"
        return self.name


@dataclass(frozen=True, slots=True)
class ComparisonPayload:
    """One row of a comparison: competitor `row` against every other competitor."""

    schema_version: str
    config_json: str
    competitors: tuple[Competitor, ...]
    seeds: int
    seed_start: int
    row: int

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported comparison payload schema")
        TournamentConfig.from_json(self.config_json)
        if not 2 <= len(self.competitors) <= MAX_COMPETITORS:
            raise ValueError(f"compare between 2 and {MAX_COMPETITORS} competitors")
        if len({item.label for item in self.competitors}) != len(self.competitors):
            raise ValueError("competitors must be different")
        if not 1 <= self.seeds <= 5_000:
            raise ValueError("seeds must be between 1 and 5,000")
        if not 0 <= self.row < len(self.competitors):
            raise ValueError("row must be one of the competitors")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "config_json": self.config_json,
                "competitors": [
                    {
                        "name": item.name,
                        "artifact_id": item.artifact_id,
                        "sha256": item.sha256,
                        "mode": item.mode,
                    }
                    for item in self.competitors
                ],
                "seeds": self.seeds,
                "seed_start": self.seed_start,
                "row": self.row,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, payload: bytes) -> ComparisonPayload:
        data = json.loads(payload)
        return cls(
            str(data["schema_version"]),
            str(data["config_json"]),
            tuple(Competitor(**item) for item in data["competitors"]),
            int(data["seeds"]),
            int(data["seed_start"]),
            int(data["row"]),
        )


def play_row(
    members: Sequence[PoolMember],
    row: int,
    config: TournamentConfig,
    seeds: int,
    seed_start: int,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, object]:
    """`members[row]` in one seat against a field of each other member.

    Per opponent: the row competitor's result per seed (mean over its seat rotations, in
    buy-ins).
    """
    hero = members[row]
    players = config.players
    against: dict[str, dict[str, object]] = {}
    for column, field in enumerate(members):
        if column == row:
            continue
        results: list[float] = []
        for index in range(seeds):
            seed = seed_start + index
            total = 0.0
            for seat in range(players):
                agents = [
                    hero.build(seed * 16 + other)
                    if other == seat
                    else field.build(seed * 16 + other)
                    for other in range(players)
                ]
                total += Tournament(config, seed).run(agents).results[seat]
            results.append(round(total / players / config.buy_in, 6))
            if on_progress is not None:
                on_progress(len(against) * seeds + index + 1)
        against[str(column)] = {"results": results}
    return {"schema_version": ROW_SCHEMA, "row": row, "against": against}


def summarize(
    names: Sequence[str],
    config: TournamentConfig,
    seeds: int,
    seed_start: int,
    rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Matrix and average ROI from finished rows (missing rows are left out)."""
    finished: dict[int, dict[int, list[float]]] = {}
    for row in rows:
        against = row["against"]
        if not isinstance(against, dict):
            raise ValueError("invalid comparison row")
        finished[int(str(row["row"]))] = {
            int(key): [float(value) for value in cell["results"]] for key, cell in against.items()
        }
    matrix: dict[str, dict[str, object]] = {}
    for first, cells in finished.items():
        for second, results in cells.items():
            matrix.setdefault(names[first], {})[names[second]] = {
                "roi": Estimate.of(results).as_dict(),
                "tournaments": len(results) * config.players,
            }
    ranking: list[tuple[float, dict[str, object]]] = []
    for index, name in enumerate(names):
        row_cells = finished.get(index)
        if not row_cells:
            continue
        per_seed = [
            sum(results[seed] for results in row_cells.values()) / len(row_cells)
            for seed in range(seeds)
        ]
        estimate = Estimate.of(per_seed)
        ranking.append((-estimate.mean, {"name": name, "average_roi": estimate.as_dict()}))
    ranking.sort(key=lambda item: item[0])
    return {
        "schema_version": COMPARISON_SCHEMA,
        "config": json.loads(config.to_json()),
        "competitors": list(names),
        "seeds": seeds,
        "seed_start": seed_start,
        "tournaments_per_pair": seeds * config.players,
        "rows_finished": len(finished),
        "matrix": matrix,
        "ranking": [item for _, item in ranking],
    }


def compare(
    members: Sequence[PoolMember],
    config: TournamentConfig,
    seeds: int,
    seed_start: int,
) -> dict[str, object]:
    """Every row in this process (tests and scripts)."""
    rows = [play_row(members, row, config, seeds, seed_start) for row in range(len(members))]
    return summarize([member.name for member in members], config, seeds, seed_start, rows)


def build_members(
    competitors: Sequence[Competitor], fetch: Callable[[Competitor], bytes]
) -> list[PoolMember]:
    members = []
    for competitor in competitors:
        kind, _, rest = competitor.name.partition(":")
        if kind == "rule":
            member = rule_members([rest])[0]
        else:
            member = model_member(
                competitor.name, fetch(competitor), mode=competitor.mode or "average"
            )
        member.name = competitor.label
        members.append(member)
    return members


@dataclass(frozen=True, slots=True)
class ComparisonExecutor:
    fetch_artifact: Callable[[str], bytes]
    output_kind: ArtifactKind = ArtifactKind.EVALUATION_RESULT
    output_media_type: str = "application/json"
    output_schema: str = ROW_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        from tableforge.learning.experience import verified

        payload = ComparisonPayload.from_bytes(payload_bytes)
        members = build_members(
            payload.competitors,
            lambda item: verified(self.fetch_artifact(item.artifact_id), item.sha256),
        )
        result = play_row(
            members,
            payload.row,
            TournamentConfig.from_json(payload.config_json),
            payload.seeds,
            payload.seed_start,
            on_progress,
        )
        return json.dumps(result, separators=(",", ":")).encode()
