from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tableforge.infrastructure.sql.database import Database, parse_timestamp

TABLES: tuple[str, ...] = (
    "experiments",
    "experiment_revisions",
    "jobs",
    "job_attempts",
    "job_outputs",
    "nodes",
    "node_capability_snapshots",
    "checkpoints",
    "enrollment_tokens",
    "enrollment_token_uses",
    "node_sessions",
    "events",
    "cluster_identity",
    "invites",
    "operators",
    "worker_slots",
)

TIMESTAMP_COLUMNS: dict[str, frozenset[str]] = {
    "experiments": frozenset({"created_at", "updated_at"}),
    "experiment_revisions": frozenset({"created_at"}),
    "jobs": frozenset({"not_before", "created_at"}),
    "job_attempts": frozenset({"leased_at", "expires_at", "completed_at", "failed_at"}),
    "job_outputs": frozenset({"created_at"}),
    "nodes": frozenset({"registered_at", "last_heartbeat_at"}),
    "node_capability_snapshots": frozenset({"captured_at"}),
    "checkpoints": frozenset({"created_at", "updated_at"}),
    "enrollment_tokens": frozenset({"created_at", "expires_at", "used_at"}),
    "enrollment_token_uses": frozenset({"used_at"}),
    "node_sessions": frozenset({"created_at", "expires_at"}),
    "events": frozenset({"occurred_at", "published_at"}),
    "cluster_identity": frozenset({"created_at"}),
    "invites": frozenset({"created_at", "expires_at", "redeemed_at"}),
    "operators": frozenset({"created_at", "expires_at"}),
    "worker_slots": frozenset({"started_at", "last_seen_at"}),
}

ORDER_BY: dict[str, str] = {
    "experiments": "experiment_id",
    "experiment_revisions": "experiment_id, revision",
    "jobs": "job_id",
    "job_attempts": "job_id, attempt_number",
    "job_outputs": "job_id, name",
    "nodes": "node_id",
    "node_capability_snapshots": "node_id, revision",
    "checkpoints": "checkpoint_id",
    "enrollment_tokens": "token_hash",
    "enrollment_token_uses": "token_hash, node_id",
    "node_sessions": "token_hash",
    "events": "sequence",
    "cluster_identity": "singleton",
    "invites": "invite_id",
    "operators": "operator_id",
    "worker_slots": "worker_id",
}

BATCH = 500


class TargetNotEmptyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CopyReport:
    rows: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def copy_metadata(
    source: Database,
    target: Database,
    progress: Callable[[str, int], None] | None = None,
) -> CopyReport:
    """Copy every metadata table from ``source`` into an empty ``target``.

    The whole copy runs in one target transaction, so a failure leaves the target empty.
    IDs, revisions, attempt history, and event sequence numbers are preserved.
    """
    counts: dict[str, int] = {}
    with target.transaction() as destination:
        for table in TABLES:
            existing = destination.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            if int(existing["count"]):
                raise TargetNotEmptyError(
                    f"target table '{table}' already has rows; refusing to merge metadata"
                )
        with source.read() as origin:
            for table in TABLES:
                columns = _columns(origin, source, table)
                placeholders = ", ".join("?" for _ in columns)
                insert = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                timestamps = TIMESTAMP_COLUMNS[table] & set(columns)
                copied = 0
                offset = 0
                while True:
                    rows = origin.execute(
                        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {ORDER_BY[table]}"
                        " LIMIT ? OFFSET ?",
                        (BATCH, offset),
                    ).fetchall()
                    if not rows:
                        break
                    for row in rows:
                        destination.execute(
                            insert,
                            [
                                _convert(target, row[column], column in timestamps)
                                for column in columns
                            ],
                        )
                    copied += len(rows)
                    offset += len(rows)
                    if progress is not None:
                        progress(table, copied)
                counts[table] = copied
        if target.name == "postgres":
            destination.execute(
                "SELECT setval(pg_get_serial_sequence('events', 'sequence'),"
                " COALESCE((SELECT MAX(sequence) FROM events), 0) + 1, false)"
            )
    return CopyReport(counts)


def _columns(connection: Any, database: Database, table: str) -> list[str]:
    if database.name == "sqlite":
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return [row["name"] for row in rows]
    rows = connection.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = ?
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [row["column_name"] for row in rows]


def _convert(target: Database, value: object, is_timestamp: bool) -> object:
    if value is None or not is_timestamp:
        return value
    return target.timestamp(parse_timestamp(value))
