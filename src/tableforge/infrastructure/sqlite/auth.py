from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from tableforge.domain.auth import AuthenticationError, IssuedSession
from tableforge.domain.ids import NodeId

__all__ = ["AuthenticationError", "IssuedSession", "SQLiteAuthRepository"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollment_tokens (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_by_node TEXT,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS node_sessions (
    token_hash TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
"""


class SQLiteAuthRepository:
    def __init__(self, database: Path) -> None:
        self._database = database
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def create_enrollment_token(self, now: datetime, ttl: timedelta) -> str:
        _require_aware(now)
        token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO enrollment_tokens (token_hash, created_at, expires_at)
                VALUES (?, ?, ?)
                """,
                (_hash(token), now.isoformat(), (now + ttl).isoformat()),
            )
        return token

    def enroll(
        self,
        enrollment_token: str,
        node_id: NodeId,
        now: datetime,
        session_ttl: timedelta,
    ) -> IssuedSession:
        _require_aware(now)
        if not node_id:
            raise AuthenticationError("node id is required")
        token_hash = _hash(enrollment_token)
        session = secrets.token_urlsafe(32)
        expires_at = now + session_ttl
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM enrollment_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or datetime.fromisoformat(row["expires_at"]) <= now:
                raise AuthenticationError("invalid or expired enrollment token")
            if row["used_by_node"] is not None and row["used_by_node"] != str(node_id):
                raise AuthenticationError("enrollment token already used")
            connection.execute(
                """
                UPDATE enrollment_tokens SET used_by_node = ?, used_at = ?
                WHERE token_hash = ?
                """,
                (str(node_id), now.isoformat(), token_hash),
            )
            connection.execute(
                """
                INSERT INTO node_sessions (token_hash, node_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (_hash(session), str(node_id), now.isoformat(), expires_at.isoformat()),
            )
        return IssuedSession(session, node_id, expires_at)

    def authenticate(self, session_token: str, now: datetime) -> NodeId:
        _require_aware(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM node_sessions WHERE token_hash = ?",
                (_hash(session_token),),
            ).fetchone()
        if (
            row is None
            or row["revoked"]
            or datetime.fromisoformat(row["expires_at"]) <= now
        ):
            raise AuthenticationError("invalid or expired session")
        return NodeId(row["node_id"])

    def revoke_node(self, node_id: NodeId) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE node_sessions SET revoked = 1 WHERE node_id = ? AND revoked = 0",
                (str(node_id),),
            )
            return cursor.rowcount


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
