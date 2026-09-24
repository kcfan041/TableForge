from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.ids import NodeId


@dataclass(frozen=True, slots=True)
class RegisteredNode:
    node_id: NodeId
    capability: NodeCapability
    registered_at: datetime
    last_heartbeat_at: datetime

    def __post_init__(self) -> None:
        if self.registered_at.tzinfo is None or self.last_heartbeat_at.tzinfo is None:
            raise ValueError("node timestamps must be timezone-aware")
