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
