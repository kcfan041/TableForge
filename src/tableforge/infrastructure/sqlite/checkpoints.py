from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from tableforge.domain.checkpoints import (
    Checkpoint,
    CheckpointStatus,
    CheckpointTransitionError,
)
from tableforge.domain.ids import AgentId, ArtifactId, CheckpointId, ExperimentId
from tableforge.infrastructure.sqlite.events import ensure_events_table, record_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    parent_id TEXT,
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (agent_id, round_index)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_checkpoint_per_agent
ON checkpoints(agent_id) WHERE status = 'active';
"""


class SQLiteCheckpointRepository:
    def __init__(self, database: Path) -> None:
        self._database = database
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            ensure_events_table(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def register_candidate(self, checkpoint: Checkpoint) -> Checkpoint:
        if checkpoint.status is not CheckpointStatus.CANDIDATE:
            raise CheckpointTransitionError("new checkpoints must start as candidates")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM checkpoints WHERE agent_id = ? AND round_index = ?",
                (str(checkpoint.agent_id), checkpoint.round_index),
            ).fetchone()
            if existing is not None:
                if existing["sha256"] != checkpoint.sha256:
                    raise CheckpointTransitionError(
                        "a different checkpoint already exists for this round"
                    )
                return _row(existing)
            connection.execute(
                """
                INSERT INTO checkpoints (
                    checkpoint_id, agent_id, experiment_id, round_index, parent_id,
                    artifact_id, sha256, status, decision_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(checkpoint.checkpoint_id),
                    str(checkpoint.agent_id),
                    str(checkpoint.experiment_id),
                    checkpoint.round_index,
                    str(checkpoint.parent_id) if checkpoint.parent_id else None,
                    str(checkpoint.artifact_id),
                    checkpoint.sha256,
                    checkpoint.status.value,
                    checkpoint.decision_reason,
                    checkpoint.created_at.isoformat(),
                    checkpoint.updated_at.isoformat(),
                ),
            )
        return checkpoint

    def decide(
        self,
        checkpoint_id: CheckpointId,
        promote: bool,
        reason: str,
        now: datetime,
    ) -> Checkpoint:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (str(checkpoint_id),)
            ).fetchone()
            if row is None:
                raise CheckpointTransitionError(f"unknown checkpoint {checkpoint_id}")
            current = CheckpointStatus(row["status"])
            target = CheckpointStatus.ACTIVE if promote else CheckpointStatus.REJECTED
            if current is target:
                return _row(row)
            if current is not CheckpointStatus.CANDIDATE:
                raise CheckpointTransitionError(
                    f"cannot move checkpoint from {current.value} to {target.value}"
                )
            if promote:
                connection.execute(
                    """
                    UPDATE checkpoints SET status = ?, updated_at = ?
                    WHERE agent_id = ? AND status = ?
                    """,
                    (
                        CheckpointStatus.RETIRED.value,
                        now.isoformat(),
                        row["agent_id"],
                        CheckpointStatus.ACTIVE.value,
                    ),
                )
            connection.execute(
                """
                UPDATE checkpoints SET status = ?, decision_reason = ?, updated_at = ?
                WHERE checkpoint_id = ?
                """,
                (target.value, reason, now.isoformat(), str(checkpoint_id)),
            )
            record_event(
                connection,
                "CHECKPOINT_PROMOTED" if promote else "CHECKPOINT_REJECTED",
                "checkpoint",
                str(checkpoint_id),
                int(row["round_index"]),
                {
                    "agent_id": row["agent_id"],
                    "experiment_id": row["experiment_id"],
                    "reason": reason,
                    "sha256": row["sha256"],
                },
                now,
            )
            updated = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (str(checkpoint_id),)
            ).fetchone()
        return _row(updated)

    def get(self, checkpoint_id: CheckpointId) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (str(checkpoint_id),)
            ).fetchone()
        return _row(row) if row is not None else None

    def active(self, agent_id: AgentId) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE agent_id = ? AND status = ?",
                (str(agent_id), CheckpointStatus.ACTIVE.value),
            ).fetchone()
        return _row(row) if row is not None else None

    def list_checkpoints(self, agent_id: AgentId | None = None) -> tuple[Checkpoint, ...]:
        with self._connect() as connection:
            if agent_id is None:
                rows = connection.execute(
                    "SELECT * FROM checkpoints ORDER BY agent_id, round_index"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM checkpoints WHERE agent_id = ? ORDER BY round_index",
                    (str(agent_id),),
                ).fetchall()
        return tuple(_row(row) for row in rows)


def _row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=CheckpointId(row["checkpoint_id"]),
        agent_id=AgentId(row["agent_id"]),
        experiment_id=ExperimentId(row["experiment_id"]),
        round_index=row["round_index"],
        parent_id=CheckpointId(row["parent_id"]) if row["parent_id"] else None,
        artifact_id=ArtifactId(row["artifact_id"]),
        sha256=row["sha256"],
        status=CheckpointStatus(row["status"]),
        decision_reason=row["decision_reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
