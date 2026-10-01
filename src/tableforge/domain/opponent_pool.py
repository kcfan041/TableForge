"""Opponent pools as job inputs: which rule bots and model checkpoints the pool contains.

The coordinator writes a pool once per round as a content-addressed artifact; actor and
match jobs reference it. Workers verify every model checkpoint by sha256 before loading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

POOL_SCHEMA = "tableforge.opponent-pool.v1"
WEIGHTINGS = ("uniform", "hard", "variance")


@dataclass(frozen=True, slots=True)
class PoolModel:
    name: str
    artifact_id: str
    sha256: str
    kind: str = "model"
    """``model`` (from the model library) or ``history`` (an earlier round)."""
    algorithm: str = "nfsp"
    """Written to the pool only when not NFSP, so existing pools keep their bytes."""

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or not self.artifact_id or not self.name:
            raise ValueError("pool model reference is invalid")
        if self.kind not in ("model", "history"):
            raise ValueError("pool model kind must be model or history")
        if not self.algorithm:
            raise ValueError("pool model algorithm cannot be empty")


@dataclass(frozen=True, slots=True)
class OpponentPoolSpec:
    rules: tuple[str, ...]
    models: tuple[PoolModel, ...] = field(default=())
    weighting: str = "uniform"

    def __post_init__(self) -> None:
        if self.weighting not in WEIGHTINGS:
            raise ValueError(f"weighting must be one of {', '.join(WEIGHTINGS)}")
        if not self.rules and not self.models:
            raise ValueError("an opponent pool needs at least one member")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "schema_version": POOL_SCHEMA,
                "rules": list(self.rules),
                "models": [
                    {
                        "name": model.name,
                        "artifact_id": model.artifact_id,
                        "sha256": model.sha256,
                        "kind": model.kind,
                        **({} if model.algorithm == "nfsp" else {"algorithm": model.algorithm}),
                    }
                    for model in self.models
                ],
                "weighting": self.weighting,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, content: bytes) -> OpponentPoolSpec:
        data = json.loads(content)
        if data.get("schema_version") != POOL_SCHEMA:
            raise ValueError("unsupported opponent pool schema")
        return cls(
            tuple(str(rule) for rule in data["rules"]),
            tuple(PoolModel(**model) for model in data["models"]),
            str(data["weighting"]),
        )
