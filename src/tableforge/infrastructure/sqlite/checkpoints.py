from __future__ import annotations

from pathlib import Path

from tableforge.infrastructure.sql.checkpoints import SqlCheckpointRepository
from tableforge.infrastructure.sql.database import SQLiteDatabase


class SQLiteCheckpointRepository(SqlCheckpointRepository):
    def __init__(self, database: Path) -> None:
        super().__init__(SQLiteDatabase(database))
