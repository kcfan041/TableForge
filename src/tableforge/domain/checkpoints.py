from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tableforge.domain.ids import AgentId, ArtifactId, CheckpointId, ExperimentId


class CheckpointStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: CheckpointId
    agent_id: AgentId
    experiment_id: ExperimentId
    round_index: int
    parent_id: CheckpointId | None
    artifact_id: ArtifactId
    sha256: str
    status: CheckpointStatus
    decision_reason: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.round_index < 0:
            raise ValueError("round_index cannot be negative")
        if len(self.sha256) != 64:
            raise ValueError("sha256 must be a hexadecimal digest")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("checkpoint timestamps must be timezone-aware")


class CheckpointTransitionError(ValueError):
    pass
