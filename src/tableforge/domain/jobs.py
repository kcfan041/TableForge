from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from tableforge.domain.artifacts import Artifact
from tableforge.domain.ids import AttemptId, ExperimentId, JobId, NodeId


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
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    cpu_cores: int = 1
    memory_bytes: int = 0
    gpu_count: int = 0
    gpu_memory_bytes: int = 0
    storage_bytes: int = 0
    required_role: str = ""
    required_runtime: str = "cpu"
    exclusive_gpu: bool = False

    def __post_init__(self) -> None:
        values = (
            self.cpu_cores,
            self.memory_bytes,
            self.gpu_count,
            self.gpu_memory_bytes,
            self.storage_bytes,
        )
        if any(value < 0 for value in values):
            raise ValueError("resource values cannot be negative")
        if not self.required_runtime:
            raise ValueError("required_runtime cannot be empty")


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


@dataclass(frozen=True, slots=True)
class JobAttemptProvenance:
    attempt_id: AttemptId
    node_id: NodeId
    capability_revision: int
    workload_key: str
    workload_version: str
    execution_plan_json: str


@dataclass(frozen=True, slots=True)
class JobAttemptError:
    attempt_id: AttemptId
    error_code: str
    error_summary: str
    retryable: bool
    failed_at: datetime
    diagnostic_artifact_id: str | None = None

    def __post_init__(self) -> None:
        if not self.error_code or not self.error_summary:
            raise ValueError("error code and summary cannot be empty")
        if self.failed_at.tzinfo is None:
            raise ValueError("failed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class JobOutput:
    job_id: JobId
    artifact: Artifact
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("output name cannot be empty")


@dataclass(frozen=True, slots=True)
class JobLease:
    job: Job
    attempt_id: AttemptId
    node_id: NodeId
    token: str
    attempt_number: int
    leased_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("lease token cannot be empty")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.leased_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("lease times must be timezone-aware")
        if self.expires_at <= self.leased_at:
            raise ValueError("lease expiration must be after leased_at")
