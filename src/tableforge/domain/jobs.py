from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from tableforge.domain.artifacts import Artifact
from tableforge.domain.ids import ExperimentId, JobId


class JobType(StrEnum):
    UNSPECIFIED = "unspecified"
    SIMULATION = "simulation"
    EVALUATION = "evaluation"
    TRAINING = "training"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    cpu_cores: int = 1
    memory_bytes: int = 0
    gpu_count: int = 0
    gpu_memory_bytes: int = 0
    required_role: str = ""

    def __post_init__(self) -> None:
        if self.cpu_cores < 0:
            raise ValueError("cpu_cores cannot be negative")
        if self.memory_bytes < 0 or self.gpu_count < 0 or self.gpu_memory_bytes < 0:
            raise ValueError("resource values cannot be negative")


@dataclass(frozen=True, slots=True)
class JobSpec:
    schema_version: str
    job_id: JobId
    experiment_id: ExperimentId
    type: JobType
    priority: int
    resources: ResourceRequest
    payload: bytes
    payload_type: str
    idempotency_key: str
    max_attempts: int = 3
    inputs: tuple[Artifact, ...] = field(default_factory=tuple)
    not_before: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("schema_version cannot be empty")
        if self.type is JobType.UNSPECIFIED:
            raise ValueError("job type cannot be unspecified")
        if not self.payload_type:
            raise ValueError("payload_type cannot be empty")
        if not self.idempotency_key:
            raise ValueError("idempotency_key cannot be empty")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.not_before is not None and self.not_before.tzinfo is None:
            raise ValueError("not_before must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Job:
    spec: JobSpec
    status: JobStatus
    version: int

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ValueError("version cannot be negative")
