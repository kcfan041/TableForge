from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

from tableforge.capability.evaluator import assess_compatibility
from tableforge.capability.gpu import discover_gpus
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.domain.capabilities import (
    CompatibilityStatus,
    GpuCapability,
    NodeCapability,
    ReasonCode,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId

GIB = 1024**3
SMALL_GPU = next(w for w in BUILTIN_WORKLOADS if w.key == "hu-nfsp-small-gpu")


class FakeNvml:
    def __init__(self, devices: list[tuple[str, int, int, tuple[int, int]]]) -> None:
        self.devices = devices
        self.shutdown_called = False

    def nvmlInit(self) -> None:  # noqa: N802
        pass

    def nvmlShutdown(self) -> None:  # noqa: N802
        self.shutdown_called = True

    def nvmlSystemGetDriverVersion(self) -> bytes:  # noqa: N802
        return b"610.88"

    def nvmlDeviceGetCount(self) -> int:  # noqa: N802
        return len(self.devices)

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:  # noqa: N802
        return index

    def nvmlDeviceGetName(self, handle: int) -> str:  # noqa: N802
        return self.devices[handle][0]

    def nvmlDeviceGetMemoryInfo(self, handle: int) -> SimpleNamespace:  # noqa: N802
        _, total, free, _ = self.devices[handle]
        return SimpleNamespace(total=total, free=free)

    def nvmlDeviceGetCudaComputeCapability(self, handle: int) -> tuple[int, int]:  # noqa: N802
        return self.devices[handle][3]


def test_nvml_discovery_reports_each_device() -> None:
    fake = FakeNvml(
        [("RTX 2070", 8 * GIB, 7 * GIB, (7, 5)), ("RTX 4090", 24 * GIB, 23 * GIB, (8, 9))]
    )
    warnings: list[str] = []

    gpus = discover_gpus(warnings, nvml=fake, cuda_runtime=True)

    assert [gpu.device_id for gpu in gpus] == ["cuda:0", "cuda:1"]
    assert gpus[0].model == "RTX 2070"
    assert gpus[0].compute_capability == "7.5"
    assert gpus[1].total_memory_bytes == 24 * GIB
    assert gpus[0].runtimes == ("cuda",)
    assert gpus[0].driver_version == "610.88"
    assert fake.shutdown_called
    assert warnings == []


def test_smi_fallback_when_nvml_fails() -> None:
    class Broken:
        def nvmlInit(self) -> None:  # noqa: N802
            raise RuntimeError("no nvml")

    warnings: list[str] = []

    gpus = discover_gpus(
        warnings,
        nvml=Broken(),
        run_command=lambda _: "0, NVIDIA GeForce RTX 2070, 8192, 7160, 610.88, 7.5\n",
        cuda_runtime=False,
    )

    assert warnings == ["GPU_NVML_FAILED"]
    assert gpus[0].total_memory_bytes == 8 * GIB
    assert gpus[0].runtimes == ()


def test_no_tools_and_no_cuda_reports_nothing() -> None:
    warnings: list[str] = []

    def failing(arguments: list[str]) -> str:
        raise subprocess.CalledProcessError(1, arguments)

    assert discover_gpus(warnings, nvml=None, run_command=failing, cuda_runtime=False) == ()
    assert warnings == ["GPU_SMI_FAILED"]


def node(gpus: tuple[GpuCapability, ...], runtimes: tuple[str, ...]) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId("gpu-node"),
        revision=1,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=16,
        physical_cpu_cores=8,
        total_memory_bytes=64 * GIB,
        available_memory_bytes=48 * GIB,
        gpus=gpus,
        storage=(StorageCapability("data", 500 * GIB),),
        runtime=RuntimeCapability("3.12", runtimes),
        enabled_roles=("nfsp_learner",),
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def gpu(total_gib: float, free_gib: float | None = None) -> GpuCapability:
    return GpuCapability(
        device_id="cuda:0",
        vendor="nvidia",
        model="test",
        total_memory_bytes=int(total_gib * GIB),
        available_memory_bytes=None if free_gib is None else int(free_gib * GIB),
        runtimes=("cuda",),
    )


def test_large_gpu_runs_at_full_batch() -> None:
    result = assess_compatibility(node((gpu(24, 23),), ("cpu", "cuda")), SMALL_GPU)

    assert result.status is CompatibilityStatus.SUPPORTED
    assert result.plan is not None
    assert result.plan.batch_size == SMALL_GPU.fallback_batch_sizes[0]
    assert result.plan.adjustments == ()


def test_idle_16gb_gpu_runs_at_full_batch() -> None:
    result = assess_compatibility(node((gpu(16, 15.2),), ("cpu", "cuda")), SMALL_GPU)

    assert result.status is CompatibilityStatus.SUPPORTED
    assert result.plan is not None
    assert result.plan.batch_size == SMALL_GPU.fallback_batch_sizes[0]


def test_16gb_gpu_shared_with_another_program_is_degraded() -> None:
    result = assess_compatibility(node((gpu(16, 6.4),), ("cpu", "cuda")), SMALL_GPU)

    assert result.status is CompatibilityStatus.DEGRADED
    assert result.plan is not None
    assert result.plan.batch_size == 131_072


def test_8gb_gpu_is_degraded_to_smaller_batch() -> None:
    result = assess_compatibility(node((gpu(8, 6.0),), ("cpu", "cuda")), SMALL_GPU)

    assert result.status is CompatibilityStatus.DEGRADED
    assert ReasonCode.REDUCED_BATCH_SIZE in result.reason_codes
    assert result.plan is not None
    assert result.plan.device_id == "cuda:0"
    assert result.plan.batch_size == 131_072
    assert SMALL_GPU.gpu_memory_for(result.plan.batch_size) <= int(6.0 * GIB)


def test_busy_gpu_uses_free_memory_not_total() -> None:
    result = assess_compatibility(node((gpu(8, 1.5),), ("cpu", "cuda")), SMALL_GPU)

    assert result.status is CompatibilityStatus.DEGRADED
    assert result.plan is not None
    assert result.plan.batch_size == 16_384


def test_tiny_gpu_is_unsupported() -> None:
    result = assess_compatibility(node((gpu(2, 0.5),), ("cpu", "cuda")), SMALL_GPU)

    assert result.status is CompatibilityStatus.UNSUPPORTED
    assert ReasonCode.INSUFFICIENT_VRAM in result.reason_codes
    assert result.plan is None


def test_gpu_without_cuda_runtime_is_not_found() -> None:
    cpu_only = GpuCapability("cuda:0", "nvidia", "test", 24 * GIB, 23 * GIB, runtimes=())

    result = assess_compatibility(node((cpu_only,), ("cpu",)), SMALL_GPU)

    assert result.status is CompatibilityStatus.UNSUPPORTED
    assert ReasonCode.GPU_NOT_FOUND in result.reason_codes
