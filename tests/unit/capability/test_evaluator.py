from __future__ import annotations

from datetime import UTC, datetime

from tableforge.capability.evaluator import assess_compatibility
from tableforge.domain.capabilities import (
    CompatibilityStatus,
    GpuCapability,
    NodeCapability,
    ReasonCode,
    RuntimeCapability,
    StorageCapability,
    WorkloadProfile,
)
from tableforge.domain.ids import NodeId
from tableforge.domain.jobs import ResourceRequest


def capability(
    *,
    cpu: int | None = 8,
    memory: int | None = 32 * 1024**3,
    roles: tuple[str, ...] = ("simulation",),
    gpus: tuple[GpuCapability, ...] = (),
    runtimes: tuple[str, ...] = ("cpu",),
) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId("node-test"),
        revision=1,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=cpu,
        physical_cpu_cores=None,
        total_memory_bytes=memory,
        available_memory_bytes=memory,
        gpus=gpus,
        storage=(StorageCapability("data", 100 * 1024**3),),
        runtime=RuntimeCapability("3.12", runtimes),
        enabled_roles=roles,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def workload(request: ResourceRequest) -> WorkloadProfile:
    return WorkloadProfile(
        schema_version="1.0",
        key="test-workload",
        version="1",
        payload_type="tableforge.v1.TestJob",
        minimum=request,
        recommended=request,
        supported_runtimes=(request.required_runtime,),
    )


def test_cpu_workload_is_supported_at_boundary() -> None:
    assessment = assess_compatibility(
        capability(cpu=4, memory=8 * 1024**3),
        workload(
            ResourceRequest(
                cpu_cores=4,
                memory_bytes=8 * 1024**3,
                storage_bytes=10 * 1024**3,
                required_role="simulation",
            )
        ),
    )

    assert assessment.status is CompatibilityStatus.SUPPORTED
    assert assessment.plan is not None
    assert assessment.plan.runtime == "cpu"


def test_unknown_required_memory_is_not_supported() -> None:
    assessment = assess_compatibility(
        capability(memory=None),
        workload(ResourceRequest(memory_bytes=1, required_role="simulation")),
    )

    assert assessment.status is CompatibilityStatus.UNKNOWN
    assert ReasonCode.CAPABILITY_UNKNOWN in assessment.reason_codes


def test_missing_role_is_unsupported() -> None:
    assessment = assess_compatibility(
        capability(roles=("evaluation",)),
        workload(ResourceRequest(required_role="simulation")),
    )

    assert assessment.status is CompatibilityStatus.UNSUPPORTED
    assert assessment.reason_codes == (ReasonCode.ROLE_DISABLED,)


def test_gpu_memory_requirement_is_enforced() -> None:
    gpu = GpuCapability(
        device_id="cuda:0",
        vendor="nvidia",
        model="test",
        total_memory_bytes=8 * 1024**3,
        available_memory_bytes=8 * 1024**3,
        runtimes=("cuda",),
    )
    assessment = assess_compatibility(
        capability(
            roles=("nfsp_learner",),
            gpus=(gpu,),
            runtimes=("cpu", "cuda"),
        ),
        workload(
            ResourceRequest(
                gpu_count=1,
                gpu_memory_bytes=16 * 1024**3,
                required_role="nfsp_learner",
                required_runtime="cuda",
            )
        ),
    )

    assert assessment.status is CompatibilityStatus.UNSUPPORTED
    assert ReasonCode.INSUFFICIENT_VRAM in assessment.reason_codes


def test_compatible_gpu_is_selected() -> None:
    gpu = GpuCapability(
        device_id="cuda:0",
        vendor="nvidia",
        model="test",
        total_memory_bytes=24 * 1024**3,
        available_memory_bytes=20 * 1024**3,
        runtimes=("cuda",),
    )
    assessment = assess_compatibility(
        capability(
            roles=("nfsp_learner",),
            gpus=(gpu,),
            runtimes=("cpu", "cuda"),
        ),
        workload(
            ResourceRequest(
                gpu_count=1,
                gpu_memory_bytes=16 * 1024**3,
                required_role="nfsp_learner",
                required_runtime="cuda",
            )
        ),
    )

    assert assessment.status is CompatibilityStatus.SUPPORTED
    assert assessment.plan is not None
    assert assessment.plan.device_id == "cuda:0"
