"""Tournament benchmark jobs: a candidate rule bot against a field, for a range of seeds."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tableforge.poker.tournament import TournamentConfig

TOURNAMENT_PAYLOAD_TYPE = "tableforge.v1.TournamentJobPayload"
TOURNAMENT_OUTPUT_SCHEMA = "tableforge.tournament-samples.v1"

RULE_BOTS = ("random", "call", "tight", "aggro", "pushfold", "hunter")
FIELDS = ("mixed", "call", "tight", "aggro", "pushfold", "hunter")


@dataclass(frozen=True, slots=True)
class TournamentJobPayload:
    schema_version: str
    config_json: str
    seeds: int
    seed_start: int
    candidate: str = "pushfold"
    field: str = "mixed"
    rotate_seats: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported tournament payload schema")
        if self.seeds < 1:
            raise ValueError("seeds must be positive")
        if self.candidate not in RULE_BOTS:
            raise ValueError(f"unknown candidate: {self.candidate}")
        if self.field not in FIELDS:
            raise ValueError(f"unknown field: {self.field}")
        TournamentConfig.from_json(self.config_json)

    @property
    def config(self) -> TournamentConfig:
        return TournamentConfig.from_json(self.config_json)

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "candidate": self.candidate,
                "config_json": self.config_json,
                "field": self.field,
                "rotate_seats": self.rotate_seats,
                "schema_version": self.schema_version,
                "seed_start": self.seed_start,
                "seeds": self.seeds,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, payload: bytes) -> TournamentJobPayload:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("tournament payload must be an object")
        return cls(
            schema_version=str(data["schema_version"]),
            config_json=str(data["config_json"]),
            seeds=int(data["seeds"]),
            seed_start=int(data["seed_start"]),
            candidate=str(data.get("candidate", "pushfold")),
            field=str(data.get("field", "mixed")),
            rotate_seats=bool(data.get("rotate_seats", True)),
        )
