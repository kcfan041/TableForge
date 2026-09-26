from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from tableforge.domain.auth import InviteScope

INVITE_PREFIX = "tfinv1_"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class InviteFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InviteCode:
    """Everything a new node or operator needs to join, in one copyable string.

    The code carries no long-lived credential: ``secret`` is exchanged exactly once for a
    node session and/or operator credential. ``fingerprint`` pins the coordinator's CA
    certificate so the first connection cannot be redirected; ``cluster_id`` names the
    cluster and must match what the coordinator reports.
    """

    cluster_id: str
    address: str
    fingerprint: str
    secret: str
    scope: InviteScope

    def __post_init__(self) -> None:
        if not self.cluster_id or not self.address or not self.secret:
            raise InviteFormatError("invite is missing required fields")
        if not _FINGERPRINT.match(self.fingerprint):
            raise InviteFormatError("invite fingerprint must be 64 lowercase hex characters")
        if ":" not in self.address.rsplit("]", 1)[-1]:
            raise InviteFormatError("invite address must include a port, e.g. host:7443")

    def encode(self) -> str:
        payload = {
            "a": self.address,
            "c": self.cluster_id,
            "f": self.fingerprint,
            "s": self.secret,
            "x": self.scope.encode(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> InviteCode:
        value = value.strip()
        if not value.startswith(INVITE_PREFIX):
            raise InviteFormatError(
                "not a TableForge invite (expected it to start with tfinv1_); "
                "check that the whole code was copied"
            )
        body = value.removeprefix(INVITE_PREFIX)
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            data = json.loads(raw)
            return cls(
                cluster_id=str(data["c"]),
                address=str(data["a"]),
                fingerprint=str(data["f"]),
                secret=str(data["s"]),
                scope=InviteScope.decode(str(data["x"])),
            )
        except InviteFormatError:
            raise
        except (ValueError, KeyError, TypeError) as error:
            raise InviteFormatError(
                "invite is corrupted or truncated; copy the whole code again"
            ) from error
