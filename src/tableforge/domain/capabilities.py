from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from tableforge.domain.ids import NodeId
from tableforge.domain.jobs import ResourceRequest


class ResourceRuntime(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


class CompatibilityStatus(StrEnum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ReasonCode(StrEnum):
    GPU_NOT_FOUND = "GPU_NOT_FOUND"
    INSUFFICIENT_VRAM = "INSUFFICIENT_VRAM"
    INSUFFICIENT_SYSTEM_MEMORY = "INSUFFICIENT_SYSTEM_MEMORY"
    INSUFFICIENT_CPU = "INSUFFICIENT_CPU"
    INSUFFICIENT_DISK = "INSUFFICIENT_DISK"
    ROLE_DISABLED = "ROLE_DISABLED"
    RUNTIME_VERSION_UNSUPPORTED = "RUNTIME_VERSION_UNSUPPORTED"
    CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"


@dataclass(frozen=True, slots=True)
class GpuCapability:
    device_id: str
    vendor: str
    model: str
    total_memory_bytes: int | None
    available_memory_bytes: int | None
    driver_version: str | None = None
    compute_capability: str | None = None
    runtimes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.total_memory_bytes, self.available_memory_bytes):
            if value is not None and value < 0:
                raise ValueError("GPU memory cannot be negative")


@dataclass(frozen=True, slots=True)
class StorageCapability:
    volume_id: str
    available_bytes: int | None
    storage_class: str = "unknown"

    def __post_init__(self) -> None:
        if self.available_bytes is not None and self.available_bytes < 0:
            raise ValueError("storage bytes cannot be negative")


@dataclass(frozen=True, slots=True)
class RuntimeCapability:
    python_version: str
    runtimes: tuple[str, ...] = (ResourceRuntime.CPU.value,)
    container_runtime: str | None = None


@dataclass(frozen=True, slots=True)
class NodeCapability:
    schema_version: str
    node_id: NodeId
    revision: int
    operating_system: str
    architecture: str
    logical_cpu_cores: int | None
    physical_cpu_cores: int | None
    total_memory_bytes: int | None
    available_memory_bytes: int | None
    gpus: tuple[GpuCapability, ...]
    storage: tuple[StorageCapability, ...]
    runtime: RuntimeCapability
    enabled_roles: tuple[str, ...]
    captured_at: datetime
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("schema_version cannot be empty")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        for value in (
            self.logical_cpu_cores,
            self.physical_cpu_cores,
            self.total_memory_bytes,
            self.available_memory_bytes,
        ):
            if value is not None and value < 0:
                raise ValueError("capability values cannot be negative")


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    schema_version: str
    key: str
    version: str
    payload_type: str
    minimum: ResourceRequest
    recommended: ResourceRequest
    supported_runtimes: tuple[str, ...]
    fallback_batch_sizes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.key or not self.version or not self.payload_type:
            raise ValueError("workload identity fields cannot be empty")
        if any(size < 1 for size in self.fallback_batch_sizes):
            raise ValueError("fallback batch sizes must be positive")


@dataclass(frozen=True, slots=True)
class ResolvedExecutionPlan:
    runtime: str
    device_id: str | None = None
    batch_size: int | None = None
    adjustments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompatibilityAssessment:
    schema_version: str
    status: CompatibilityStatus
    node_id: NodeId
    capability_revision: int
    workload_key: str
    workload_version: str
    reason_codes: tuple[ReasonCode, ...]
    plan: ResolvedExecutionPlan | None
    details: dict[str, int | str | None] = field(default_factory=dict)
