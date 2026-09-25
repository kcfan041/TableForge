from __future__ import annotations

import functools
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from tableforge.infrastructure.sql.database import Connection, Cursor


@functools.lru_cache(maxsize=512)
def _placeholders(sql: str) -> str:
    if "%" in sql:
        raise ValueError("portable SQL must not contain '%'")
    return sql.replace("?", "%s")


class _PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters: Sequence[Any] = (), /) -> Cursor:
        cursor: Cursor = self._connection.execute(_placeholders(sql), tuple(parameters))
        return cursor


class PostgresDatabase:
    name = "postgres"
    lock_for_update = " FOR UPDATE"
    lock_skip_locked = " FOR UPDATE SKIP LOCKED"

    def __init__(
        self,
        url: str,
        schema: str | None = None,
        min_size: int = 1,
        max_size: int = 16,
    ) -> None:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        kwargs: dict[str, Any] = {"row_factory": dict_row, "autocommit": False}
        if schema is not None:
            if not schema.replace("_", "").isalnum():
                raise ValueError("schema must be alphanumeric")
            kwargs["options"] = f"-c search_path={schema}"
        self.integrity_errors: tuple[type[Exception], ...] = (psycopg.errors.IntegrityError,)
        self._pool = ConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs=kwargs,
            open=True,
            name="tableforge-metadata",
        )
        self._pool.wait(timeout=30.0)
        self._closed = threading.Event()
        from tableforge.infrastructure.sql.migrations import migrate

        migrate(self)

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._pool.connection() as connection:
            yield _PostgresConnection(connection)

    read = transaction

    def timestamp(self, value: datetime) -> object:
        return value

    def close(self) -> None:
        if not self._closed.is_set():
            self._closed.set()
            self._pool.close()
