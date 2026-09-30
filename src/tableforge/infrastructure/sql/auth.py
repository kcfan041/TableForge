from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from tableforge.domain.auth import (
    ROLE_SCOPES,
    AuthenticationError,
    Invite,
    InviteScope,
    IssuedOperator,
    IssuedSession,
    OperatorIdentity,
    OperatorRecord,
    OperatorRole,
    Redemption,
    Scope,
    WebSession,
    parse_scopes,
    role_for,
)
from tableforge.domain.ids import (
    ClusterId,
    InviteId,
    NodeId,
    OperatorId,
    WorkerId,
    new_cluster_id,
    new_invite_id,
    new_operator_id,
    new_worker_id,
)
from tableforge.infrastructure.sql.database import (
    Database,
    optional_timestamp,
    parse_timestamp,
    require_aware,
)
from tableforge.infrastructure.sql.events import record_event

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
        if session_token.startswith(OPERATOR_TOKEN_PREFIX):
            raise AuthenticationError("operator credentials cannot act as a node")
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

    def cluster_id(self, now: datetime) -> ClusterId:
        """Return this cluster's permanent identity, creating it on first use."""
        with self._db.transaction() as connection:
            row = connection.execute(
                f"SELECT cluster_id FROM cluster_identity WHERE singleton = 1"
                f"{self._db.lock_for_update}"
            ).fetchone()
            if row is not None:
                return ClusterId(row["cluster_id"])
            cluster = new_cluster_id()
            connection.execute(
                "INSERT INTO cluster_identity (singleton, cluster_id, created_at) VALUES (1, ?, ?)",
                (str(cluster), self._db.timestamp(now)),
            )
            return cluster

    def create_invite(
        self, scope: InviteScope, now: datetime, ttl: timedelta, label: str = ""
    ) -> tuple[Invite, str]:
        """Create a single-use invite and return it with its secret (shown only once)."""
        require_aware(now)
        if ttl <= timedelta(0):
            raise ValueError("invite ttl must be positive")
        secret = secrets.token_urlsafe(32)
        invite = Invite(
            invite_id=new_invite_id(),
            scope=scope,
            label=label,
            created_at=now,
            expires_at=now + ttl,
            redeemed_at=None,
            redeemed_by=None,
            revoked=False,
        )
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO invites (
                    invite_id, secret_hash, scope, label, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(invite.invite_id),
                    _hash(secret),
                    scope.encode(),
                    label,
                    self._db.timestamp(now),
                    self._db.timestamp(invite.expires_at),
                ),
            )
        return invite, secret

    def redeem_invite(
        self,
        secret: str,
        node_id: NodeId | None,
        now: datetime,
        session_ttl: timedelta,
        operator_ttl: timedelta,
    ) -> Redemption:
        """Exchange an invite secret for long-lived credentials, exactly once.

        A node-scoped invite requires ``node_id`` and yields a node session. A role-scoped
        invite yields an operator credential. The secret itself never authenticates anything
        afterwards.
        """
        require_aware(now)
        with self._db.transaction() as connection:
            row = connection.execute(
                f"SELECT * FROM invites WHERE secret_hash = ?{self._db.lock_for_update}",
                (_hash(secret),),
            ).fetchone()
            if row is None:
                raise AuthenticationError("invalid invite")
            invite = _invite(row)
            if invite.revoked:
                raise AuthenticationError("invite was revoked")
            if invite.redeemed_at is not None:
                raise AuthenticationError("invite was already used")
            if now >= invite.expires_at:
                raise AuthenticationError("invite expired")
            if invite.scope.node and not node_id:
                raise AuthenticationError("this invite enrolls a node; node id is required")
            session: IssuedSession | None = None
            if invite.scope.node and node_id:
                token = secrets.token_urlsafe(32)
                session = IssuedSession(token, node_id, now + session_ttl)
                connection.execute(
                    """
                    INSERT INTO node_sessions (
                        token_hash, node_id, created_at, expires_at, invite_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        _hash(token),
                        str(node_id),
                        self._db.timestamp(now),
                        self._db.timestamp(session.expires_at),
                        str(invite.invite_id),
                    ),
                )
            operator: IssuedOperator | None = None
            if invite.scope.grants_operator:
                token = f"{OPERATOR_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
                role = invite.scope.operator_role or OperatorRole.CUSTOM
                operator = IssuedOperator(
                    new_operator_id(), token, role, now + operator_ttl, invite.scope.scopes
                )
                connection.execute(
                    """
                    INSERT INTO operators (
                        operator_id, token_hash, role, label, invite_id, created_at, expires_at,
                        scopes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(operator.operator_id),
                        _hash(token),
                        operator.role.value,
                        invite.label,
                        str(invite.invite_id),
                        self._db.timestamp(now),
                        self._db.timestamp(operator.expires_at),
                        _encode_scopes(operator.scopes),
                    ),
                )
            redeemed_by = str(node_id) if node_id else str(operator.operator_id if operator else "")
            connection.execute(
                "UPDATE invites SET redeemed_at = ?, redeemed_by = ? WHERE invite_id = ?",
                (self._db.timestamp(now), redeemed_by, str(invite.invite_id)),
            )
            if operator is not None and Scope.ADMIN_MANAGE in operator.scopes:
                record_event(
                    self._db,
                    connection,
                    "ADMIN_INVITE_REDEEMED",
                    "invite",
                    str(invite.invite_id),
                    1,
                    {
                        "label": invite.label,
                        "node_id": str(node_id or ""),
                        "operator_id": str(operator.operator_id),
                    },
                    now,
                )
        redeemed = Invite(
            invite.invite_id,
            invite.scope,
            invite.label,
            invite.created_at,
            invite.expires_at,
            now,
            redeemed_by,
            False,
        )
        return Redemption(redeemed, session, operator)

    def list_invites(self) -> tuple[Invite, ...]:
        with self._db.read() as connection:
            rows = connection.execute("SELECT * FROM invites ORDER BY created_at").fetchall()
        return tuple(_invite(row) for row in rows)

    def revoke_invite(self, invite_id: InviteId) -> bool:
        with self._db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE invites SET revoked = 1
                WHERE invite_id = ? AND revoked = 0 AND redeemed_at IS NULL
                """,
                (str(invite_id),),
            )
            return cursor.rowcount == 1

    def authenticate_operator(self, token: str, now: datetime) -> OperatorIdentity:
        require_aware(now)
        if not token.startswith(OPERATOR_TOKEN_PREFIX):
            raise AuthenticationError("invalid operator credential")
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT * FROM operators WHERE token_hash = ?", (_hash(token),)
            ).fetchone()
        if row is None or row["revoked"] or parse_timestamp(row["expires_at"]) <= now:
            raise AuthenticationError("invalid or expired operator credential")
        return _identity(row)

    def list_operators(self) -> tuple[OperatorIdentity, ...]:
        with self._db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM operators WHERE revoked = 0 ORDER BY created_at"
            ).fetchall()
        return tuple(_identity(row) for row in rows)

    def revoke_operator(self, operator_id: OperatorId) -> bool:
        with self._db.transaction() as connection:
            target = connection.execute(
                "SELECT * FROM operators WHERE operator_id = ? AND revoked = 0",
                (str(operator_id),),
            ).fetchone()
            if target is not None and Scope.ADMIN_MANAGE in _identity(target).scopes:
                self._require_other_admin(connection, operator_id)
            cursor = connection.execute(
                "UPDATE operators SET revoked = 1 WHERE operator_id = ? AND revoked = 0",
                (str(operator_id),),
            )
            connection.execute(
                "DELETE FROM web_sessions WHERE operator_id = ?", (str(operator_id),)
            )
            return cursor.rowcount == 1

    def issue_operator(
        self,
        role: OperatorRole,
        label: str,
        now: datetime,
        ttl: timedelta,
        scopes: frozenset[Scope] | None = None,
    ) -> IssuedOperator:
        """Issue an operator credential directly, without an invite.

        This is the only way to obtain the admin role and must only be reachable from the
        coordinator host.
        """
        require_aware(now)
        if ttl <= timedelta(0):
            raise ValueError("operator ttl must be positive")
        granted = ROLE_SCOPES[role] if scopes is None else scopes
        token = f"{OPERATOR_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        issued = IssuedOperator(new_operator_id(), token, role_for(granted), now + ttl, granted)
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO operators (
                    operator_id, token_hash, role, label, invite_id, created_at, expires_at,
                    scopes
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    str(issued.operator_id),
                    _hash(token),
                    issued.role.value,
                    label,
                    self._db.timestamp(now),
                    self._db.timestamp(issued.expires_at),
                    _encode_scopes(granted),
                ),
            )
        return issued

    def set_operator_scopes(
        self, operator_id: OperatorId, scopes: frozenset[Scope], now: datetime
    ) -> OperatorIdentity:
        """Replace an operator's scopes (the role label follows). Keeps at least one admin."""
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM operators WHERE operator_id = ? AND revoked = 0",
                (str(operator_id),),
            ).fetchone()
            if row is None:
                raise KeyError(str(operator_id))
            before = _identity(row)
            if Scope.ADMIN_MANAGE in before.scopes and Scope.ADMIN_MANAGE not in scopes:
                self._require_other_admin(connection, operator_id)
            role = role_for(scopes)
            connection.execute(
                "UPDATE operators SET role = ?, scopes = ? WHERE operator_id = ?",
                (role.value, _encode_scopes(scopes), str(operator_id)),
            )
            record_event(
                self._db,
                connection,
                "OPERATOR_SCOPES_CHANGED",
                "operator",
                str(operator_id),
                1,
                {"before": before.scope_list(), "after": sorted(scope.value for scope in scopes)},
                now,
            )
        return OperatorIdentity(operator_id, role, before.label, scopes)

    def _require_other_admin(self, connection: Any, operator_id: OperatorId) -> None:
        rows = connection.execute(
            "SELECT * FROM operators WHERE revoked = 0 AND operator_id <> ?",
            (str(operator_id),),
        ).fetchall()
        if not any(Scope.ADMIN_MANAGE in _identity(row).scopes for row in rows):
            raise ValueError("the cluster must keep at least one credential with admin:manage")

    def list_operator_records(self) -> tuple[OperatorRecord, ...]:
        with self._db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM operators WHERE revoked = 0 ORDER BY created_at"
            ).fetchall()
        return tuple(
            OperatorRecord(
                _identity(row),
                parse_timestamp(row["created_at"]),
                parse_timestamp(row["expires_at"]),
            )
            for row in rows
        )

    def create_web_session(self, operator_token: str, now: datetime, ttl: timedelta) -> WebSession:
        """Exchange an operator credential for a browser session.

        The session never outlives the credential it came from.
        """
        identity = self.authenticate_operator(operator_token, now)
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT expires_at FROM operators WHERE operator_id = ?",
                (str(identity.operator_id),),
            ).fetchone()
        expires_at = min(now + ttl, parse_timestamp(row["expires_at"]))
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self._db.transaction() as connection:
            connection.execute(
                "DELETE FROM web_sessions WHERE expires_at <= ?", (self._db.timestamp(now),)
            )
            connection.execute(
                """
                INSERT INTO web_sessions (
                    token_hash, operator_id, csrf_token, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _hash(token),
                    str(identity.operator_id),
                    csrf,
                    self._db.timestamp(now),
                    self._db.timestamp(expires_at),
                ),
            )
        return WebSession(token, csrf, identity, expires_at)

    def web_session(self, token: str, now: datetime) -> WebSession:
        require_aware(now)
        with self._db.read() as connection:
            row = connection.execute(
                """
                SELECT s.csrf_token, s.expires_at AS session_expires, o.*
                FROM web_sessions s JOIN operators o ON o.operator_id = s.operator_id
                WHERE s.token_hash = ?
                """,
                (_hash(token),),
            ).fetchone()
        if (
            row is None
            or row["revoked"]
            or parse_timestamp(row["session_expires"]) <= now
            or parse_timestamp(row["expires_at"]) <= now
        ):
            raise AuthenticationError("session expired; sign in again")
        identity = _identity(row)
        return WebSession(
            token, row["csrf_token"], identity, parse_timestamp(row["session_expires"])
        )

    def end_web_session(self, token: str) -> None:
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM web_sessions WHERE token_hash = ?", (_hash(token),))

    def list_worker_slots(self) -> tuple[tuple[WorkerId, NodeId, str, datetime], ...]:
        with self._db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM worker_slots ORDER BY last_seen_at DESC"
            ).fetchall()
        return tuple(
            (
                WorkerId(row["worker_id"]),
                NodeId(row["node_id"]),
                row["label"],
                parse_timestamp(row["last_seen_at"]),
            )
            for row in rows
        )

    def start_worker(self, node_id: NodeId, label: str, now: datetime) -> WorkerId:
        require_aware(now)
        worker_id = new_worker_id()
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO worker_slots (worker_id, node_id, label, started_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(worker_id),
                    str(node_id),
                    label,
                    self._db.timestamp(now),
                    self._db.timestamp(now),
                ),
            )
        return worker_id

    def worker_node(self, worker_id: WorkerId) -> NodeId | None:
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT node_id FROM worker_slots WHERE worker_id = ?", (str(worker_id),)
            ).fetchone()
        return NodeId(row["node_id"]) if row is not None else None

    def touch_worker(self, worker_id: WorkerId, now: datetime) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "UPDATE worker_slots SET last_seen_at = ? WHERE worker_id = ?",
                (self._db.timestamp(now), str(worker_id)),
            )


OPERATOR_TOKEN_PREFIX = "tfo_"


def _invite(row: Any) -> Invite:
    return Invite(
        invite_id=InviteId(row["invite_id"]),
        scope=InviteScope.decode(row["scope"]),
        label=row["label"],
        created_at=parse_timestamp(row["created_at"]),
        expires_at=parse_timestamp(row["expires_at"]),
        redeemed_at=optional_timestamp(row["redeemed_at"]),
        redeemed_by=row["redeemed_by"],
        revoked=bool(row["revoked"]),
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _encode_scopes(scopes: frozenset[Scope]) -> str:
    return ",".join(sorted(scope.value for scope in scopes))


def _identity(row: Any) -> OperatorIdentity:
    """Identity from an operators row; an empty scopes column means the role's scopes."""
    role = OperatorRole(row["role"])
    stored = row["scopes"] if "scopes" in row.keys() else ""  # noqa: SIM118
    scopes = parse_scopes(stored) if stored else ROLE_SCOPES[role]
    return OperatorIdentity(OperatorId(row["operator_id"]), role, row["label"], scopes)
