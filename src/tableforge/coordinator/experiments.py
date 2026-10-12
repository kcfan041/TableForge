from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from tableforge.application.ports import CheckpointRepository, ExperimentRepository
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.nfsp_iterative import (
    IterativeNfspOrchestrator,
    PipelineFailedError,
    ppo_orchestrator,
)
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.capabilities import WorkloadProfile
from tableforge.domain.experiments import (
    BUILTIN_PRESETS,
    Experiment,
    ExperimentConfig,
    ExperimentKind,
    ExperimentPreset,
    ExperimentStatus,
)
from tableforge.domain.ids import ExperimentId, new_experiment_id, new_job_id
from tableforge.domain.jobs import Job, JobSpec, JobStatus, JobType
from tableforge.domain.league import MatrixCell
from tableforge.domain.learning import (
    EVALUATION_PAYLOAD_TYPE,
    TRAINING_PAYLOAD_TYPE,
    NfspEvaluationPayload,
    NfspTrainingPayload,
)
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.domain.tournament import TOURNAMENT_PAYLOAD_TYPE, TournamentJobPayload

_TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


def config_algorithm(config: ExperimentConfig) -> str:
    """The algorithm an experiment trains."""
    return config.algorithm


class Orchestrator(Protocol):
    """Drives an iterative training experiment: creates its jobs round by round."""

    def reconcile(self, experiment: Experiment, now: datetime) -> bool:
        """Create the jobs that can be created now; True when the experiment is finished."""
        ...

    def matrix(self, experiment: Experiment) -> dict[str, dict[str, MatrixCell]] | None: ...


OrchestratorFactory = Callable[
    [CoordinatorService, CheckpointRepository, Callable[..., WorkloadProfile], Path | None],
    Orchestrator,
]
ORCHESTRATORS: dict[str, OrchestratorFactory] = {
    "nfsp": IterativeNfspOrchestrator,
    "ppo": ppo_orchestrator,
}
"""The round pipeline of each training algorithm (`learning.algorithms` has the workers' side)."""


def _profile(payload_type: str, key: str | None = None, runtime: str = "cpu") -> WorkloadProfile:
    matches = [
        profile
        for profile in BUILTIN_WORKLOADS
        if profile.payload_type == payload_type and (key is None or profile.key == key)
    ]
    for profile in matches:
        if profile.minimum.required_runtime == runtime:
            return profile
    if matches and key is not None:
        return matches[0]
    raise ValueError(f"no {runtime} workload profile for {payload_type}")


@dataclass(frozen=True, slots=True)
class ExperimentService:
    experiments: ExperimentRepository
    coordinator: CoordinatorService
    checkpoints: CheckpointRepository | None = None
    models_root: Path | None = None

    def _orchestrator(self, algorithm: str = "nfsp") -> Orchestrator:
        """The round pipeline (actors, learner, evaluation) of a training algorithm."""
        if self.checkpoints is None:
            raise ValueError("iterative training experiments require a checkpoint repository")
        factory = ORCHESTRATORS.get(algorithm)
        if factory is None:
            raise ValueError(f"no training pipeline for algorithm {algorithm!r}")
        return factory(self.coordinator, self.checkpoints, _profile, self.models_root)

    def reconcile_all(self, now: datetime) -> int:
        advanced = 0
        for experiment in self.experiments.list_experiments():
            if experiment.status is ExperimentStatus.RUNNING:
                before = len(self.coordinator.jobs.list_jobs(experiment.experiment_id))
                after_experiment = self.status(experiment.experiment_id, now)
                after = len(self.coordinator.jobs.list_jobs(experiment.experiment_id))
                if after != before or after_experiment.status is not ExperimentStatus.RUNNING:
                    advanced += 1
        return advanced

    def create(
        self,
        preset_key: str,
        now: datetime,
        overrides: Mapping[str, object] | None = None,
    ) -> Experiment:
        """Create an experiment from a preset, optionally changing training settings."""
        config = self._resolve_exploit(
            apply_overrides(_preset(preset_key).config(), overrides or {})
        )
        return self.experiments.create(new_experiment_id(), config, now)

    def _resolve_exploit(self, config: ExperimentConfig) -> ExperimentConfig:
        """Check an exploitability experiment's target; a model target sets the tournament.

        ``rule:<bot>`` keeps the preset's (or the chosen) tournament; any other value is a
        model id, whose own tournament is used so the learner plays the game it was trained
        for.
        """
        target = config.exploit_target
        if config.preset_key.endswith("-exploit") and not target:
            raise ValueError("choose the model (or rule:<bot>) to exploit")
        if not target:
            return config
        if target.startswith("rule:"):
            from tableforge.learning.opponents_catalog import RULE_POOL

            if target[5:] not in (*RULE_POOL, "pushfold_nash"):
                raise ValueError(f"unknown rule bot: {target[5:]}")
            return config
        from tableforge.coordinator.analysis import tournament_json
        from tableforge.models import ModelLibrary
        from tableforge.poker.tournament import TournamentConfig

        if self.models_root is None:
            raise ValueError("this cluster has no model library")
        try:
            info = ModelLibrary(self.models_root).info(target)
        except KeyError as error:
            raise ValueError(f"no such model: {target}") from error
        if info.algorithm != config_algorithm(config):
            # The learner starts as a copy of the target, so both use the same algorithm.
            raise ValueError(
                f"model {target} uses {info.algorithm}; this experiment trains "
                f"{config_algorithm(config)}"
            )
        tournament = TournamentConfig.from_json(tournament_json(info))
        return replace(config, tournament_json=tournament.to_json(), players=tournament.players)

    def create_group(
        self,
        preset_key: str,
        seeds: Sequence[int],
        now: datetime,
        overrides: Mapping[str, object] | None = None,
    ) -> list[Experiment]:
        """One experiment per seed, sharing a group id so they can be compared side by side."""
        if not 1 <= len(seeds) <= 16 or len(set(seeds)) != len(seeds):
            raise ValueError("a group needs 1 to 16 different seeds")
        group_id = str(new_experiment_id())
        base = self._resolve_exploit(apply_overrides(_preset(preset_key).config(), overrides or {}))
        return [
            self.experiments.create(
                new_experiment_id(),
                replace(base, seed_start=int(seed), group_id=group_id),
                now,
            )
            for seed in seeds
        ]

    def training_curve(self, experiment_id: ExperimentId) -> list[dict[str, object]]:
        """Per-round evaluation of an iterative NFSP experiment, in round order."""
        experiment = self.experiments.get(experiment_id)
        if experiment.config.kind is not ExperimentKind.NFSP_ITERATIVE:
            return []
        rounds: list[dict[str, object]] = []
        prefix = f"experiment:{experiment_id}:round:"
        for job in self.coordinator.jobs.list_jobs(experiment_id):
            key = job.spec.idempotency_key
            if not (key.startswith(prefix) and key.endswith(":match")):
                continue
            if job.status is not JobStatus.SUCCEEDED:
                continue
            outputs = self.coordinator.job_outputs(job.spec.job_id)
            if not outputs:
                continue
            result = json.loads(
                self.coordinator.read_artifact(str(outputs[0].artifact.descriptor.artifact_id))
            )
            round_index = int(key[len(prefix) :].split(":", 1)[0])
            references = result.get("references", {})
            rounds.append(
                {
                    "round": round_index,
                    "unit": result.get("reference_unit", "bb_per_100"),
                    "tournament": result.get("tournament"),
                    "train_steps": result.get("candidate_train_steps"),
                    "head_to_head": result.get("head_to_head"),
                    "benchmark": {
                        name: {
                            "bb_per_100": values["bb_per_100"],
                            "low": values["confidence_low"],
                            "high": values["confidence_high"],
                            "hands": values["hands"],
                        }
                        for name, values in references.items()
                    },
                }
            )
        return sorted(rounds, key=lambda item: int(item["round"]))  # type: ignore[call-overload]

    def start(self, experiment_id: ExperimentId, now: datetime) -> Experiment:
        experiment = self.experiments.get(experiment_id)
        if experiment.status is ExperimentStatus.RUNNING:
            return experiment
        if experiment.status is not ExperimentStatus.CREATED:
            raise ValueError(f"cannot start experiment in {experiment.status.value}")
        if experiment.config.kind is ExperimentKind.NFSP_PIPELINE:
            self._create_training_job(experiment)
        elif experiment.config.kind is ExperimentKind.NFSP_ITERATIVE:
            self._orchestrator(experiment.config.algorithm)
        elif experiment.config.kind is ExperimentKind.TOURNAMENT_BENCHMARK:
            self._create_tournament_jobs(experiment)
        else:
            self._create_simulation_jobs(experiment)
        running = self.experiments.update_status(
            experiment_id,
            experiment.revision,
            ExperimentStatus.RUNNING,
            now,
        )
        if experiment.config.kind is ExperimentKind.NFSP_ITERATIVE:
            self._orchestrator(running.config.algorithm).reconcile(running, now)
        return running

    def _create_simulation_jobs(self, experiment: Experiment) -> None:
        profile = _profile("tableforge.v1.SimulationJobPayload")
        config = experiment.config
        for segment, hand_start in enumerate(range(0, config.hands, config.hands_per_job)):
            hands = min(config.hands_per_job, config.hands - hand_start)
            self.coordinator.create_job(
                JobSpec(
                    schema_version="1.0",
                    job_id=new_job_id(),
                    experiment_id=experiment.experiment_id,
                    type=JobType.SIMULATION,
                    priority=1,
                    resources=profile.minimum,
                    payload=SimulationJobPayload(
                        schema_version="1.0",
                        hands=hands,
                        seed_start=config.seed_start + hand_start,
                        players=config.players,
                    ).to_bytes(),
                    payload_type=profile.payload_type,
                    idempotency_key=self._key(experiment, f"segment:{segment}"),
                )
            )

    def _create_tournament_jobs(self, experiment: Experiment) -> None:
        profile = _profile(TOURNAMENT_PAYLOAD_TYPE)
        config = experiment.config
        for segment, seed_offset in enumerate(range(0, config.hands, config.hands_per_job)):
            seeds = min(config.hands_per_job, config.hands - seed_offset)
            self.coordinator.create_job(
                JobSpec(
                    schema_version="1.0",
                    job_id=new_job_id(),
                    experiment_id=experiment.experiment_id,
                    type=JobType.EVALUATION,
                    priority=1,
                    resources=profile.minimum,
                    payload=TournamentJobPayload(
                        "1.0",
                        config.tournament_json,
                        seeds,
                        config.seed_start + seed_offset,
                        config.candidate,
                        config.opponents,
                    ).to_bytes(),
                    payload_type=profile.payload_type,
                    idempotency_key=self._key(experiment, f"tournament:{segment}"),
                )
            )

    def tournament_result(self, experiment_id: ExperimentId) -> dict[str, object] | None:
        """Merge the samples of every finished tournament job into one benchmark summary."""
        experiment = self.experiments.get(experiment_id)
        if experiment.config.kind is not ExperimentKind.TOURNAMENT_BENCHMARK:
            return None
        from tableforge.poker.tournament import TournamentConfig
        from tableforge.poker.tournament_benchmark import TournamentBenchmark, TournamentSamples

        config = TournamentConfig.from_json(experiment.config.tournament_json)
        merged = TournamentSamples(players=config.players, buy_in=config.buy_in)
        finished = 0
        for job in self.coordinator.jobs.list_jobs(experiment_id):
            if job.spec.payload_type != TOURNAMENT_PAYLOAD_TYPE:
                continue
            if job.status is not JobStatus.SUCCEEDED:
                continue
            outputs = self.coordinator.job_outputs(job.spec.job_id)
            if not outputs:
                continue
            content = self.coordinator.read_artifact(
                str(outputs[0].artifact.descriptor.artifact_id)
            )
            merged = merged.merge(TournamentSamples.from_json(content))
            finished += 1
        if finished == 0:
            return None
        summary = TournamentBenchmark.from_samples(merged, config.name).summary()
        summary.update(
            {
                "candidate": experiment.config.candidate,
                "opponents": experiment.config.opponents,
                "starting_stack_bb": config.starting_stack_bb,
                "payout": list(config.payout),
                "bounty": (
                    {
                        "starting_bounty": config.starting_bounty,
                        "cash_share": config.bounty.cash_share,
                        "winner_keeps_own": config.bounty.winner_keeps_own,
                    }
                    if config.bounty is not None
                    else None
                ),
                "jobs_finished": finished,
            }
        )
        return summary

    def _create_training_job(self, experiment: Experiment) -> None:
        profile = _profile(TRAINING_PAYLOAD_TYPE, "hu-nfsp-cpu-smoke")
        config = experiment.config
        self.coordinator.create_job(
            JobSpec(
                schema_version="1.0",
                job_id=new_job_id(),
                experiment_id=experiment.experiment_id,
                type=JobType.TRAINING,
                priority=1,
                resources=profile.minimum,
                payload=NfspTrainingPayload(
                    schema_version="1.0",
                    hands=config.hands,
                    seed_start=config.seed_start,
                    agent_seed=config.seed_start,
                ).to_bytes(),
                payload_type=profile.payload_type,
                idempotency_key=self._key(experiment, "train"),
            )
        )

    def _advance_pipeline(self, experiment: Experiment, jobs: tuple[Job, ...]) -> None:
        training = [job for job in jobs if job.spec.payload_type == TRAINING_PAYLOAD_TYPE]
        if not training or training[0].status is not JobStatus.SUCCEEDED:
            return
        if any(job.spec.payload_type == EVALUATION_PAYLOAD_TYPE for job in jobs):
            return
        outputs = self.coordinator.job_outputs(training[0].spec.job_id)
        if not outputs:
            raise AssertionError("succeeded training job must have a checkpoint output")
        checkpoint = outputs[0].artifact
        profile = _profile(EVALUATION_PAYLOAD_TYPE)
        self.coordinator.create_job(
            JobSpec(
                schema_version="1.0",
                job_id=new_job_id(),
                experiment_id=experiment.experiment_id,
                type=JobType.EVALUATION,
                priority=1,
                resources=profile.minimum,
                payload=NfspEvaluationPayload(
                    schema_version="1.0",
                    checkpoint_artifact_id=str(checkpoint.descriptor.artifact_id),
                    checkpoint_sha256=checkpoint.sha256,
                    deals=experiment.config.evaluation_deals,
                    seed_start=experiment.config.seed_start + 10_000_000,
                ).to_bytes(),
                payload_type=profile.payload_type,
                idempotency_key=self._key(experiment, "evaluate"),
                inputs=(checkpoint,),
            )
        )

    def _key(self, experiment: Experiment, stage: str) -> str:
        return f"experiment:{experiment.experiment_id}:{stage}"

    def status(self, experiment_id: ExperimentId, now: datetime) -> Experiment:
        experiment = self.experiments.get(experiment_id)
        if experiment.status is not ExperimentStatus.RUNNING:
            return experiment
        if experiment.config.kind is ExperimentKind.NFSP_ITERATIVE:
            try:
                finished = self._orchestrator(experiment.config.algorithm).reconcile(
                    experiment, now
                )
            except PipelineFailedError:
                return self.experiments.update_status(
                    experiment_id, experiment.revision, ExperimentStatus.FAILED, now
                )
            if not finished:
                return experiment
            return self.experiments.update_status(
                experiment_id, experiment.revision, ExperimentStatus.COMPLETED, now
            )
        jobs = self.coordinator.jobs.list_jobs(experiment_id)
        if experiment.config.kind is ExperimentKind.NFSP_PIPELINE:
            self._advance_pipeline(experiment, jobs)
            jobs = self.coordinator.jobs.list_jobs(experiment_id)
            finished = any(job.spec.payload_type == EVALUATION_PAYLOAD_TYPE for job in jobs)
        else:
            finished = True
        if finished and jobs and all(job.status is JobStatus.SUCCEEDED for job in jobs):
            return self.experiments.update_status(
                experiment_id,
                experiment.revision,
                ExperimentStatus.COMPLETED,
                now,
            )
        return experiment

    def stop(self, experiment_id: ExperimentId, now: datetime) -> Experiment:
        experiment = self.experiments.get(experiment_id)
        if experiment.status in (
            ExperimentStatus.STOPPED,
            ExperimentStatus.COMPLETED,
            ExperimentStatus.FAILED,
        ):
            return experiment
        for job in self.coordinator.jobs.list_jobs(experiment_id):
            if job.status not in _TERMINAL:
                self.coordinator.cancel_job(job.spec.job_id, now)
        return self.experiments.update_status(
            experiment_id,
            experiment.revision,
            ExperimentStatus.STOPPED,
            now,
        )

    def list_experiments(self) -> tuple[Experiment, ...]:
        return self.experiments.list_experiments()

    def league_matrix(self, experiment_id: ExperimentId) -> dict[str, dict[str, MatrixCell]] | None:
        experiment = self.experiments.get(experiment_id)
        if not experiment.config.league:
            raise ValueError("experiment is not a league experiment")
        return self._orchestrator(experiment.config.algorithm).matrix(experiment)

    def evaluation_result(self, experiment_id: ExperimentId) -> dict[str, object] | None:
        for job in self.coordinator.jobs.list_jobs(experiment_id):
            if (
                job.spec.payload_type == EVALUATION_PAYLOAD_TYPE
                and job.status is JobStatus.SUCCEEDED
            ):
                outputs = self.coordinator.job_outputs(job.spec.job_id)
                if outputs:
                    content = self.coordinator.read_artifact(
                        str(outputs[0].artifact.descriptor.artifact_id)
                    )
                    result = json.loads(content)
                    if not isinstance(result, dict):
                        raise ValueError("evaluation result must be a JSON object")
                    return result
        return None


TRAINING_OVERRIDES: dict[str, type] = {
    "seed_start": int,
    "rounds": int,
    "actors_per_round": int,
    "hands_per_job": int,
    "train_steps_per_round": int,
    "hidden_size": int,
    "batch_size": int,
    "learning_rate": float,
    "anticipatory": float,
    "epsilon": float,
    "target_update_steps": int,
    "reward_scale": float,
    "self_play_share": float,
    "rl_capacity": int,
    "sl_capacity": int,
    "pool_models": str,
    "pool_history": int,
    "pfsp": str,
    "pool_eval_seeds": int,
    "exploit_target": str,
    "observation_schema": str,
    "action_schema": str,
    "benchmark_deals": int,
    "evaluation_deals": int,
    "max_evaluation_deals": int,
    "entropy_coef": float,
    "gae_lambda": float,
    "clip": float,
    "magnet_coef": float,
    "magnet_every": int,
    "value_mode": str,
}
"""Settings a user may change when creating an iterative training experiment."""

ALGORITHM_ONLY_SETTINGS: dict[str, tuple[str, ...]] = {
    "anticipatory": ("nfsp",),
    "epsilon": ("nfsp",),
    "target_update_steps": ("nfsp",),
    "rl_capacity": ("nfsp",),
    "sl_capacity": ("nfsp",),
    "exploit_target": ("nfsp",),
    "entropy_coef": ("ppo",),
    "gae_lambda": ("ppo",),
    "clip": ("ppo",),
    "magnet_coef": ("ppo",),
    "magnet_every": ("ppo",),
    "value_mode": ("ppo",),
}
"""Settings that only mean something for some algorithms (the UI shows them only there)."""

OVERRIDE_LIMITS: dict[str, tuple[float, float]] = {
    "rounds": (1, 200),
    "actors_per_round": (1, 64),
    "hands_per_job": (100, 1_000_000),
    "train_steps_per_round": (1, 1_000_000),
    "hidden_size": (8, 4_096),
    "batch_size": (8, 262_144),
    "target_update_steps": (1, 1_000_000),
    "reward_scale": (0.01, 10_000),
    "self_play_share": (0, 1),
    "rl_capacity": (1_000, 5_000_000),
    "sl_capacity": (1_000, 5_000_000),
    "pool_history": (0, 20),
    "pool_eval_seeds": (0, 5_000),
    "benchmark_deals": (0, 100_000),
    "evaluation_deals": (2, 100_000),
    "max_evaluation_deals": (2, 1_000_000),
    "entropy_coef": (0, 1),
    "gae_lambda": (0, 1),
    "clip": (0.01, 0.9),
    "magnet_coef": (0, 10),
    "magnet_every": (1, 1_000),
}


TOURNAMENT_FIELDS = ("mixed", "call", "tight", "aggro", "pushfold", "hunter", "pushfold_nash")


def is_tournament(config: ExperimentConfig) -> bool:
    """Tournament benchmarks and tournament NFSP training (both carry a tournament)."""
    return bool(config.tournament_json)


def apply_tournament(
    config: ExperimentConfig, tournament: Mapping[str, object] | None, opponents: str | None
) -> ExperimentConfig:
    """Replace the tournament of a tournament preset with a user-chosen specification.

    `tournament` is a `TournamentSpec` object (players, starting_stack_bb, payout, pko,
    bounty split, blind structure...); omitted settings keep their spec defaults, not the
    preset's, so a request always says exactly which tournament it means. `opponents`
    changes the benchmark field.
    """
    if tournament is None and opponents is None:
        return config
    if not is_tournament(config):
        raise ValueError("tournament settings are only available for tournament presets")
    from tableforge.poker.tournament_spec import TournamentSpec

    changes: dict[str, Any] = {}
    if tournament is not None:
        if not isinstance(tournament, Mapping):
            raise ValueError("tournament must be an object")
        tournament_config = TournamentSpec.from_dict(dict(tournament)).to_config()
        changes["tournament_json"] = tournament_config.to_json()
        changes["players"] = tournament_config.players
    if opponents is not None:
        if opponents not in TOURNAMENT_FIELDS:
            raise ValueError(f"opponents must be one of {', '.join(TOURNAMENT_FIELDS)}")
        changes["opponents"] = opponents
    return replace(config, **changes)


def apply_overrides(config: ExperimentConfig, overrides: Mapping[str, object]) -> ExperimentConfig:
    """Return `config` with user-chosen training settings; unknown or out-of-range values fail."""
    overrides = dict(overrides)
    tournament = overrides.pop("tournament", None)
    opponents = overrides.pop("opponents", None)
    sampler = overrides.pop("sampler", None)
    config = apply_tournament(
        config,
        tournament,  # type: ignore[arg-type]
        None if opponents is None else str(opponents),
    )
    if sampler is not None:
        from tableforge.poker.tournament_sampler import TournamentSampler

        if config.kind is not ExperimentKind.NFSP_ITERATIVE or not config.tournament_json:
            raise ValueError("a tournament sampler needs a tournament training preset")
        if sampler == {} or sampler is False:
            config = replace(config, sampler_json="")
        else:
            if not isinstance(sampler, Mapping):
                raise ValueError("sampler must be an object")
            parsed = TournamentSampler.from_dict(dict(sampler))
            config = replace(config, sampler_json=parsed.to_json())
    if not overrides:
        return replace(config)
    if config.kind is not ExperimentKind.NFSP_ITERATIVE:
        raise ValueError("custom settings are only available for iterative NFSP presets")
    unknown = set(overrides) - set(TRAINING_OVERRIDES)
    if unknown:
        raise ValueError(f"these settings cannot be changed: {', '.join(sorted(unknown))}")
    changes: dict[str, Any] = {}
    for name, value in overrides.items():
        kind = TRAINING_OVERRIDES[name]
        try:
            converted = kind(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be a {kind.__name__}") from error
        limits = OVERRIDE_LIMITS.get(name)
        if limits is not None and not limits[0] <= converted <= limits[1]:
            raise ValueError(f"{name} must be between {limits[0]:g} and {limits[1]:g}")
        changes[name] = converted
    updated = replace(config, **changes)
    if updated.max_evaluation_deals < updated.evaluation_deals:
        updated = replace(updated, max_evaluation_deals=updated.evaluation_deals)
    return updated


def _preset(key: str) -> ExperimentPreset:
    for preset in BUILTIN_PRESETS:
        if preset.key == key:
            return preset
    raise ValueError(f"unknown preset: {key}")
