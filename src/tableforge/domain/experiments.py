from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tableforge.domain.ids import ExperimentId


class ExperimentStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    schema_version: str
    preset_key: str
    preset_version: str
    hands: int
    hands_per_job: int
    seed_start: int

    def __post_init__(self) -> None:
        if not self.schema_version or not self.preset_key or not self.preset_version:
            raise ValueError("experiment config identity cannot be empty")
        if self.hands < 1 or self.hands_per_job < 1:
            raise ValueError("hand counts must be positive")


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: ExperimentId
    revision: int
    status: ExperimentStatus
    config: ExperimentConfig
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("experiment timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ExperimentPreset:
    key: str
    version: str
    hands: int
    hands_per_job: int
    seed_start: int = 0

    def config(self) -> ExperimentConfig:
        return ExperimentConfig(
            schema_version="1.0",
            preset_key=self.key,
            preset_version=self.version,
            hands=self.hands,
            hands_per_job=self.hands_per_job,
            seed_start=self.seed_start,
        )


BUILTIN_PRESETS = (
    ExperimentPreset("hu-rule-smoke", "1", hands=1_000, hands_per_job=250),
    ExperimentPreset("hu-rule-soak", "1", hands=1_000_000, hands_per_job=10_000),
)
