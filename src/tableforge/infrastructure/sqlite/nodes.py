from __future__ import annotations

from pathlib import Path

from tableforge.infrastructure.sql.database import SQLiteDatabase
from tableforge.infrastructure.sql.nodes import SqlNodeRepository


class SQLiteNodeRepository(SqlNodeRepository):
    def __init__(self, database: Path) -> None:
        super().__init__(SQLiteDatabase(database))
