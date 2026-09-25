from __future__ import annotations

from pathlib import Path

from tableforge.domain.auth import AuthenticationError, IssuedSession
from tableforge.infrastructure.sql.auth import SqlAuthRepository
from tableforge.infrastructure.sql.database import SQLiteDatabase

__all__ = ["AuthenticationError", "IssuedSession", "SQLiteAuthRepository"]


class SQLiteAuthRepository(SqlAuthRepository):
    def __init__(self, database: Path) -> None:
        super().__init__(SQLiteDatabase(database))
