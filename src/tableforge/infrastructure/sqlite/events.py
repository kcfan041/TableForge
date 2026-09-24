from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tableforge.domain.ids import new_event_id

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    published_at TEXT
);
"""


def ensure_events_table(connection: sqlite3.Connection) -> None:
    connection.executescript(EVENTS_SCHEMA)


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
    connection: sqlite3.Connection,
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
            occurred_at.isoformat(),
        ),
    )


class SQLiteEventLog:
    def __init__(self, database: Path) -> None:
        self._database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            ensure_events_table(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def list_events(self, after: int = 0, limit: int = 100) -> tuple[DomainEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (after, limit),
            ).fetchall()
        return tuple(_row(row) for row in rows)

    def mark_published(self, up_to: int, now: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE events SET published_at = ? WHERE sequence <= ? AND published_at IS NULL",
                (now.isoformat(), up_to),
            )
            return cursor.rowcount

    def unpublished_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM events WHERE published_at IS NULL"
            ).fetchone()
        return int(row[0])


def _row(row: sqlite3.Row) -> DomainEvent:
    payload = json.loads(row["payload_json"])
    return DomainEvent(
        sequence=row["sequence"],
        event_id=row["event_id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=row["aggregate_version"],
        payload=payload if isinstance(payload, dict) else {},
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        published_at=(
            datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
        ),
    )
