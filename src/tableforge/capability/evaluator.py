from __future__ import annotations

from tableforge.domain.capabilities import (
    CompatibilityAssessment,
    CompatibilityStatus,
    NodeCapability,
    ReasonCode,
    ResolvedExecutionPlan,
    WorkloadProfile,
)


def assess_compatibility(
    capability: NodeCapability,
    workload: WorkloadProfile,
) -> CompatibilityAssessment:
    required = workload.minimum
    reasons: list[ReasonCode] = []
    details: dict[str, int | str | None] = {}

    _check_known_minimum(
        "cpu_cores",
        required.cpu_cores,
        capability.logical_cpu_cores,
        ReasonCode.INSUFFICIENT_CPU,
        reasons,
        details,
    )
    _check_known_minimum(
        "memory_bytes",
        required.memory_bytes,
        capability.total_memory_bytes,
        ReasonCode.INSUFFICIENT_SYSTEM_MEMORY,
        reasons,
        details,
    )
    available_storage = max(
        (
            storage.available_bytes
            for storage in capability.storage
            if storage.available_bytes is not None
        ),
        default=None,
    )
    _check_known_minimum(
        "storage_bytes",
        required.storage_bytes,
        available_storage,
        ReasonCode.INSUFFICIENT_DISK,
        reasons,
        details,
    )
    if required.required_role and required.required_role not in capability.enabled_roles:
        reasons.append(ReasonCode.ROLE_DISABLED)
        details["required_role"] = required.required_role
    if required.required_runtime not in capability.runtime.runtimes:
        reasons.append(ReasonCode.RUNTIME_VERSION_UNSUPPORTED)
        details["required_runtime"] = required.required_runtime

    selected_device: str | None = None
    if required.gpu_count > 0:
        if len(capability.gpus) < required.gpu_count:
            reasons.append(ReasonCode.GPU_NOT_FOUND)
        else:
            compatible_gpus = [
                gpu
                for gpu in capability.gpus
                if gpu.total_memory_bytes is not None
                and gpu.total_memory_bytes >= required.gpu_memory_bytes
                and required.required_runtime in gpu.runtimes
            ]
            unknown_memory = any(gpu.total_memory_bytes is None for gpu in capability.gpus)
            if len(compatible_gpus) < required.gpu_count:
                reasons.append(
                    ReasonCode.CAPABILITY_UNKNOWN
                    if unknown_memory
                    else ReasonCode.INSUFFICIENT_VRAM
                )
                details["required_gpu_memory_bytes"] = required.gpu_memory_bytes
            else:
                selected_device = compatible_gpus[0].device_id

    if ReasonCode.CAPABILITY_UNKNOWN in reasons:
        status = CompatibilityStatus.UNKNOWN
    elif reasons:
        status = CompatibilityStatus.UNSUPPORTED
    else:
        status = CompatibilityStatus.SUPPORTED
    plan = None
    if status is CompatibilityStatus.SUPPORTED:
        plan = ResolvedExecutionPlan(
            runtime=required.required_runtime,
            device_id=selected_device,
        )
    return CompatibilityAssessment(
        schema_version="1.0",
        status=status,
        node_id=capability.node_id,
        capability_revision=capability.revision,
        workload_key=workload.key,
        workload_version=workload.version,
        reason_codes=tuple(dict.fromkeys(reasons)),
        plan=plan,
        details=details,
    )


def _check_known_minimum(
    name: str,
    required: int,
    available: int | None,
    insufficient_reason: ReasonCode,
    reasons: list[ReasonCode],
    details: dict[str, int | str | None],
) -> None:
    if required == 0:
        return
    details[f"required_{name}"] = required
    details[f"available_{name}"] = available
    if available is None:
        reasons.append(ReasonCode.CAPABILITY_UNKNOWN)
    elif available < required:
        reasons.append(insufficient_reason)
