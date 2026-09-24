from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SimulationJobPayload:
    schema_version: str
    hands: int
    seed_start: int
    starting_stack: int = 100
    small_blind: int = 1
    big_blind: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported simulation payload schema")
        if self.hands < 1:
            raise ValueError("hands must be positive")
        if self.starting_stack < self.big_blind:
            raise ValueError("starting_stack must cover the big blind")
        if not 0 < self.small_blind < self.big_blind:
            raise ValueError("invalid blinds")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "big_blind": self.big_blind,
                "hands": self.hands,
                "schema_version": self.schema_version,
                "seed_start": self.seed_start,
                "small_blind": self.small_blind,
                "starting_stack": self.starting_stack,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, payload: bytes) -> SimulationJobPayload:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("simulation payload must be an object")
        return cls(
            schema_version=str(data["schema_version"]),
            hands=int(data["hands"]),
            seed_start=int(data["seed_start"]),
            starting_stack=int(data["starting_stack"]),
            small_blind=int(data["small_blind"]),
            big_blind=int(data["big_blind"]),
        )
