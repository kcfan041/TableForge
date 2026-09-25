from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from tableforge.domain.auth import AuthenticationError, IssuedSession
from tableforge.domain.ids import NodeId
from tableforge.infrastructure.sql.database import Database, parse_timestamp, require_aware

ENROLLMENT_TOKEN_PREFIX = "tfe_"


class SqlAuthRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def create_enrollment_token(self, now: datetime, ttl: timedelta, max_uses: int = 1) -> str:
        require_aware(now)
        if max_uses < 1:
            raise ValueError("max_uses must be at least 1")
        if ttl <= timedelta(0):
            raise ValueError("enrollment token ttl must be positive")
        token = f"{ENROLLMENT_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO enrollment_tokens (token_hash, created_at, expires_at, max_uses)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _hash(token),
                    self._db.timestamp(now),
                    self._db.timestamp(now + ttl),
                    max_uses,
                ),
            )
        return token

    def enroll(
        self,
        enrollment_token: str,
        node_id: NodeId,
        now: datetime,
        session_ttl: timedelta,
    ) -> IssuedSession:
        require_aware(now)
        if not node_id:
            raise AuthenticationError("node id is required")
        token_hash = _hash(enrollment_token)
        session = secrets.token_urlsafe(32)
        expires_at = now + session_ttl
        with self._db.transaction() as connection:
            row = connection.execute(
                f"SELECT * FROM enrollment_tokens WHERE token_hash = ?{self._db.lock_for_update}",
                (token_hash,),
            ).fetchone()
            if row is None or parse_timestamp(row["expires_at"]) <= now:
                raise AuthenticationError("invalid or expired enrollment token")
            previous = connection.execute(
                "SELECT 1 FROM enrollment_token_uses WHERE token_hash = ? AND node_id = ?",
                (token_hash, str(node_id)),
            ).fetchone()
            if previous is None:
                used = connection.execute(
                    "SELECT COUNT(*) AS count FROM enrollment_token_uses WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if int(used["count"]) >= int(row["max_uses"]):
                    raise AuthenticationError("enrollment token already used")
                connection.execute(
                    """
                    INSERT INTO enrollment_token_uses (token_hash, node_id, used_at)
                    VALUES (?, ?, ?)
                    """,
                    (token_hash, str(node_id), self._db.timestamp(now)),
                )
                if row["used_by_node"] is None:
                    connection.execute(
                        """
                        UPDATE enrollment_tokens SET used_by_node = ?, used_at = ?
                        WHERE token_hash = ?
                        """,
                        (str(node_id), self._db.timestamp(now), token_hash),
                    )
            connection.execute(
                """
                INSERT INTO node_sessions (token_hash, node_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _hash(session),
                    str(node_id),
                    self._db.timestamp(now),
                    self._db.timestamp(expires_at),
                ),
            )
        return IssuedSession(session, node_id, expires_at)

    def authenticate(self, session_token: str, now: datetime) -> NodeId:
        require_aware(now)
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT * FROM node_sessions WHERE token_hash = ?", (_hash(session_token),)
            ).fetchone()
        if row is None or row["revoked"] or parse_timestamp(row["expires_at"]) <= now:
            raise AuthenticationError("invalid or expired session")
        return NodeId(row["node_id"])

    def revoke_node(self, node_id: NodeId) -> int:
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE node_sessions SET revoked = 1 WHERE node_id = ? AND revoked = 0",
                (str(node_id),),
            )
            return cursor.rowcount

    def rotate_session(
        self, session_token: str, now: datetime, session_ttl: timedelta
    ) -> IssuedSession:
        require_aware(now)
        replacement = secrets.token_urlsafe(32)
        expires_at = now + session_ttl
        with self._db.transaction() as connection:
            row = connection.execute(
                f"SELECT * FROM node_sessions WHERE token_hash = ?{self._db.lock_for_update}",
                (_hash(session_token),),
            ).fetchone()
            if row is None or row["revoked"] or parse_timestamp(row["expires_at"]) <= now:
                raise AuthenticationError("invalid or expired session")
            connection.execute(
                "UPDATE node_sessions SET revoked = 1 WHERE token_hash = ?",
                (_hash(session_token),),
            )
            connection.execute(
                """
                INSERT INTO node_sessions (token_hash, node_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _hash(replacement),
                    row["node_id"],
                    self._db.timestamp(now),
                    self._db.timestamp(expires_at),
                ),
            )
        return IssuedSession(replacement, NodeId(row["node_id"]), expires_at)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
