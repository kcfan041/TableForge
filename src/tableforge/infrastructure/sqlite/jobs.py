from __future__ import annotations

from pathlib import Path

from tableforge.infrastructure.sql.database import SQLiteDatabase
from tableforge.infrastructure.sql.jobs import SqlJobRepository


class SQLiteJobRepository(SqlJobRepository):
    def __init__(self, database: Path) -> None:
        super().__init__(SQLiteDatabase(database))
