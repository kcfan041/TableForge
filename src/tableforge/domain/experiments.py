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
    # `hands` is the number of tournament seeds and `hands_per_job` the seeds per job.
    TOURNAMENT_BENCHMARK = "tournament_benchmark"


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
    learner_runtime: str = "cpu"
    hidden_size: int = 64
    batch_size: int = 64
    players: int = 2
    observation_schema: str = "poker-hunlhe-vector-v1"
    tournament_json: str = ""
    candidate: str = ""
    opponents: str = ""
    learning_rate: float = 1e-3
    anticipatory: float = 0.1
    epsilon: float = 0.06
    target_update_steps: int = 200
    benchmark_deals: int = 0
    group_id: str = ""

    def __post_init__(self) -> None:
        if self.learner_runtime not in ("cpu", "cuda"):
            raise ValueError("learner_runtime must be cpu or cuda")
        if self.hidden_size < 1 or self.batch_size < 1:
            raise ValueError("hidden_size and batch_size must be positive")
        if self.observation_schema not in ("poker-hunlhe-vector-v1", "poker-hunlhe-vector-v2"):
            raise ValueError(f"unknown observation schema: {self.observation_schema}")
        if not 2 <= self.players <= 6:
            raise ValueError("players must be between 2 and 6")
        if self.players != 2 and self.kind not in (
            ExperimentKind.RULE_SIMULATION,
            ExperimentKind.TOURNAMENT_BENCHMARK,
        ):
            raise ValueError("only rule simulations and tournaments support more than two players")
        if self.kind is ExperimentKind.TOURNAMENT_BENCHMARK:
            from tableforge.domain.tournament import TournamentJobPayload

            TournamentJobPayload("1.0", self.tournament_json, 1, 0, self.candidate, self.opponents)
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
        if not 0 < self.learning_rate <= 1:
            raise ValueError("learning_rate must be in (0, 1]")
        if not 0 <= self.anticipatory <= 1 or not 0 <= self.epsilon <= 1:
            raise ValueError("anticipatory and epsilon must be between 0 and 1")
        if self.target_update_steps < 1:
            raise ValueError("target_update_steps must be positive")
        if self.benchmark_deals < 0 or self.benchmark_deals == 1:
            raise ValueError("benchmark_deals must be 0 (off) or at least 2")
        if len(self.group_id) > 64:
            raise ValueError("group_id is too long")


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
    learner_runtime: str = "cpu"
    hidden_size: int = 64
    batch_size: int = 64
    players: int = 2
    observation_schema: str = "poker-hunlhe-vector-v1"
    tournament_json: str = ""
    candidate: str = ""
    opponents: str = ""
    learning_rate: float = 1e-3
    anticipatory: float = 0.1
    epsilon: float = 0.06
    target_update_steps: int = 200
    benchmark_deals: int = 0
    group_id: str = ""

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
            learner_runtime=self.learner_runtime,
            hidden_size=self.hidden_size,
            batch_size=self.batch_size,
            players=self.players,
            observation_schema=self.observation_schema,
            tournament_json=self.tournament_json,
            candidate=self.candidate,
            opponents=self.opponents,
            learning_rate=self.learning_rate,
            anticipatory=self.anticipatory,
            epsilon=self.epsilon,
            target_update_steps=self.target_update_steps,
            benchmark_deals=self.benchmark_deals,
            group_id=self.group_id,
        )


BUILTIN_PRESETS: tuple[ExperimentPreset, ...] = (
    ExperimentPreset("hu-rule-smoke", "1", hands=1_000, hands_per_job=250),
    ExperimentPreset("hu-rule-soak", "1", hands=1_000_000, hands_per_job=10_000),
    ExperimentPreset("hu-rule-soak-10m", "1", hands=10_000_000, hands_per_job=10_000),
    ExperimentPreset("6max-rule-smoke", "1", hands=1_200, hands_per_job=300, players=6),
    ExperimentPreset("6max-rule-soak", "1", hands=1_000_000, hands_per_job=10_000, players=6),
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
        "hu-nfsp-cpu-iterative-v2",
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
        observation_schema="poker-hunlhe-vector-v2",
        benchmark_deals=200,
    ),
    ExperimentPreset(
        # Training preset for P1-b: 10 rounds x 4 actors x 2,000 hands = 80,000 self-play
        # hands, with a 1,000-deal benchmark against every rule bot after each round.
        "hu-nfsp-v2-train",
        "1",
        hands=2_000,
        hands_per_job=2_000,
        kind=ExperimentKind.NFSP_ITERATIVE,
        evaluation_deals=500,
        rounds=10,
        actors_per_round=4,
        train_steps_per_round=2_000,
        max_evaluation_deals=2_000,
        target_ci_width=100.0,
        hidden_size=128,
        batch_size=128,
        observation_schema="poker-hunlhe-vector-v2",
        benchmark_deals=1_000,
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
    ExperimentPreset(
        "hu-nfsp-gpu-iterative",
        "1",
        hands=4_000,
        hands_per_job=4_000,
        kind=ExperimentKind.NFSP_ITERATIVE,
        evaluation_deals=200,
        rounds=3,
        actors_per_round=2,
        train_steps_per_round=400,
        max_evaluation_deals=1_000,
        target_ci_width=150.0,
        learner_runtime="cuda",
        hidden_size=512,
        batch_size=8_192,
    ),
)


def _tournament_preset(stack_bb: int, payout: str, suffix: str) -> ExperimentPreset:
    """Push/fold baseline in a mixed rule-bot field: 500 seeds x 6 seats = 3,000 tournaments."""
    from tableforge.poker.tournament import preset_config

    return ExperimentPreset(
        f"6max-sng-{stack_bb}bb{suffix}",
        "1",
        hands=500,
        hands_per_job=50,
        kind=ExperimentKind.TOURNAMENT_BENCHMARK,
        players=6,
        tournament_json=preset_config(stack_bb, payout).to_json(),
        candidate="pushfold",
        opponents="mixed",
    )


BUILTIN_PRESETS = BUILTIN_PRESETS + tuple(
    _tournament_preset(stack_bb, payout, suffix)
    for stack_bb in (10, 25, 50)
    for payout, suffix in (("65-35", ""), ("50-30-20", "-top3"))
)
