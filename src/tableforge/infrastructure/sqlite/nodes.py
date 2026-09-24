from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from tableforge.domain.capabilities import (
    GpuCapability,
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId
from tableforge.domain.nodes import RegisteredNode

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    current_capability_revision INTEGER NOT NULL,
    registered_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    available_memory_bytes INTEGER
);
CREATE TABLE IF NOT EXISTS node_capability_snapshots (
    node_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    capability_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (node_id, revision),
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);
"""


class SQLiteNodeRepository:
    def __init__(self, database: Path) -> None:
        self._database = database
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def register(self, capability: NodeCapability, now: datetime) -> RegisteredNode:
        _require_aware(now)
        payload = _capability_json(capability)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM nodes WHERE node_id = ?", (str(capability.node_id),)
            ).fetchone()
            registered_at = (
                datetime.fromisoformat(existing["registered_at"]) if existing else now
            )
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
                    registered_at.isoformat(),
                    now.isoformat(),
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
                    payload,
                    capability.captured_at.isoformat(),
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
        _require_aware(now)
        with self._connect() as connection:
            node = connection.execute(
                "SELECT * FROM nodes WHERE node_id = ?", (str(node_id),)
            ).fetchone()
            if node is None or node["current_capability_revision"] != capability_revision:
                raise ValueError("node is not registered with this capability revision")
            connection.execute(
                """
                UPDATE nodes SET last_heartbeat_at = ?, available_memory_bytes = ?
                WHERE node_id = ?
                """,
                (now.isoformat(), available_memory_bytes, str(node_id)),
            )
        registered = self.get(node_id)
        if registered is None:
            raise AssertionError("registered node disappeared")
        return RegisteredNode(
            registered.node_id,
            registered.capability,
            registered.registered_at,
            now,
        )

    def get(self, node_id: NodeId) -> RegisteredNode | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT n.*, s.capability_json
                FROM nodes n
                JOIN node_capability_snapshots s
                  ON s.node_id = n.node_id
                 AND s.revision = n.current_capability_revision
                WHERE n.node_id = ?
                """,
                (str(node_id),),
            ).fetchone()
        return _row_to_node(row) if row is not None else None

    def list_nodes(self) -> tuple[RegisteredNode, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT n.*, s.capability_json
                FROM nodes n
                JOIN node_capability_snapshots s
                  ON s.node_id = n.node_id
                 AND s.revision = n.current_capability_revision
                ORDER BY n.node_id
                """
            ).fetchall()
        return tuple(_row_to_node(row) for row in rows)


def _capability_json(capability: NodeCapability) -> str:
    data = asdict(capability)
    data["node_id"] = str(capability.node_id)
    data["captured_at"] = capability.captured_at.isoformat()
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _row_to_node(row: sqlite3.Row) -> RegisteredNode:
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
        registered_at=datetime.fromisoformat(row["registered_at"]),
        last_heartbeat_at=datetime.fromisoformat(row["last_heartbeat_at"]),
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
