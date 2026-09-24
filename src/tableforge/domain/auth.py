from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tableforge.domain.ids import NodeId


class AuthenticationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    node_id: NodeId
    expires_at: datetime
