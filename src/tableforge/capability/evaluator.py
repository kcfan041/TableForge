from __future__ import annotations

from tableforge.domain.capabilities import (
    CompatibilityAssessment,
    CompatibilityStatus,
    GpuCapability,
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
    batch_size: int | None = None
    degraded = False
    if required.gpu_count > 0:
        runtime_gpus = [
            gpu for gpu in capability.gpus if required.required_runtime in gpu.runtimes
        ]
        if len(runtime_gpus) < required.gpu_count:
            reasons.append(ReasonCode.GPU_NOT_FOUND)
        elif any(gpu.total_memory_bytes is None for gpu in runtime_gpus):
            reasons.append(ReasonCode.CAPABILITY_UNKNOWN)
        else:
            best = max(runtime_gpus, key=_usable_vram)
            vram = _usable_vram(best)
            details["available_gpu_memory_bytes"] = vram
            details["required_gpu_memory_bytes"] = required.gpu_memory_bytes
            if workload.gpu_memory_per_batch_item:
                batch_size, degraded = _choose_batch(workload, vram)
                fits = batch_size is not None
            else:
                fits = vram >= required.gpu_memory_bytes
            if not fits:
                reasons.append(ReasonCode.INSUFFICIENT_VRAM)
            else:
                selected_device = best.device_id
                if degraded:
                    details["batch_size"] = batch_size

    if ReasonCode.CAPABILITY_UNKNOWN in reasons:
        status = CompatibilityStatus.UNKNOWN
    elif reasons:
        status = CompatibilityStatus.UNSUPPORTED
    elif degraded:
        status = CompatibilityStatus.DEGRADED
        reasons.append(ReasonCode.REDUCED_BATCH_SIZE)
    else:
        status = CompatibilityStatus.SUPPORTED
    plan = None
    if status in (CompatibilityStatus.SUPPORTED, CompatibilityStatus.DEGRADED):
        plan = ResolvedExecutionPlan(
            runtime=required.required_runtime,
            device_id=selected_device,
            batch_size=batch_size,
            adjustments=(f"batch_size={batch_size}",) if degraded else (),
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


def _usable_vram(gpu: GpuCapability) -> int:
    total = gpu.total_memory_bytes or 0
    if gpu.available_memory_bytes is None:
        return total
    return min(total, gpu.available_memory_bytes)


def _choose_batch(workload: WorkloadProfile, vram: int) -> tuple[int | None, bool]:
    sizes = workload.fallback_batch_sizes
    if not sizes:
        return None, False
    for index, size in enumerate(sizes):
        if workload.gpu_memory_for(size) <= vram:
            return size, index > 0
    return None, False


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
