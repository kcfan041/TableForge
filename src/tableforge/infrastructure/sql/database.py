from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class Cursor(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Sequence[Any]: ...


class Connection(Protocol):
    def execute(self, sql: str, parameters: Sequence[Any] = ..., /) -> Cursor: ...


class Database(Protocol):
    """A transactional SQL backend shared by the metadata repositories.

    Repositories write portable SQL with ``?`` placeholders. Row locks are expressed with
    ``lock_for_update`` / ``lock_skip_locked`` suffixes, which are empty on SQLite because
    ``BEGIN IMMEDIATE`` already serializes writers there.
    """

    name: str
    lock_for_update: str
    lock_skip_locked: str
    integrity_errors: tuple[type[Exception], ...]

    def transaction(self) -> AbstractContextManager[Connection]: ...

    def read(self) -> AbstractContextManager[Connection]: ...

    def timestamp(self, value: datetime) -> object: ...

    def close(self) -> None: ...


def parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"expected timestamp, got {type(value).__name__}")


def optional_timestamp(value: object) -> datetime | None:
    return None if value is None else parse_timestamp(value)


def require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")


class SQLiteDatabase:
    name = "sqlite"
    lock_for_update = ""
    lock_skip_locked = ""
    integrity_errors: tuple[type[Exception], ...] = (sqlite3.IntegrityError,)

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        from tableforge.infrastructure.sql.migrations import migrate

        migrate(self)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def timestamp(self, value: datetime) -> object:
        require_aware(value)
        return value.astimezone(UTC).isoformat()

    def close(self) -> None:
        return None
