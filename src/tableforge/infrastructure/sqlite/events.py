from __future__ import annotations

from pathlib import Path

from tableforge.infrastructure.sql.database import SQLiteDatabase
from tableforge.infrastructure.sql.events import DomainEvent, SqlEventLog

__all__ = ["DomainEvent", "SQLiteEventLog"]


class SQLiteEventLog(SqlEventLog):
    def __init__(self, database: Path) -> None:
        super().__init__(SQLiteDatabase(database))
