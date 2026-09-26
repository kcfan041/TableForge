from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from tableforge.domain.ids import NodeId, new_node_id

_UUID7 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class NodeIdentityStore:
    path: Path

    def load_or_create(self) -> NodeId:
        """Return this node's permanent id, creating it exactly once.

        The id is written to a private temporary file and then published with an atomic
        hard link, so concurrent callers never observe a partially written file: whoever
        links first wins and everyone else reads the winner's complete id.
        """
        if self.path.exists():
            return self._load_published()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        node_id = new_node_id()
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("w", encoding="ascii", newline="\n") as target:
                target.write(f"{node_id}\n")
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, self.path)
            except FileExistsError:
                return self._load_published()
            return node_id
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> NodeId:
        try:
            value = self.path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise ValueError(f"cannot read node identity: {self.path}") from error
        if not _UUID7.fullmatch(value):
            raise ValueError(f"invalid node identity: {self.path}")
        return NodeId(value)

    def _load_published(self) -> NodeId:
        # A file published by an older version may still be mid-write; give it a moment
        # before declaring it corrupt.
        for attempt in range(20):
            try:
                return self.load()
            except ValueError:
                if attempt == 19:
                    raise
                time.sleep(0.01 * (attempt + 1))
        raise AssertionError("unreachable")
