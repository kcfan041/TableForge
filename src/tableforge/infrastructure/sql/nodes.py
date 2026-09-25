from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from tableforge.domain.capabilities import (
    GpuCapability,
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId
from tableforge.domain.nodes import RegisteredNode
from tableforge.infrastructure.sql.database import Database, parse_timestamp, require_aware

_SELECT = """
SELECT n.*, s.capability_json
FROM nodes n
JOIN node_capability_snapshots s
  ON s.node_id = n.node_id
 AND s.revision = n.current_capability_revision
"""


class SqlNodeRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def register(self, capability: NodeCapability, now: datetime) -> RegisteredNode:
        require_aware(now)
        with self._db.transaction() as connection:
            existing = connection.execute(
                f"SELECT registered_at FROM nodes WHERE node_id = ?{self._db.lock_for_update}",
                (str(capability.node_id),),
            ).fetchone()
            registered_at = parse_timestamp(existing["registered_at"]) if existing else now
            connection.execute(
                """
                INSERT INTO nodes (
                    node_id, current_capability_revision, registered_at,
                    last_heartbeat_at, available_memory_bytes
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    current_capability_revision = excluded.current_capability_revision,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    available_memory_bytes = excluded.available_memory_bytes
                """,
                (
                    str(capability.node_id),
                    capability.revision,
                    self._db.timestamp(registered_at),
                    self._db.timestamp(now),
                    capability.available_memory_bytes,
                ),
            )
            connection.execute(
                """
                INSERT INTO node_capability_snapshots (
                    node_id, revision, capability_json, captured_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id, revision) DO UPDATE SET
                    capability_json = excluded.capability_json,
                    captured_at = excluded.captured_at
                """,
                (
                    str(capability.node_id),
                    capability.revision,
                    _capability_json(capability),
                    self._db.timestamp(capability.captured_at),
                ),
            )
        return RegisteredNode(capability.node_id, capability, registered_at, now)

    def heartbeat(
        self,
        node_id: NodeId,
        capability_revision: int,
        now: datetime,
        available_memory_bytes: int | None = None,
    ) -> RegisteredNode:
        require_aware(now)
        with self._db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE nodes SET last_heartbeat_at = ?, available_memory_bytes = ?
                WHERE node_id = ? AND current_capability_revision = ?
                """,
                (
                    self._db.timestamp(now),
                    available_memory_bytes,
                    str(node_id),
                    capability_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("node is not registered with this capability revision")
            row = connection.execute(f"{_SELECT} WHERE n.node_id = ?", (str(node_id),)).fetchone()
        return _row_to_node(row)

    def get(self, node_id: NodeId) -> RegisteredNode | None:
        with self._db.read() as connection:
            row = connection.execute(f"{_SELECT} WHERE n.node_id = ?", (str(node_id),)).fetchone()
        return _row_to_node(row) if row is not None else None

    def list_nodes(self) -> tuple[RegisteredNode, ...]:
        with self._db.read() as connection:
            rows = connection.execute(f"{_SELECT} ORDER BY n.node_id").fetchall()
        return tuple(_row_to_node(row) for row in rows)


def _capability_json(capability: NodeCapability) -> str:
    data = asdict(capability)
    data["node_id"] = str(capability.node_id)
    data["captured_at"] = capability.captured_at.isoformat()
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _row_to_node(row: Any) -> RegisteredNode:
    data = json.loads(row["capability_json"])
    capability = NodeCapability(
        schema_version=data["schema_version"],
        node_id=NodeId(data["node_id"]),
        revision=data["revision"],
        operating_system=data["operating_system"],
        architecture=data["architecture"],
        logical_cpu_cores=data["logical_cpu_cores"],
        physical_cpu_cores=data["physical_cpu_cores"],
        total_memory_bytes=data["total_memory_bytes"],
        available_memory_bytes=row["available_memory_bytes"],
        gpus=tuple(
            GpuCapability(
                device_id=gpu["device_id"],
                vendor=gpu["vendor"],
                model=gpu["model"],
                total_memory_bytes=gpu["total_memory_bytes"],
                available_memory_bytes=gpu["available_memory_bytes"],
                driver_version=gpu["driver_version"],
                compute_capability=gpu["compute_capability"],
                runtimes=tuple(gpu["runtimes"]),
            )
            for gpu in data["gpus"]
        ),
        storage=tuple(StorageCapability(**storage) for storage in data["storage"]),
        runtime=RuntimeCapability(
            python_version=data["runtime"]["python_version"],
            runtimes=tuple(data["runtime"]["runtimes"]),
            container_runtime=data["runtime"]["container_runtime"],
        ),
        enabled_roles=tuple(data["enabled_roles"]),
        captured_at=datetime.fromisoformat(data["captured_at"]),
        warnings=tuple(data["warnings"]),
    )
    return RegisteredNode(
        node_id=NodeId(row["node_id"]),
        capability=capability,
        registered_at=parse_timestamp(row["registered_at"]),
        last_heartbeat_at=parse_timestamp(row["last_heartbeat_at"]),
    )
