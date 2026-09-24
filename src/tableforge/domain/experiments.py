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
    FAILED = "failed"


class ExperimentKind(StrEnum):
    RULE_SIMULATION = "rule_simulation"
    NFSP_PIPELINE = "nfsp_pipeline"
    NFSP_ITERATIVE = "nfsp_iterative"


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    schema_version: str
    preset_key: str
    preset_version: str
    hands: int
    hands_per_job: int
    seed_start: int
    kind: ExperimentKind = ExperimentKind.RULE_SIMULATION
    evaluation_deals: int = 0
    rounds: int = 0
    actors_per_round: int = 0
    train_steps_per_round: int = 0
    max_evaluation_deals: int = 0
    target_ci_width: float = 0.0
    league: bool = False
    historical_limit: int = 0
    matrix_deals: int = 0

    def __post_init__(self) -> None:
        if not self.schema_version or not self.preset_key or not self.preset_version:
            raise ValueError("experiment config identity cannot be empty")
        if self.hands < 1 or self.hands_per_job < 1:
            raise ValueError("hand counts must be positive")
        if self.kind is ExperimentKind.NFSP_PIPELINE and self.evaluation_deals < 2:
            raise ValueError("NFSP pipeline requires at least two evaluation deals")
        if self.kind is ExperimentKind.NFSP_ITERATIVE:
            if min(self.rounds, self.actors_per_round, self.train_steps_per_round) < 1:
                raise ValueError("iterative NFSP requires positive rounds, actors, and steps")
            if self.evaluation_deals < 2 or self.max_evaluation_deals < self.evaluation_deals:
                raise ValueError("iterative NFSP requires valid evaluation deal limits")
            if self.target_ci_width < 0:
                raise ValueError("target_ci_width cannot be negative")
            if self.league and (self.historical_limit < 1 or self.matrix_deals < 2):
                raise ValueError("league requires historical_limit and matrix_deals")


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
    kind: ExperimentKind = ExperimentKind.RULE_SIMULATION
    evaluation_deals: int = 0
    rounds: int = 0
    actors_per_round: int = 0
    train_steps_per_round: int = 0
    max_evaluation_deals: int = 0
    target_ci_width: float = 0.0
    league: bool = False
    historical_limit: int = 0
    matrix_deals: int = 0

    def config(self) -> ExperimentConfig:
        return ExperimentConfig(
            schema_version="1.0",
            preset_key=self.key,
            preset_version=self.version,
            hands=self.hands,
            hands_per_job=self.hands_per_job,
            seed_start=self.seed_start,
            kind=self.kind,
            evaluation_deals=self.evaluation_deals,
            rounds=self.rounds,
            actors_per_round=self.actors_per_round,
            train_steps_per_round=self.train_steps_per_round,
            max_evaluation_deals=self.max_evaluation_deals,
            target_ci_width=self.target_ci_width,
            league=self.league,
            historical_limit=self.historical_limit,
            matrix_deals=self.matrix_deals,
        )


BUILTIN_PRESETS = (
    ExperimentPreset("hu-rule-smoke", "1", hands=1_000, hands_per_job=250),
    ExperimentPreset("hu-rule-soak", "1", hands=1_000_000, hands_per_job=10_000),
    ExperimentPreset("hu-rule-soak-10m", "1", hands=10_000_000, hands_per_job=10_000),
    ExperimentPreset(
        "hu-nfsp-cpu-smoke",
        "1",
        hands=2_000,
        hands_per_job=2_000,
        kind=ExperimentKind.NFSP_PIPELINE,
        evaluation_deals=500,
    ),
    ExperimentPreset(
        "hu-nfsp-cpu-iterative",
        "1",
        hands=1_000,
        hands_per_job=1_000,
        kind=ExperimentKind.NFSP_ITERATIVE,
        evaluation_deals=200,
        rounds=3,
        actors_per_round=2,
        train_steps_per_round=400,
        max_evaluation_deals=1_000,
        target_ci_width=150.0,
    ),
    ExperimentPreset(
        "hu-nfsp-cpu-league",
        "1",
        hands=1_000,
        hands_per_job=1_000,
        kind=ExperimentKind.NFSP_ITERATIVE,
        evaluation_deals=200,
        rounds=4,
        actors_per_round=2,
        train_steps_per_round=400,
        max_evaluation_deals=1_000,
        target_ci_width=150.0,
        league=True,
        historical_limit=3,
        matrix_deals=200,
    ),
)
