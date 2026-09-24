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
    ).discover()

    assert capability.logical_cpu_cores is None or capability.logical_cpu_cores > 0
    assert capability.storage[0].available_bytes is not None
    assert capability.storage[0].available_bytes > 0
    assert "cpu" in capability.runtime.runtimes
    assert capability.enabled_roles == ("simulation",)
    assert capability.gpus == ()


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
