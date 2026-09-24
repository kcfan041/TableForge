from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum

POPULATION_SCHEMA = "tableforge.league-population.v1"
MATCHUP_PAYLOAD_TYPE = "tableforge.v1.LeagueMatchupJobPayload"
MATCHUP_SCHEMA = "tableforge.league-matchup.v1"
RULE_BOTS = ("call", "random", "tight", "aggro")


class MemberKind(StrEnum):
    SELF = "self"
    CHECKPOINT = "checkpoint"
    RULE = "rule"


@dataclass(frozen=True, slots=True)
class LeagueMember:
    name: str
    kind: MemberKind
    weight: float = 0.0
    artifact_id: str = ""
    sha256: str = ""
    rule: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("league member name cannot be empty")
        if self.weight < 0:
            raise ValueError("league member weight cannot be negative")
        if self.kind is MemberKind.CHECKPOINT and (not self.artifact_id or len(self.sha256) != 64):
            raise ValueError("checkpoint member requires artifact reference")
        if self.kind is MemberKind.RULE and self.rule not in RULE_BOTS:
            raise ValueError(f"unknown rule bot: {self.rule}")

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LeagueMember:
        return cls(
            name=str(data["name"]),
            kind=MemberKind(str(data["kind"])),
            weight=float(data.get("weight", 0.0)),  # type: ignore[arg-type]
            artifact_id=str(data.get("artifact_id", "")),
            sha256=str(data.get("sha256", "")),
            rule=str(data.get("rule", "")),
        )


@dataclass(frozen=True, slots=True)
class LeaguePopulation:
    round_index: int
    learner_sha256: str
    members: tuple[LeagueMember, ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("league population cannot be empty")
        if sum(member.weight for member in self.members) <= 0:
            raise ValueError("league population weights must be positive")
        names = [member.name for member in self.members]
        if len(names) != len(set(names)):
            raise ValueError("league member names must be unique")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "learner_sha256": self.learner_sha256,
                "members": [member.to_dict() for member in self.members],
                "round_index": self.round_index,
                "schema_version": POPULATION_SCHEMA,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_bytes(cls, content: bytes) -> LeaguePopulation:
        data = json.loads(content)
        if data.get("schema_version") != POPULATION_SCHEMA:
            raise ValueError("unsupported league population schema")
        return cls(
            round_index=int(data["round_index"]),
            learner_sha256=str(data["learner_sha256"]),
            members=tuple(LeagueMember.from_dict(item) for item in data["members"]),
        )


@dataclass(frozen=True, slots=True)
class HistoricalCheckpoint:
    name: str
    artifact_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MatchmakingWeights:
    current: float = 0.4
    historical: float = 0.3
    baseline: float = 0.3


DEFAULT_WEIGHTS = MatchmakingWeights()


def build_population(
    round_index: int,
    learner_sha256: str,
    historical: tuple[HistoricalCheckpoint, ...],
    weights: MatchmakingWeights = DEFAULT_WEIGHTS,
) -> LeaguePopulation:
    current = weights.current
    if not historical:
        current += weights.historical
    members = [LeagueMember("self", MemberKind.SELF, current)]
    for item in historical:
        members.append(
            LeagueMember(
                item.name,
                MemberKind.CHECKPOINT,
                weights.historical / len(historical),
                artifact_id=item.artifact_id,
                sha256=item.sha256,
            )
        )
    for rule in RULE_BOTS:
        members.append(
            LeagueMember(
                f"rule:{rule}", MemberKind.RULE, weights.baseline / len(RULE_BOTS), rule=rule
            )
        )
    return LeaguePopulation(round_index, learner_sha256, tuple(members))


@dataclass(frozen=True, slots=True)
class LeagueMatchupPayload:
    schema_version: str
    first: LeagueMember
    second: LeagueMember
    deals: int
    seed_start: int

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported matchup payload schema")
        if self.deals < 2:
            raise ValueError("at least two deals are required")
        if MemberKind.SELF in (self.first.kind, self.second.kind):
            raise ValueError("matchups require concrete members")
        if self.first.name == self.second.name:
            raise ValueError("a member cannot play itself")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "deals": self.deals,
                "first": self.first.to_dict(),
                "schema_version": self.schema_version,
                "second": self.second.to_dict(),
                "seed_start": self.seed_start,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> LeagueMatchupPayload:
        data = json.loads(content)
        return cls(
            schema_version=str(data["schema_version"]),
            first=LeagueMember.from_dict(data["first"]),
            second=LeagueMember.from_dict(data["second"]),
            deals=int(data["deals"]),
            seed_start=int(data["seed_start"]),
        )


@dataclass(frozen=True, slots=True)
class MatrixCell:
    bb_per_100: float
    confidence_low: float
    confidence_high: float
    hands: int


def build_matrix(
    names: tuple[str, ...], results: list[dict[str, object]]
) -> dict[str, dict[str, MatrixCell]]:
    matrix: dict[str, dict[str, MatrixCell]] = {name: {} for name in names}
    for result in results:
        first = str(result["first"])
        second = str(result["second"])
        mean = float(result["bb_per_100"])  # type: ignore[arg-type]
        low = float(result["confidence_low"])  # type: ignore[arg-type]
        high = float(result["confidence_high"])  # type: ignore[arg-type]
        hands = int(result["hands"])  # type: ignore[call-overload]
        matrix[first][second] = MatrixCell(mean, low, high, hands)
        matrix[second][first] = MatrixCell(-mean, -high, -low, hands)
    return matrix
