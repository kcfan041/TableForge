from __future__ import annotations

import json
from dataclasses import asdict, dataclass

TRAINING_PAYLOAD_TYPE = "tableforge.v1.NfspTrainingJobPayload"
EVALUATION_PAYLOAD_TYPE = "tableforge.v1.NfspEvaluationJobPayload"
CHECKPOINT_SCHEMA = "tableforge.nfsp-checkpoint.v1"
EVALUATION_SCHEMA = "tableforge.nfsp-evaluation.v1"
OPPONENTS = ("call", "random", "tight", "aggro")


def _dump(data: dict[str, object]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class NfspTrainingPayload:
    schema_version: str
    hands: int
    seed_start: int
    agent_seed: int
    hidden_size: int = 64
    batch_size: int = 64
    train_every: int = 4

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported training payload schema")
        if self.hands < 1 or self.hidden_size < 1 or self.batch_size < 1 or self.train_every < 1:
            raise ValueError("training payload values must be positive")

    def to_bytes(self) -> bytes:
        return _dump(asdict(self))

    @classmethod
    def from_bytes(cls, payload: bytes) -> NfspTrainingPayload:
        return cls(**json.loads(payload))


@dataclass(frozen=True, slots=True)
class NfspEvaluationPayload:
    schema_version: str
    checkpoint_artifact_id: str
    checkpoint_sha256: str
    deals: int
    seed_start: int
    opponents: tuple[str, ...] = ("call", "random")

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported evaluation payload schema")
        if self.deals < 2:
            raise ValueError("at least two deals are required")
        if not self.checkpoint_artifact_id or len(self.checkpoint_sha256) != 64:
            raise ValueError("checkpoint reference is invalid")
        unknown = set(self.opponents) - set(OPPONENTS)
        if unknown or not self.opponents:
            raise ValueError(f"unknown opponents: {sorted(unknown)}")

    def to_bytes(self) -> bytes:
        data = asdict(self)
        data["opponents"] = list(self.opponents)
        return _dump(data)

    @classmethod
    def from_bytes(cls, payload: bytes) -> NfspEvaluationPayload:
        data = json.loads(payload)
        data["opponents"] = tuple(data["opponents"])
        return cls(**data)


INIT_PAYLOAD_TYPE = "tableforge.v1.NfspInitJobPayload"
ACTOR_PAYLOAD_TYPE = "tableforge.v1.NfspActorJobPayload"
LEARNER_PAYLOAD_TYPE = "tableforge.v1.NfspLearnerJobPayload"
MATCH_PAYLOAD_TYPE = "tableforge.v1.NfspMatchJobPayload"
SHARD_OUTPUT_SCHEMA = "tableforge.nfsp-experience-shard.v1"
MATCH_SCHEMA = "tableforge.nfsp-match.v1"


def _require_sha(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("sha256 reference is invalid")


@dataclass(frozen=True, slots=True)
class NfspInitPayload:
    schema_version: str
    agent_seed: int
    hidden_size: int = 64
    batch_size: int = 64

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported init payload schema")
        if self.hidden_size < 1 or self.batch_size < 1:
            raise ValueError("init payload values must be positive")

    def to_bytes(self) -> bytes:
        return _dump(asdict(self))

    @classmethod
    def from_bytes(cls, payload: bytes) -> NfspInitPayload:
        return cls(**json.loads(payload))


@dataclass(frozen=True, slots=True)
class NfspActorPayload:
    schema_version: str
    checkpoint_artifact_id: str
    checkpoint_sha256: str
    hands: int
    seed_start: int
    actor_seed: int
    population_artifact_id: str = ""
    population_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported actor payload schema")
        if self.hands < 1:
            raise ValueError("actor hands must be positive")
        _require_sha(self.checkpoint_sha256)
        if self.population_artifact_id or self.population_sha256:
            if not self.population_artifact_id:
                raise ValueError("population reference is incomplete")
            _require_sha(self.population_sha256)

    def to_bytes(self) -> bytes:
        return _dump(asdict(self))

    @classmethod
    def from_bytes(cls, payload: bytes) -> NfspActorPayload:
        return cls(**json.loads(payload))


@dataclass(frozen=True, slots=True)
class NfspLearnerPayload:
    schema_version: str
    checkpoint_artifact_id: str
    checkpoint_sha256: str
    snapshot_artifact_id: str
    snapshot_sha256: str
    train_steps: int
    round_index: int

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported learner payload schema")
        if self.train_steps < 1 or self.round_index < 1:
            raise ValueError("learner payload values must be positive")
        _require_sha(self.checkpoint_sha256)
        _require_sha(self.snapshot_sha256)

    def to_bytes(self) -> bytes:
        return _dump(asdict(self))

    @classmethod
    def from_bytes(cls, payload: bytes) -> NfspLearnerPayload:
        return cls(**json.loads(payload))


@dataclass(frozen=True, slots=True)
class NfspMatchPayload:
    schema_version: str
    candidate_artifact_id: str
    candidate_sha256: str
    baseline_artifact_id: str
    baseline_sha256: str
    seed_start: int
    min_deals: int
    max_deals: int
    batch_deals: int
    target_width: float
    reference_opponents: tuple[str, ...] = ("call", "random")
    reference_deals: int = 200

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported match payload schema")
        _require_sha(self.candidate_sha256)
        _require_sha(self.baseline_sha256)
        if self.min_deals < 2 or self.max_deals < self.min_deals or self.batch_deals < 1:
            raise ValueError("invalid match deal limits")
        if self.target_width < 0 or self.reference_deals < 2:
            raise ValueError("invalid match evaluation settings")
        unknown = set(self.reference_opponents) - set(OPPONENTS)
        if unknown:
            raise ValueError(f"unknown opponents: {sorted(unknown)}")

    def to_bytes(self) -> bytes:
        data = asdict(self)
        data["reference_opponents"] = list(self.reference_opponents)
        return _dump(data)

    @classmethod
    def from_bytes(cls, payload: bytes) -> NfspMatchPayload:
        data = json.loads(payload)
        data["reference_opponents"] = tuple(data["reference_opponents"])
        return cls(**data)
