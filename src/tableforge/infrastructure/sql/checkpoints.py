from __future__ import annotations

from datetime import datetime
from typing import Any

from tableforge.domain.checkpoints import (
    Checkpoint,
    CheckpointStatus,
    CheckpointTransitionError,
)
from tableforge.domain.ids import AgentId, ArtifactId, CheckpointId, ExperimentId
from tableforge.infrastructure.sql.database import Database, parse_timestamp, require_aware
from tableforge.infrastructure.sql.events import record_event


class SqlCheckpointRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def register_candidate(self, checkpoint: Checkpoint) -> Checkpoint:
        if checkpoint.status is not CheckpointStatus.CANDIDATE:
            raise CheckpointTransitionError("new checkpoints must start as candidates")
        for _ in range(2):
            try:
                with self._db.transaction() as connection:
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
                            self._db.timestamp(checkpoint.created_at),
                            self._db.timestamp(checkpoint.updated_at),
                        ),
                    )
                return checkpoint
            except self._db.integrity_errors:
                continue
        raise CheckpointTransitionError("checkpoint registration kept conflicting")

    def decide(
        self, checkpoint_id: CheckpointId, promote: bool, reason: str, now: datetime
    ) -> Checkpoint:
        require_aware(now)
        with self._db.transaction() as connection:
            target_row = connection.execute(
                "SELECT agent_id FROM checkpoints WHERE checkpoint_id = ?",
                (str(checkpoint_id),),
            ).fetchone()
            if target_row is None:
                raise CheckpointTransitionError(f"unknown checkpoint {checkpoint_id}")
            connection.execute(
                "SELECT checkpoint_id FROM checkpoints WHERE agent_id = ? ORDER BY checkpoint_id"
                f"{self._db.lock_for_update}",
                (target_row["agent_id"],),
            ).fetchall()
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (str(checkpoint_id),)
            ).fetchone()
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
                        self._db.timestamp(now),
                        row["agent_id"],
                        CheckpointStatus.ACTIVE.value,
                    ),
                )
            connection.execute(
                """
                UPDATE checkpoints SET status = ?, decision_reason = ?, updated_at = ?
                WHERE checkpoint_id = ?
                """,
                (target.value, reason, self._db.timestamp(now), str(checkpoint_id)),
            )
            record_event(
                self._db,
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
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (str(checkpoint_id),)
            ).fetchone()
        return _row(row) if row is not None else None

    def active(self, agent_id: AgentId) -> Checkpoint | None:
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE agent_id = ? AND status = ?",
                (str(agent_id), CheckpointStatus.ACTIVE.value),
            ).fetchone()
        return _row(row) if row is not None else None

    def list_checkpoints(self, agent_id: AgentId | None = None) -> tuple[Checkpoint, ...]:
        with self._db.read() as connection:
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


def _row(row: Any) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=CheckpointId(row["checkpoint_id"]),
        agent_id=AgentId(row["agent_id"]),
        experiment_id=ExperimentId(row["experiment_id"]),
        round_index=int(row["round_index"]),
        parent_id=CheckpointId(row["parent_id"]) if row["parent_id"] else None,
        artifact_id=ArtifactId(row["artifact_id"]),
        sha256=row["sha256"],
        status=CheckpointStatus(row["status"]),
        decision_reason=row["decision_reason"],
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )
