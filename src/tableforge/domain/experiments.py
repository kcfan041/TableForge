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


V3_OBSERVATION_SCHEMA = "poker-nlhe-vector-v3"
V4_OBSERVATION_SCHEMA = "poker-nlhe-vector-v4"
"""v3 plus the field around our table (T5 MTT stages)."""
OBSERVATION_SCHEMAS = (
    "poker-hunlhe-vector-v1",
    "poker-hunlhe-vector-v2",
    V3_OBSERVATION_SCHEMA,
    V4_OBSERVATION_SCHEMA,
)
NINE_SEAT_SCHEMAS = (V3_OBSERVATION_SCHEMA, V4_OBSERVATION_SCHEMA)


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
    reward_scale: float = 1.0
    benchmark_deals: int = 0
    group_id: str = ""
    self_play_share: float = 0.5
    hand_horizon: bool = False
    double_q: bool = False
    rl_capacity: int = 50_000
    sl_capacity: int = 100_000
    pool_models: str = ""
    pool_history: int = 0
    pfsp: str = "uniform"
    pool_eval_seeds: int = 0
    exploit_target: str = ""
    """S4: a model id; every other seat is this frozen model and the learner trains to beat
    it. The learner's best-response ROI against a field of the target is a lower bound on
    how much the target can be exploited."""
    algorithm: str = "nfsp"
    """The training algorithm (`learning.algorithms`) of an iterative training experiment."""
    action_schema: str = "nlhe-discrete-v1"
    """Abstract actions of the trained model (`learning.actions`)."""
    sampler_json: str = ""
    """S3: train on tournaments drawn from this `TournamentSampler`; `tournament_json` stays
    the benchmark tournament."""
    entropy_coef: float = 0.01
    """PPO: weight of the entropy bonus (keeps the policy exploring)."""
    gae_lambda: float = 0.95
    """PPO: GAE lambda (0 = one-step advantages, 1 = full returns)."""
    clip: float = 0.2
    """PPO: how far one update may move each action's probability ratio."""
    magnet_coef: float = 0.0
    """PPO with MMD: weight of the pull towards the magnet policy (0 = plain PPO)."""
    magnet_every: int = 10
    """PPO with MMD: the magnet becomes the current policy every this many rounds."""

    def __post_init__(self) -> None:
        if self.magnet_coef < 0 or self.magnet_every < 1:
            raise ValueError("invalid MMD settings")
        if not self.algorithm or len(self.algorithm) > 32:
            raise ValueError("algorithm must be a short name")
        if self.algorithm == "ppo":
            if self.kind is not ExperimentKind.NFSP_ITERATIVE or not self.tournament_json:
                raise ValueError("PPO trains on tournaments (an iterative tournament preset)")
            if self.league or self.exploit_target:
                raise ValueError("PPO does not support league or exploit experiments yet")
        if self.entropy_coef < 0 or not 0 <= self.gae_lambda <= 1 or not 0 < self.clip < 1:
            raise ValueError("invalid PPO settings")
        if self.exploit_target:
            if self.kind is not ExperimentKind.NFSP_ITERATIVE or not self.tournament_json:
                raise ValueError("an exploit target needs tournament NFSP training")
            if self.pool_models or self.pool_history:
                raise ValueError("an exploit target replaces the opponent pool")
            if self.self_play_share != 0:
                raise ValueError("an exploit experiment trains against the target only")
            if len(self.exploit_target) > 80:
                raise ValueError("exploit target is too long")
        if self.learner_runtime not in ("cpu", "cuda"):
            raise ValueError("learner_runtime must be cpu or cuda")
        if self.hidden_size < 1 or self.batch_size < 1:
            raise ValueError("hidden_size and batch_size must be positive")
        if self.observation_schema not in OBSERVATION_SCHEMAS:
            raise ValueError(f"unknown observation schema: {self.observation_schema}")
        if self.action_schema not in ("nlhe-discrete-v1", "nlhe-discrete-v2"):
            raise ValueError(f"unknown action schema: {self.action_schema}")
        if not 2 <= self.players <= 9:
            raise ValueError("players must be between 2 and 9")
        tournament_training = self.kind is ExperimentKind.NFSP_ITERATIVE and bool(
            self.tournament_json
        )
        if (
            self.players != 2
            and not tournament_training
            and self.kind
            not in (
                ExperimentKind.RULE_SIMULATION,
                ExperimentKind.TOURNAMENT_BENCHMARK,
            )
        ):
            raise ValueError("only rule simulations and tournaments support more than two players")
        if tournament_training:
            from tableforge.poker.tournament import TournamentConfig

            tournament = TournamentConfig.from_json(self.tournament_json)
            if tournament.players != self.players:
                raise ValueError("players must match the tournament")
            if self.observation_schema == "poker-hunlhe-vector-v1":
                raise ValueError("tournament training needs the v2, v3 or v4 observation schema")
            seats = 9 if self.observation_schema in NINE_SEAT_SCHEMAS else 6
            if tournament.stage is not None and self.observation_schema != V4_OBSERVATION_SCHEMA:
                raise ValueError(
                    "MTT stage training needs the v4 observation schema, which sees the field"
                )
            if self.sampler_json:
                from tableforge.poker.tournament_sampler import TournamentSampler

                sampler = TournamentSampler.from_json(self.sampler_json)
                if self.observation_schema not in NINE_SEAT_SCHEMAS:
                    raise ValueError(
                        "training on many structures needs the v3 or v4 observation schema, "
                        "which sees the structure"
                    )
                if sampler.stages and self.observation_schema != V4_OBSERVATION_SCHEMA:
                    raise ValueError("a sampler with MTT stages needs the v4 observation schema")
                if sampler.max_players > seats:
                    raise ValueError("the sampler has more players than the encoder has seats")
            if tournament.players > seats:
                raise ValueError(f"{self.observation_schema} has seats for at most {seats}")
        elif self.sampler_json:
            raise ValueError("a tournament sampler needs tournament training")
        if not 0 <= self.self_play_share <= 1:
            raise ValueError("self_play_share must be between 0 and 1")
        if self.rl_capacity < 1 or self.sl_capacity < 1:
            raise ValueError("buffer capacities must be positive")
        if self.pfsp not in ("uniform", "hard", "variance"):
            raise ValueError("pfsp must be uniform, hard or variance")
        if self.pool_history < 0 or self.pool_eval_seeds < 0:
            raise ValueError("pool settings cannot be negative")
        if (self.pool_models or self.pool_history or self.pool_eval_seeds) and not (
            tournament_training
        ):
            raise ValueError("opponent pools are only available for tournament training")
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
        if self.reward_scale <= 0:
            raise ValueError("reward_scale must be positive")
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
    reward_scale: float = 1.0
    benchmark_deals: int = 0
    group_id: str = ""
    self_play_share: float = 0.5
    hand_horizon: bool = False
    double_q: bool = False
    rl_capacity: int = 50_000
    sl_capacity: int = 100_000
    pool_models: str = ""
    pool_history: int = 0
    pfsp: str = "uniform"
    pool_eval_seeds: int = 0
    exploit_target: str = ""
    algorithm: str = "nfsp"
    action_schema: str = "nlhe-discrete-v1"
    sampler_json: str = ""
    entropy_coef: float = 0.01
    gae_lambda: float = 0.95
    clip: float = 0.2
    magnet_coef: float = 0.0
    magnet_every: int = 10

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
            reward_scale=self.reward_scale,
            benchmark_deals=self.benchmark_deals,
            group_id=self.group_id,
            self_play_share=self.self_play_share,
            hand_horizon=self.hand_horizon,
            double_q=self.double_q,
            rl_capacity=self.rl_capacity,
            sl_capacity=self.sl_capacity,
            pool_models=self.pool_models,
            pool_history=self.pool_history,
            pfsp=self.pfsp,
            pool_eval_seeds=self.pool_eval_seeds,
            exploit_target=self.exploit_target,
            algorithm=self.algorithm,
            action_schema=self.action_schema,
            sampler_json=self.sampler_json,
            entropy_coef=self.entropy_coef,
            gae_lambda=self.gae_lambda,
            clip=self.clip,
            magnet_coef=self.magnet_coef,
            magnet_every=self.magnet_every,
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
        reward_scale=100.0,
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


def _tournament_training_preset(
    key: str, stack_bb: float = 10, payout: str = "65-35", **overrides: object
) -> ExperimentPreset:
    """NFSP on a 6-max PKO with the settings accepted in T3 (see docs/status.md).

    Only the tournament differs between these presets (starting stack, payout); the
    tournament of any tournament preset can also be replaced when an experiment is created.

    Each round: `actors_per_round` actors play `hands_per_job` training tournaments with the
    current checkpoint, one learner trains `train_steps_per_round` steps on them, then the
    new checkpoint is benchmarked on `evaluation_deals` x 6 tournaments against a mixed
    rule-bot field (average policy, best response and the previous checkpoint).
    """
    from tableforge.poker.tournament import PAYOUTS
    from tableforge.poker.tournament_spec import TournamentSpec

    tournament = TournamentSpec(starting_stack_bb=stack_bb, payout=PAYOUTS[payout]).to_config()
    settings: dict[str, object] = {
        "hands": 750,
        "hands_per_job": 750,
        "kind": ExperimentKind.NFSP_ITERATIVE,
        "players": 6,
        "tournament_json": tournament.to_json(),
        "opponents": "mixed",
        "rounds": 20,
        "actors_per_round": 4,
        "train_steps_per_round": 31_000,
        "evaluation_deals": 300,
        "max_evaluation_deals": 300,
        "hidden_size": 128,
        "batch_size": 128,
        "observation_schema": "poker-hunlhe-vector-v2",
        "reward_scale": 0.3,
        "double_q": True,
        "hand_horizon": True,
        "rl_capacity": 200_000,
        "sl_capacity": 1_000_000,
        "self_play_share": 0.75,
    }
    settings.update(overrides)
    return ExperimentPreset(key, "1", **settings)  # type: ignore[arg-type]


def _tournament_preset(
    stack_bb: int, payout: str, suffix: str, pko: bool = False, candidate: str = "pushfold"
) -> ExperimentPreset:
    """A rule bot in a mixed rule-bot field: 500 seeds x 6 seats = 3,000 tournaments.

    PKO presets use 50% of the buy-in as bounty, half of each knockout paid in cash.
    """
    from tableforge.poker.tournament import BountyConfig, preset_config

    kind = "pko" if pko else "sng"
    name = f"6max-{kind}-{stack_bb}bb{suffix}"
    if candidate != "pushfold":
        name += f"-{candidate}"
    return ExperimentPreset(
        name,
        "1",
        hands=500,
        hands_per_job=50,
        kind=ExperimentKind.TOURNAMENT_BENCHMARK,
        players=6,
        tournament_json=preset_config(
            stack_bb, payout, bounty=BountyConfig() if pko else None
        ).to_json(),
        candidate=candidate,
        opponents="mixed",
    )


def _default_sampler() -> str:
    """Structures for multi-structure training: 2-9 players, 8-60 BB, varied blinds."""
    from tableforge.poker.tournament_sampler import TournamentSampler

    return TournamentSampler(players=(2, 3, 4, 5, 6, 7, 8, 9)).to_json()


def _stage_sampler() -> str:
    """v4 training: the v3 mix, and half of the tournaments are MTT stages (T5)."""
    from tableforge.poker.tournament_sampler import TournamentSampler

    return TournamentSampler(
        players=(2, 3, 4, 5, 6, 7, 8, 9),
        stages=("bubble", "itm", "final_tables"),
        stage_share=0.5,
    ).to_json()


def _stage_tournament(stage: str, stack_bb: float = 25, pko: bool = True) -> str:
    """A 9-handed table of a 1,000-player MTT at a named stage (`mtt_stage.STAGES`)."""
    from tableforge.poker.tournament_spec import TournamentSpec, parse_stage

    return (
        TournamentSpec(players=9, starting_stack_bb=stack_bb, pko=pko, stage=parse_stage(stage))
        .to_config()
        .to_json()
    )


def _stage_preset(stage: str, candidate: str) -> ExperimentPreset:
    """A rule bot at our table of an MTT stage, against the mixed field: 500 seeds x 9."""
    return ExperimentPreset(
        f"mtt-pko-{stage.replace('_', '-')}-{candidate}",
        "1",
        hands=500,
        hands_per_job=50,
        kind=ExperimentKind.TOURNAMENT_BENCHMARK,
        players=9,
        tournament_json=_stage_tournament(stage),
        candidate=candidate,
        opponents="mixed",
    )


BUILTIN_PRESETS = (
    BUILTIN_PRESETS
    + (
        _tournament_training_preset("6max-pko-10bb-train"),
        _tournament_training_preset("6max-pko-25bb-train", 25),
        _tournament_training_preset("6max-pko-50bb-train", 50),
        _tournament_training_preset("6max-pko-25bb-top3-train", 25, "50-30-20"),
        _tournament_training_preset(
            "6max-pko-10bb-smoke",
            hands=20,
            hands_per_job=20,
            rounds=2,
            actors_per_round=2,
            train_steps_per_round=100,
            evaluation_deals=10,
            max_evaluation_deals=10,
            hidden_size=32,
            batch_size=32,
            rl_capacity=20_000,
            sl_capacity=20_000,
        ),
        # S4: train only against a frozen target (set `exploit_target` when creating).
        # The tournament follows the target model; 10 BB PKO is only the default.
        _tournament_training_preset(
            "6max-pko-10bb-exploit",
            rounds=10,
            self_play_share=0.0,
            pool_eval_seeds=200,
            benchmark_deals=0,
        ),
        # v3 (S2/S3/T4): finer bet sizes, 2-9 players, and tournaments drawn from a range
        # of structures. The benchmark tournament (the curve) is 6-max PKO at 25 BB.
        _tournament_training_preset(
            "mixed-pko-v3-train",
            25,
            observation_schema=V3_OBSERVATION_SCHEMA,
            action_schema="nlhe-discrete-v2",
            sampler_json=_default_sampler(),
        ),
        _tournament_training_preset(
            "mixed-pko-v3-smoke",
            25,
            hands=20,
            hands_per_job=20,
            rounds=2,
            actors_per_round=2,
            train_steps_per_round=100,
            evaluation_deals=10,
            max_evaluation_deals=10,
            hidden_size=32,
            batch_size=32,
            rl_capacity=20_000,
            sl_capacity=20_000,
            observation_schema=V3_OBSERVATION_SCHEMA,
            action_schema="nlhe-discrete-v2",
            sampler_json=_default_sampler(),
        ),
    )
    + (
        # PPO (A1): the NFSP 10 BB preset's tournament, field and benchmark, so both are
        # judged on the same score card. Each round: 4 actors x 250 tournaments with the
        # current policy, then `train_steps_per_round` PPO epochs (minibatches of
        # `batch_size`) on that experience. Replay sizes do not apply.
        _tournament_training_preset(
            "6max-pko-10bb-ppo-train",
            algorithm="ppo",
            hands=250,
            hands_per_job=250,
            rounds=80,
            train_steps_per_round=4,
            batch_size=1_024,
            learning_rate=3e-4,
            double_q=False,
            rl_capacity=50_000,
            sl_capacity=100_000,
        ),
        _tournament_training_preset(
            "6max-pko-10bb-ppo-smoke",
            algorithm="ppo",
            hands=20,
            hands_per_job=20,
            rounds=2,
            actors_per_round=2,
            train_steps_per_round=2,
            evaluation_deals=10,
            max_evaluation_deals=10,
            hidden_size=32,
            batch_size=256,
            learning_rate=3e-4,
            double_q=False,
            rl_capacity=50_000,
            sl_capacity=100_000,
        ),
        # PPO with MMD: the same, plus a KL pull towards a magnet policy that becomes the
        # current policy every `magnet_every` rounds (keeps self-play from cycling).
        _tournament_training_preset(
            "6max-pko-10bb-mmd-train",
            algorithm="ppo",
            hands=250,
            hands_per_job=250,
            rounds=80,
            train_steps_per_round=4,
            batch_size=1_024,
            learning_rate=3e-4,
            double_q=False,
            rl_capacity=50_000,
            sl_capacity=100_000,
            magnet_coef=0.1,
            magnet_every=5,
        ),
        _tournament_training_preset(
            "6max-pko-10bb-mmd-smoke",
            algorithm="ppo",
            hands=20,
            hands_per_job=20,
            rounds=3,
            actors_per_round=2,
            train_steps_per_round=2,
            evaluation_deals=10,
            max_evaluation_deals=10,
            hidden_size=32,
            batch_size=256,
            learning_rate=3e-4,
            double_q=False,
            rl_capacity=50_000,
            sl_capacity=100_000,
            magnet_coef=0.1,
            magnet_every=2,
        ),
    )
    + tuple(
        _tournament_preset(stack_bb, payout, suffix)
        for stack_bb in (10, 25, 50)
        for payout, suffix in (("65-35", ""), ("50-30-20", "-top3"))
    )
    + tuple(
        _tournament_preset(stack_bb, "65-35", "", pko=True, candidate=candidate)
        for stack_bb in (10, 25, 50)
        for candidate in ("pushfold", "hunter")
    )
    # The ICM Push/Fold Nash baseline in the same mixed field (short stacks only).
    + (
        _tournament_preset(10, "65-35", "", candidate="pushfold_nash"),
        _tournament_preset(10, "65-35", "", pko=True, candidate="pushfold_nash"),
    )
    # T5: our table inside a 1,000-player PKO at three stages (bubble, just in the money,
    # three tables left); 9-handed, 25 BB average.
    + tuple(
        _stage_preset(stage, candidate)
        for stage in ("bubble", "itm", "final_tables")
        for candidate in ("pushfold", "pushfold_nash")
    )
    + (
        # v4: the v3 mix plus MTT stages; the benchmark tournament is the bubble stage.
        _tournament_training_preset(
            "mtt-pko-v4-train",
            25,
            players=9,
            tournament_json=_stage_tournament("bubble"),
            observation_schema=V4_OBSERVATION_SCHEMA,
            action_schema="nlhe-discrete-v2",
            sampler_json=_stage_sampler(),
            sl_capacity=400_000,
        ),
        _tournament_training_preset(
            "mtt-pko-v4-smoke",
            25,
            players=9,
            tournament_json=_stage_tournament("bubble"),
            hands=20,
            hands_per_job=20,
            rounds=2,
            actors_per_round=2,
            train_steps_per_round=100,
            evaluation_deals=4,
            max_evaluation_deals=4,
            hidden_size=32,
            batch_size=32,
            rl_capacity=20_000,
            sl_capacity=20_000,
            observation_schema=V4_OBSERVATION_SCHEMA,
            action_schema="nlhe-discrete-v2",
            sampler_json=_stage_sampler(),
        ),
    )
)
