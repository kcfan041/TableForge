"""Analysis jobs on named models: tournament replays and preflop strategy tables.

These jobs do not belong to an experiment; they are filed under the fixed experiment id
`ANALYSIS` and are idempotent by content, so asking twice for the same replay or table
returns the same job (and its result once it exists).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from tableforge.composition import Services
from tableforge.coordinator.experiments import _profile
from tableforge.coordinator.models import checkpoint_artifact, library
from tableforge.domain.ids import ExperimentId, JobId, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType
from tableforge.models import ModelInfo

ANALYSIS = ExperimentId("analysis")
REPLAY_PAYLOAD_TYPE = "tableforge.v1.TournamentReplayJobPayload"
STRATEGY_PAYLOAD_TYPE = "tableforge.v1.StrategyTableJobPayload"
MIXED_FIELD = ("tight", "call", "aggro", "random", "pushfold", "call")
RULE_BOTS = ("random", "call", "tight", "aggro", "pushfold", "hunter", "pushfold_nash")
_GAME = re.compile(r"^(\d)max-(pko|sng)-(\d+(?:\.\d+)?)bb")


class AnalysisError(ValueError):
    pass


def tournament_json(info: ModelInfo) -> str:
    """The tournament a model plays: from its training settings, or from its game name."""
    stored = info.training.get("tournament_json")
    if isinstance(stored, str) and stored:
        return stored
    match = _GAME.match(info.game)
    if match is None:
        raise AnalysisError(f"model {info.model_id} does not play a known tournament ({info.game})")
    from tableforge.poker.tournament import BountyConfig, preset_config

    players, kind, stack = int(match.group(1)), match.group(2), float(match.group(3))
    return preset_config(
        stack, players=players, bounty=BountyConfig() if kind == "pko" else None
    ).to_json()


def _mode(info: ModelInfo, mode: str) -> str:
    from tableforge.coordinator.models import play_modes

    keys = [item["key"] for item in play_modes(info.algorithm)]
    if not mode:
        return keys[0]
    if mode not in keys:
        raise AnalysisError(f"unknown play mode {mode}; this model offers {', '.join(keys)}")
    return mode


def _create(services: Services, payload_type: str, payload: bytes) -> str:
    key = f"analysis:{payload_type}:{hashlib.sha256(payload).hexdigest()}"
    jobs = services.coordinator.jobs
    existing = jobs.get_by_idempotency_key(ANALYSIS, key)
    if existing is not None and existing.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        return str(existing.spec.job_id)
    if existing is not None:
        key = f"{key}:{new_job_id()}"
    profile = _profile(payload_type)
    created = services.coordinator.create_job(
        JobSpec(
            schema_version="1.0",
            job_id=new_job_id(),
            experiment_id=ANALYSIS,
            type=JobType.EVALUATION,
            priority=2,
            resources=profile.minimum,
            payload=payload,
            payload_type=payload_type,
            idempotency_key=key,
        )
    )
    return str(created.spec.job_id)


def request_replay(services: Services, arguments: dict[str, Any]) -> dict[str, object]:
    """Replay one tournament: the model in `seat`, the other seats from `opponents`.

    `opponents` is ``mixed`` (the benchmark field), ``self`` (copies of the model) or one
    rule bot name for every other seat.
    """
    from tableforge.learning.replay import ReplayJobPayload, SeatSpec

    info = library(services).info(str(arguments["model_id"]))
    mode = _mode(info, str(arguments.get("mode", "")))
    config = tournament_json(info)
    players = json.loads(config)["players"]
    seat = int(arguments.get("seat", 0))
    seed = int(arguments.get("seed", 1))
    opponents = str(arguments.get("opponents", "mixed"))
    if not 0 <= seat < players:
        raise AnalysisError(f"seat must be between 0 and {players - 1}")
    if not 0 <= seed < 2**31:
        raise AnalysisError("seed must be a non-negative number")
    if opponents not in ("mixed", "self", *RULE_BOTS):
        raise AnalysisError("opponents must be mixed, self or a rule bot")
    artifact_id, sha = checkpoint_artifact(services, info.model_id)
    seats = []
    for player in range(players):
        if player == seat or opponents == "self":
            seats.append(SeatSpec("model", info.model_id, artifact_id, sha, mode, info.algorithm))
        else:
            name = MIXED_FIELD[player % len(MIXED_FIELD)] if opponents == "mixed" else opponents
            seats.append(SeatSpec("rule", name))
    payload = ReplayJobPayload("1.0", config, seed, tuple(seats)).to_bytes()
    return {"job_id": _create(services, REPLAY_PAYLOAD_TYPE, payload)}


def request_strategy(services: Services, arguments: dict[str, Any]) -> dict[str, object]:
    from tableforge.learning.strategy import StrategyTablePayload

    info = library(services).info(str(arguments["model_id"]))
    mode = _mode(info, str(arguments.get("mode", "")))
    artifact_id, sha = checkpoint_artifact(services, info.model_id)
    payload = StrategyTablePayload(
        "1.0",
        tournament_json(info),
        artifact_id,
        sha,
        mode,
        info.algorithm,
        level=int(arguments.get("level", 1)),
    ).to_bytes()
    return {"job_id": _create(services, STRATEGY_PAYLOAD_TYPE, payload)}


COMPARISON_SEEDS = 200
"""Default seeds per pair: x players tournaments (1,200 at 6-max) per ordered pair."""
COMPARISON_SEED_START = 1_700_000_000
"""Comparison seeds are kept apart from training and benchmark seeds."""


def comparison_candidates(services: Services, game: str = "") -> dict[str, object]:
    """Models grouped by the tournament they play, with the rule bots and baselines."""
    from dataclasses import replace

    from tableforge.poker.tournament import TournamentConfig

    # Same engine settings = same group; names and number formatting (10 vs 10.0) do not
    # matter, so configs are compared as values rather than hashed as text.
    configs: list[TournamentConfig] = []
    groups: list[dict[str, object]] = []
    for info in library(services).list():
        try:
            parsed = replace(TournamentConfig.from_json(tournament_json(info)), name="")
        except (AnalysisError, ValueError):
            continue
        if parsed not in configs:
            configs.append(parsed)
            groups.append(
                {
                    "tournament": str(len(groups)),
                    "game": info.game,
                    "players": parsed.players,
                    "models": [],
                }
            )
        group = groups[configs.index(parsed)]
        group["models"].append(info.model_id)  # type: ignore[attr-defined]
    return {
        "groups": [item for item in groups if not game or item["game"] == game],
        "rule_bots": list(RULE_BOTS),
    }


def request_comparison(services: Services, arguments: dict[str, Any]) -> dict[str, object]:
    """Compare models (and rule bots) that play the same tournament: one job per row.

    ``models``: model ids (all must play the same tournament); ``modes``: optional play mode
    per model id; ``rules``: rule bot names; ``seeds``: seeds per pair.
    """
    from tableforge.learning.comparison import (
        COMPARISON_PAYLOAD_TYPE,
        MAX_COMPETITORS,
        ComparisonPayload,
        Competitor,
    )
    from tableforge.learning.pool_benchmark import same_tournament
    from tableforge.poker.tournament import TournamentConfig

    model_ids = [str(item) for item in arguments.get("models") or []]
    rules = [str(item) for item in arguments.get("rules") or []]
    modes = arguments.get("modes") or {}
    if not isinstance(modes, dict):
        raise AnalysisError("modes must map model ids to play modes")
    if not model_ids:
        raise AnalysisError("choose at least one model")
    if len(model_ids) + len(rules) > MAX_COMPETITORS:
        raise AnalysisError(f"compare at most {MAX_COMPETITORS} competitors")
    unknown = [rule for rule in rules if rule not in RULE_BOTS]
    if unknown:
        raise AnalysisError(f"unknown rule bots: {', '.join(unknown)}")
    seeds = int(arguments.get("seeds", COMPARISON_SEEDS))
    if not 1 <= seeds <= 2_000:
        raise AnalysisError("seeds must be between 1 and 2,000")
    config_json = ""
    competitors = []
    for model_id in model_ids:
        info = library(services).info(model_id)
        json_text = tournament_json(info)
        if not config_json:
            config_json = json_text
        elif not same_tournament(
            TournamentConfig.from_json(config_json), TournamentConfig.from_json(json_text)
        ):
            raise AnalysisError(f"model {model_id} plays a different tournament")
        artifact_id, sha = checkpoint_artifact(services, model_id)
        competitors.append(
            Competitor(
                f"model:{model_id}",
                artifact_id,
                sha,
                _mode(info, str(modes.get(model_id, ""))),
                info.algorithm,
            )
        )
    competitors.extend(Competitor(f"rule:{rule}") for rule in rules)
    if len(competitors) < 2:
        raise AnalysisError("compare at least two competitors")
    seed_start = int(arguments.get("seed_start", COMPARISON_SEED_START))
    payloads = [
        ComparisonPayload("1.0", config_json, tuple(competitors), seeds, seed_start, row)
        for row in range(len(competitors))
    ]
    group = hashlib.sha256(b"".join(item.to_bytes() for item in payloads)).hexdigest()[:24]
    jobs = [_create(services, COMPARISON_PAYLOAD_TYPE, payload.to_bytes()) for payload in payloads]
    return {"comparison_id": ",".join(jobs), "group": group, "jobs": jobs}


def comparison_result(services: Services, comparison_id: str) -> dict[str, object]:
    """Progress and (partial) result of a comparison: its id is its row job ids."""
    from tableforge.learning.comparison import COMPARISON_PAYLOAD_TYPE, ComparisonPayload, summarize
    from tableforge.poker.tournament import TournamentConfig

    job_ids = [item for item in comparison_id.split(",") if item]
    if not job_ids or len(job_ids) > 16:
        raise AnalysisError("not a comparison")
    rows = []
    statuses = []
    payload: ComparisonPayload | None = None
    for job_id in job_ids:
        job = services.coordinator.get_job(JobId(job_id))
        if job.spec.experiment_id != ANALYSIS or job.spec.payload_type != COMPARISON_PAYLOAD_TYPE:
            raise AnalysisError("not a comparison")
        payload = ComparisonPayload.from_bytes(job.spec.payload)
        statuses.append(job.status.value)
        if job.status is not JobStatus.SUCCEEDED:
            continue
        outputs = services.coordinator.job_outputs(job.spec.job_id)
        if outputs:
            content = services.coordinator.read_artifact(
                str(outputs[0].artifact.descriptor.artifact_id)
            )
            rows.append(json.loads(content))
    assert payload is not None
    names = [item.label for item in payload.competitors]
    result = summarize(
        names,
        TournamentConfig.from_json(payload.config_json),
        payload.seeds,
        payload.seed_start,
        rows,
    )
    failed = sum(status == "failed" for status in statuses)
    status = (
        "succeeded"
        if all(item == "succeeded" for item in statuses)
        else "failed"
        if failed
        else "running"
    )
    return {"status": status, "jobs": statuses, "result": result}


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    status: str
    kind: str
    result: object | None
    error: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "kind": self.kind,
            "result": self.result,
            "error": self.error,
        }


def analysis_result(services: Services, job_id: str) -> AnalysisResult:
    job = services.coordinator.get_job(JobId(job_id))
    if job.spec.experiment_id != ANALYSIS:
        raise AnalysisError("not an analysis job")
    kind = {REPLAY_PAYLOAD_TYPE: "replay", STRATEGY_PAYLOAD_TYPE: "strategy"}.get(
        job.spec.payload_type, "other"
    )
    if job.status is not JobStatus.SUCCEEDED:
        error = ""
        if job.status is JobStatus.FAILED:
            error = "the job failed; see the worker log"
        return AnalysisResult(job.status.value, kind, None, error)
    outputs = services.coordinator.job_outputs(job.spec.job_id)
    if not outputs:
        return AnalysisResult("failed", kind, None, "the job has no output")
    content = services.coordinator.read_artifact(str(outputs[0].artifact.descriptor.artifact_id))
    return AnalysisResult(job.status.value, kind, json.loads(content), "")
