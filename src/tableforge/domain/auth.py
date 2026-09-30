from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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


class Scope(StrEnum):
    """What an operator credential may do. Checks are always made on scopes, never on roles.

    Scopes are independent of each other; a credential can hold any combination, for
    example read and download models but not control experiments. Schema migrations, GC
    and certificate changes stay host-only whatever the scopes.
    """

    EXPERIMENT_READ = "experiment:read"
    """See the cluster, experiments, jobs, checkpoints, events and diagnostics."""
    EXPERIMENT_CONTROL = "experiment:control"
    """Create, start and stop experiments."""
    MODEL_READ = "model:read"
    """See the model list and the results of replays and strategy tables."""
    MODEL_DOWNLOAD = "model:download"
    """Download model checkpoints to another computer."""
    MODEL_MANAGE = "model:manage"
    """Save an experiment's checkpoint as a named model."""
    ANALYSIS_RUN = "analysis:run"
    """Start analysis jobs that use workers (replays, strategy tables)."""
    PLAY_REMOTE = "play:remote"
    """Play against models running on the cluster host."""
    ADMIN_MANAGE = "admin:manage"
    """Invites, credentials and their scopes, node revocation, deleting models."""


ALL_SCOPES: frozenset[Scope] = frozenset(Scope)


class OperatorRole(StrEnum):
    """Named scope combinations. A credential's role is only a label; `custom` means its
    scopes match none of the presets.

    An admin can hand ``admin:manage`` on through an invite only with an explicit
    confirmation and a short lifetime (``ADMIN_INVITE_MAX_TTL``); redeeming it is recorded
    as an event.
    """

    VIEWER = "viewer"
    OPERATOR = "operator"
    MODEL_RECIPIENT = "model_recipient"
    ADMIN = "admin"
    CUSTOM = "custom"

    @property
    def scopes(self) -> frozenset[Scope]:
        return ROLE_SCOPES[self]


_VIEWER = frozenset({Scope.EXPERIMENT_READ, Scope.MODEL_READ})
ROLE_SCOPES: dict[OperatorRole, frozenset[Scope]] = {
    OperatorRole.VIEWER: _VIEWER,
    # Operators keep everything they could do before scopes existed: run experiments,
    # start analyses, save models; plus remote play.
    OperatorRole.OPERATOR: _VIEWER
    | {Scope.EXPERIMENT_CONTROL, Scope.PLAY_REMOTE, Scope.ANALYSIS_RUN, Scope.MODEL_MANAGE},
    OperatorRole.MODEL_RECIPIENT: _VIEWER | {Scope.MODEL_DOWNLOAD},
    OperatorRole.ADMIN: ALL_SCOPES,
    OperatorRole.CUSTOM: frozenset(),
}
INVITABLE_ROLES = (
    OperatorRole.VIEWER,
    OperatorRole.OPERATOR,
    OperatorRole.MODEL_RECIPIENT,
    OperatorRole.ADMIN,
)


def role_for(scopes: frozenset[Scope]) -> OperatorRole:
    """The preset these scopes match exactly, or `custom`."""
    for role in INVITABLE_ROLES:
        if ROLE_SCOPES[role] == scopes:
            return role
    return OperatorRole.CUSTOM


def parse_scopes(values: object) -> frozenset[Scope]:
    """Scopes from a list of names (or a comma-separated string); unknown names fail."""
    if isinstance(values, str):
        values = [item for item in values.split(",") if item.strip()]
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("scopes must be a list")
    result = set()
    for value in values:
        try:
            result.add(Scope(str(value).strip()))
        except ValueError as error:
            raise ValueError(f"unknown scope: {value}") from error
    return frozenset(result)
ADMIN_INVITE_MAX_TTL = timedelta(hours=1)


class AdminInviteError(ValueError):
    """An admin invite was requested without confirmation or with too long a lifetime."""


def check_invite_request(scope: InviteScope, ttl: timedelta, confirm_admin: bool) -> None:
    if Scope.ADMIN_MANAGE in scope.scopes:
        if not confirm_admin:
            raise AdminInviteError(
                "an invite with the admin role lets its holder manage the whole cluster; "
                "confirm it explicitly"
            )
        if ttl > ADMIN_INVITE_MAX_TTL:
            raise AdminInviteError("an invite with the admin role can be valid for 1 hour at most")


@dataclass(frozen=True, slots=True)
class InviteScope:
    """What redeeming an invite grants: a node enrollment, an operator credential, or both.

    The credential's scopes are either a role preset (`role`) or an explicit set
    (`custom_scopes`). Encoded as ``node,operator`` (presets, as before) or
    ``node,scope:experiment:read,scope:model:download`` (explicit scopes).
    """

    node: bool
    role: OperatorRole | None
    custom_scopes: frozenset[Scope] | None = None

    def __post_init__(self) -> None:
        if self.role is OperatorRole.CUSTOM:
            raise ValueError("use custom_scopes instead of the custom role")
        if self.role is not None and self.custom_scopes is not None:
            raise ValueError("an invite grants a role or explicit scopes, not both")
        if self.custom_scopes is not None and not self.custom_scopes:
            raise ValueError("an invite with explicit scopes needs at least one scope")
        if not self.node and self.role is None and self.custom_scopes is None:
            raise ValueError("an invite must grant a node enrollment or operator scopes")

    @property
    def grants_operator(self) -> bool:
        return self.role is not None or self.custom_scopes is not None

    @property
    def scopes(self) -> frozenset[Scope]:
        if self.custom_scopes is not None:
            return self.custom_scopes
        return ROLE_SCOPES[self.role] if self.role is not None else frozenset()

    @property
    def operator_role(self) -> OperatorRole | None:
        """Role label of the credential this invite creates."""
        if self.custom_scopes is not None:
            return role_for(self.custom_scopes)
        return self.role

    def encode(self) -> str:
        parts = ["node"] if self.node else []
        if self.role is not None:
            parts.append(self.role.value)
        if self.custom_scopes is not None:
            parts.extend(f"scope:{scope.value}" for scope in sorted(self.custom_scopes))
        return ",".join(parts)

    @classmethod
    def decode(cls, value: str) -> InviteScope:
        items = [item.strip() for item in value.split(",") if item.strip()]
        roles: list[OperatorRole] = []
        scopes: set[Scope] = set()
        node = False
        for item in items:
            if item == "node":
                node = True
            elif item.startswith("scope:"):
                try:
                    scopes.add(Scope(item.removeprefix("scope:")))
                except ValueError as error:
                    raise ValueError(f"unknown invite scope: {item}") from error
            else:
                try:
                    role = OperatorRole(item)
                except ValueError as error:
                    raise ValueError(f"unknown invite scope: {item}") from error
                if role is OperatorRole.CUSTOM:
                    raise ValueError(f"unknown invite scope: {item}")
                roles.append(role)
        if len(roles) > 1:
            raise ValueError("an invite grants at most one operator role")
        return cls(
            node=node,
            role=roles[0] if roles else None,
            custom_scopes=frozenset(scopes) if scopes else None,
        )


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
    scopes: frozenset[Scope] = frozenset()


@dataclass(frozen=True, slots=True)
class OperatorIdentity:
    operator_id: OperatorId
    role: OperatorRole
    label: str
    scopes: frozenset[Scope] = frozenset()

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def scope_list(self) -> list[str]:
        return sorted(scope.value for scope in self.scopes)


@dataclass(frozen=True, slots=True)
class WebSession:
    token: str
    csrf_token: str
    identity: OperatorIdentity
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OperatorRecord:
    identity: OperatorIdentity
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Redemption:
    invite: Invite
    session: IssuedSession | None
    operator: IssuedOperator | None
