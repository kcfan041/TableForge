from __future__ import annotations

from pathlib import Path

from tableforge.infrastructure.sql.database import SQLiteDatabase
from tableforge.infrastructure.sql.experiments import SqlExperimentRepository


class SQLiteExperimentRepository(SqlExperimentRepository):
    def __init__(self, database: Path) -> None:
        super().__init__(SQLiteDatabase(database))
