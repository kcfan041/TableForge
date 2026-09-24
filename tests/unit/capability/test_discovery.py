from __future__ import annotations

from pathlib import Path

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
