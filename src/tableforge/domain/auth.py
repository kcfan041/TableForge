from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tableforge.domain.ids import InviteId, NodeId, OperatorId


class AuthenticationError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    node_id: NodeId
    expires_at: datetime


class OperatorRole(StrEnum):
    """Remote control-plane roles.

    Administrative actions (invites, revocation, GC, migrations) are not remote roles:
    they run only on the coordinator host.
    """

    VIEWER = "viewer"
    OPERATOR = "operator"

    def allows(self, required: OperatorRole) -> bool:
        return _RANK[self] >= _RANK[required]


_RANK = {OperatorRole.VIEWER: 0, OperatorRole.OPERATOR: 1}


@dataclass(frozen=True, slots=True)
class InviteScope:
    """What redeeming an invite grants: a node enrollment, an operator credential, or both."""

    node: bool
    role: OperatorRole | None

    def __post_init__(self) -> None:
        if not self.node and self.role is None:
            raise ValueError("an invite must grant a node enrollment or an operator role")

    def encode(self) -> str:
        parts = (["node"] if self.node else []) + ([self.role.value] if self.role else [])
        return ",".join(parts)

    @classmethod
    def decode(cls, value: str) -> InviteScope:
        items = {item.strip() for item in value.split(",") if item.strip()}
        unknown = items - {"node", *OperatorRole}
        if unknown:
            raise ValueError(f"unknown invite scope: {', '.join(sorted(unknown))}")
        roles = [OperatorRole(item) for item in items if item != "node"]
        if len(roles) > 1:
            raise ValueError("an invite grants at most one operator role")
        return cls(node="node" in items, role=roles[0] if roles else None)


@dataclass(frozen=True, slots=True)
class Invite:
    invite_id: InviteId
    scope: InviteScope
    label: str
    created_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None
    redeemed_by: str | None
    revoked: bool

    def usable(self, now: datetime) -> bool:
        return not self.revoked and self.redeemed_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True)
class IssuedOperator:
    operator_id: OperatorId
    token: str
    role: OperatorRole
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    operator_id: OperatorId
    role: OperatorRole
    label: str


@dataclass(frozen=True, slots=True)
class Redemption:
    invite: Invite
    session: IssuedSession | None
    operator: IssuedOperator | None
