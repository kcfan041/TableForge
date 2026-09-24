from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.domain.capabilities import (
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def capability(revision: int = 1) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId("node-a"),
        revision=revision,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=8,
        physical_cpu_cores=4,
        total_memory_bytes=16 * 1024**3,
        available_memory_bytes=12 * 1024**3,
        gpus=(),
        storage=(StorageCapability("data", 100 * 1024**3),),
        runtime=RuntimeCapability("3.12", ("cpu",)),
        enabled_roles=("simulation",),
        captured_at=NOW,
    )


def repository(tmp_path: Path) -> SQLiteNodeRepository:
    return SQLiteNodeRepository(tmp_path / "tableforge.db")


def test_register_and_heartbeat_node(tmp_path: Path) -> None:
    nodes = repository(tmp_path)
    registered = nodes.register(capability(), NOW)

    heartbeat = nodes.heartbeat(
        registered.node_id,
        registered.capability.revision,
        NOW + timedelta(seconds=30),
        available_memory_bytes=10 * 1024**3,
    )

    assert heartbeat.registered_at == NOW
    assert heartbeat.last_heartbeat_at == NOW + timedelta(seconds=30)
    assert heartbeat.capability.available_memory_bytes == 10 * 1024**3


def test_new_capability_revision_preserves_registration_time(tmp_path: Path) -> None:
    nodes = repository(tmp_path)
    first = nodes.register(capability(), NOW)
    second_capability = replace(capability(2), logical_cpu_cores=16)

    second = nodes.register(second_capability, NOW + timedelta(hours=1))

    assert second.registered_at == first.registered_at
    assert nodes.get(NodeId("node-a")) == second
    assert second.capability.logical_cpu_cores == 16


def test_heartbeat_rejects_wrong_revision(tmp_path: Path) -> None:
    nodes = repository(tmp_path)
    nodes.register(capability(), NOW)

    with pytest.raises(ValueError, match="capability revision"):
        nodes.heartbeat(NodeId("node-a"), 2, NOW + timedelta(seconds=1))
