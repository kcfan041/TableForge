from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from tableforge.domain.ids import NodeId, new_node_id

_UUID7 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class NodeIdentityStore:
    path: Path

    def load_or_create(self) -> NodeId:
        if self.path.exists():
            return self.load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        node_id = new_node_id()
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            return self.load()
        try:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as target:
                target.write(f"{node_id}\n")
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        return node_id

    def load(self) -> NodeId:
        try:
            value = self.path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise ValueError(f"cannot read node identity: {self.path}") from error
        if not _UUID7.fullmatch(value):
            raise ValueError(f"invalid node identity: {self.path}")
        return NodeId(value)
