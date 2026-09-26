from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tableforge.domain.ids import new_event_id
from tableforge.infrastructure.sql.database import (
    Connection,
    Database,
    optional_timestamp,
    parse_timestamp,
)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    sequence: int
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload: dict[str, object]
    occurred_at: datetime
    published_at: datetime | None


def record_event(
    database: Database,
    connection: Connection,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    payload: dict[str, object],
    occurred_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO events (
            event_id, event_type, aggregate_type, aggregate_id,
            aggregate_version, payload_json, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(new_event_id()),
            event_type,
            aggregate_type,
            aggregate_id,
            aggregate_version,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            database.timestamp(occurred_at),
        ),
    )


class SqlEventLog:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_events(self, after: int = 0, limit: int = 100) -> tuple[DomainEvent, ...]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (after, limit),
            ).fetchall()
        return tuple(_row(row) for row in rows)

    def recent_events(self, limit: int = 100) -> tuple[DomainEvent, ...]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY sequence DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(_row(row) for row in rows)

    def latest_sequence(self) -> int:
        with self._database.read() as connection:
            row = connection.execute("SELECT MAX(sequence) AS latest FROM events").fetchone()
        return int(row["latest"] or 0)

    def mark_published(self, up_to: int, now: datetime) -> int:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE events SET published_at = ? WHERE sequence <= ? AND published_at IS NULL",
                (self._database.timestamp(now), up_to),
            )
            return cursor.rowcount

    def unpublished_count(self) -> int:
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM events WHERE published_at IS NULL"
            ).fetchone()
        return int(row["count"])


def _row(row: Any) -> DomainEvent:
    payload = json.loads(row["payload_json"])
    return DomainEvent(
        sequence=int(row["sequence"]),
        event_id=row["event_id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=int(row["aggregate_version"]),
        payload=payload if isinstance(payload, dict) else {},
        occurred_at=parse_timestamp(row["occurred_at"]),
        published_at=optional_timestamp(row["published_at"]),
    )
