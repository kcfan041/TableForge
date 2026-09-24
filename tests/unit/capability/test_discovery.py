from __future__ import annotations

import subprocess
from pathlib import Path

from tableforge.capability import discovery
from tableforge.capability.discovery import LocalCapabilityDiscovery
from tableforge.domain.ids import NodeId


def test_local_discovery_reports_cpu_storage_and_runtime(tmp_path: Path) -> None:
    capability = LocalCapabilityDiscovery(
        node_id=NodeId("local"),
        roles=("simulation",),
        storage_path=tmp_path,
        detect_gpus=False,
    ).discover()

    assert capability.logical_cpu_cores is None or capability.logical_cpu_cores > 0
    assert capability.storage[0].available_bytes is not None
    assert capability.storage[0].available_bytes > 0
    assert capability.runtime.runtimes == ("cpu",)
    assert capability.enabled_roles == ("simulation",)
    assert capability.gpus == ()


def test_detected_gpus_add_cuda_runtime(tmp_path: Path, monkeypatch: object) -> None:
    from tableforge.domain.capabilities import GpuCapability

    fake = GpuCapability("cuda:0", "nvidia", "fake", 8 * 1024**3, 7 * 1024**3, runtimes=("cuda",))
    monkeypatch.setattr(  # type: ignore[attr-defined]
        discovery, "discover_gpus", lambda warnings, cuda_runtime=None: (fake,)
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        discovery, "windows_commit_available", lambda: None
    )

    capability = LocalCapabilityDiscovery(NodeId("local"), ("simulation",), tmp_path).discover()

    assert capability.gpus == (fake,)
    assert capability.runtime.runtimes == ("cpu", "cuda")


def test_macos_memory_uses_sysctl(monkeypatch: object) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="17179869184\n", stderr="")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)  # type: ignore[attr-defined]
    warnings: list[str] = []

    total, available = discovery._macos_memory_bytes(warnings)

    assert total == 16 * 1024**3
    assert available is None
    assert warnings == ["AVAILABLE_MEMORY_UNKNOWN"]


def test_macos_memory_failure_is_unknown(monkeypatch: object) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, "sysctl")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)  # type: ignore[attr-defined]
    warnings: list[str] = []

    assert discovery._macos_memory_bytes(warnings) == (None, None)
    assert warnings == ["MEMORY_DISCOVERY_FAILED"]


def test_gpu_memory_is_capped_by_system_commit() -> None:
    from tableforge.domain.capabilities import GpuCapability

    gib = 1024**3
    gpu = GpuCapability("cuda:0", "nvidia", "fake", 8 * gib, 7 * gib, runtimes=("cuda",))
    warnings: list[str] = []

    capped = discovery.cap_gpu_memory_by_commit((gpu,), 4 * gib, warnings)
    roomy = discovery.cap_gpu_memory_by_commit((gpu,), 40 * gib, [])

    assert capped[0].available_memory_bytes == 3 * gib
    assert capped[0].total_memory_bytes == 8 * gib
    assert warnings == ["GPU_MEMORY_LIMITED_BY_SYSTEM_COMMIT"]
    assert roomy[0].available_memory_bytes == 7 * gib
