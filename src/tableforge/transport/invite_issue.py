from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from tableforge.application.ports import AuthRepository
from tableforge.domain.auth import AdminInviteError, Invite, InviteScope, check_invite_request
from tableforge.settings import Settings
from tableforge.transport.invite import InviteCode
from tableforge.transport.tls import TlsMaterial, covers


class InviteIssueError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedInvite:
    invite: Invite
    code: str
    warnings: tuple[str, ...]


def coordinator_tls(settings: Settings) -> TlsMaterial:
    return TlsMaterial(
        settings.data_dir / "tls" / "server.crt", settings.data_dir / "tls" / "server.key"
    )


def issue_invite(
    auth: AuthRepository,
    settings: Settings,
    address: str,
    scope: InviteScope,
    ttl: timedelta,
    label: str,
    now: datetime,
    confirm_admin: bool = False,
) -> IssuedInvite:
    """Create a single-use invite code; shared by the CLI, the web API and ControlService."""
    from tableforge.infrastructure.s3_artifacts import parse_endpoints

    if ttl <= timedelta(0):
        raise InviteIssueError("the invite lifetime must be positive")
    try:
        check_invite_request(scope, ttl, confirm_admin)
    except AdminInviteError as error:
        raise InviteIssueError(str(error)) from error
    material = coordinator_tls(settings)
    if not material.certificate_path.is_file():
        raise InviteIssueError(
            "no coordinator certificate; run `tableforge coordinator init` first"
        )
    host = address.rsplit(":", 1)[0].strip("[]")
    if ":" not in address or not covers(material.hosts(), host):
        raise InviteIssueError(
            f"the coordinator certificate does not cover {host!r} "
            f"(covers {', '.join(material.hosts())}). Run "
            f"`tableforge coordinator init --host {host} --regenerate`, restart the "
            "coordinator, and invite again."
        )
    warnings: list[str] = []
    if scope.node and settings.artifacts.backend == "s3" and settings.artifacts.direct_transfer:
        offered = parse_endpoints(settings.artifacts.endpoints)
        if not offered and not settings.artifacts.public_endpoint_url:
            warnings.append(
                "only the internal storage address is offered to nodes; remote machines "
                "usually need artifacts.endpoints (EXTRA_STORAGE_ENDPOINTS)"
            )
    invite, secret = auth.create_invite(scope, now, ttl, label)
    code = InviteCode(
        cluster_id=str(auth.cluster_id(now)),
        address=address,
        fingerprint=material.fingerprint(),
        secret=secret,
        scope=scope,
    ).encode()
    return IssuedInvite(invite, code, tuple(warnings))
