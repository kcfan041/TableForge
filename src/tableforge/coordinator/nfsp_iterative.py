from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tableforge.application.ports import CheckpointRepository
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor, ArtifactKind
from tableforge.domain.capabilities import WorkloadProfile
from tableforge.domain.checkpoints import Checkpoint, CheckpointStatus
from tableforge.domain.experiments import Experiment, ExperimentConfig
from tableforge.domain.ids import (
    AgentId,
    ArtifactId,
    new_checkpoint_id,
    new_job_id,
)
from tableforge.domain.jobs import Job, JobSpec, JobStatus, JobType
from tableforge.domain.league import (
    MATCHUP_PAYLOAD_TYPE,
    POPULATION_SCHEMA,
    RULE_BOTS,
    HistoricalCheckpoint,
    LeagueMatchupPayload,
    LeagueMember,
    MatrixCell,
    MemberKind,
    build_matrix,
    build_population,
)
from tableforge.domain.learning import (
    ACTOR_PAYLOAD_TYPE,
    INIT_PAYLOAD_TYPE,
    LEARNER_PAYLOAD_TYPE,
    MATCH_PAYLOAD_TYPE,
    NfspActorPayload,
    NfspInitPayload,
    NfspLearnerPayload,
    NfspMatchPayload,
)
from tableforge.domain.nfsp_pipeline import (
    SNAPSHOT_SCHEMA,
    promotion_decision,
    snapshot_bytes,
)


class PipelineFailedError(Exception):
    pass


def _match_payload(config: ExperimentConfig, **fields: Any) -> NfspMatchPayload:
    """The round's match job, with the Rule Bot benchmark used for the training curve.

    Experiments created before the curve existed (``benchmark_deals == 0``) keep the old
    reference evaluation so their job payloads, and idempotency, are unchanged.
    """
    if config.tournament_json:
        return NfspMatchPayload(
            **fields,
            tournament_json=config.tournament_json,
            tournament_field=config.opponents or "mixed",
        )
    if config.benchmark_deals > 0:
        return NfspMatchPayload(
            **fields,
            reference_opponents=RULE_BOTS,
            reference_deals=config.benchmark_deals,
        )
    return NfspMatchPayload(**fields)


def agent_id_for(experiment: Experiment) -> AgentId:
    """The learner of an experiment: ``<algorithm>:<experiment id>``."""
    return AgentId(f"{experiment.config.algorithm}:{experiment.experiment_id}")


def _key(experiment: Experiment, stage: str) -> str:
    return f"experiment:{experiment.experiment_id}:{stage}"


def _content_id(content: bytes) -> ArtifactId:
    return ArtifactId(f"content-{hashlib.sha256(content).hexdigest()}")


@dataclass(frozen=True, slots=True)
class IterativeNfspOrchestrator:
    coordinator: CoordinatorService
    checkpoints: CheckpointRepository
    profile_for: Callable[[str, str | None, str], WorkloadProfile]
    models_root: Path | None = None
    """The cluster's model library, for opponent pools of tournament training."""

    def reconcile(self, experiment: Experiment, now: datetime) -> bool:
        jobs = {
            job.spec.idempotency_key: job
            for job in self.coordinator.jobs.list_jobs(experiment.experiment_id)
        }
        failed = [job for job in jobs.values() if job.status is JobStatus.FAILED]
        if failed:
            raise PipelineFailedError(f"job {failed[0].spec.job_id} failed")
        agent = agent_id_for(experiment)
        if not self._ensure_initial(experiment, jobs, agent, now):
            return False
        for round_index in range(1, experiment.config.rounds + 1):
            if not self._advance_round(experiment, jobs, agent, round_index, now):
                return False
        if experiment.config.league:
            return self._advance_matrix(experiment, jobs, agent)
        return True

    def matrix_members(self, experiment: Experiment) -> tuple[LeagueMember, ...]:
        agent = agent_id_for(experiment)
        members = [
            LeagueMember(
                f"round-{item.round_index}",
                MemberKind.CHECKPOINT,
                artifact_id=str(item.artifact_id),
                sha256=item.sha256,
            )
            for item in self.checkpoints.list_checkpoints(agent)
            if item.status in (CheckpointStatus.ACTIVE, CheckpointStatus.RETIRED)
        ]
        members.extend(
            LeagueMember(f"rule:{rule}", MemberKind.RULE, rule=rule) for rule in RULE_BOTS
        )
        return tuple(members)

    def matrix(self, experiment: Experiment) -> dict[str, dict[str, MatrixCell]] | None:
        members = self.matrix_members(experiment)
        results = []
        for job in self.coordinator.jobs.list_jobs(experiment.experiment_id):
            if job.spec.payload_type != MATCHUP_PAYLOAD_TYPE:
                continue
            if job.status is not JobStatus.SUCCEEDED:
                return None
            results.append(self._json_output(job))
        if not results:
            return None
        return build_matrix(tuple(member.name for member in members), results)

    def _advance_matrix(self, experiment: Experiment, jobs: dict[str, Job], agent: AgentId) -> bool:
        del agent
        members = self.matrix_members(experiment)
        pending = False
        for first_index, first in enumerate(members):
            for second_index in range(first_index + 1, len(members)):
                second = members[second_index]
                if first.kind is MemberKind.RULE and second.kind is MemberKind.RULE:
                    continue
                key = _key(experiment, f"matrix:{first.name}:{second.name}")
                job = jobs.get(key)
                if job is None:
                    self._create(
                        experiment,
                        key,
                        JobType.EVALUATION,
                        MATCHUP_PAYLOAD_TYPE,
                        LeagueMatchupPayload(
                            "1.0",
                            first,
                            second,
                            experiment.config.matrix_deals,
                            experiment.config.seed_start
                            + 90_000_000
                            + (first_index * 64 + second_index) * 100_000,
                        ).to_bytes(),
                    )
                    pending = True
                elif job.status is not JobStatus.SUCCEEDED:
                    pending = True
        return not pending

    def _population(
        self, agent: AgentId, base: Checkpoint, round_index: int, limit: int
    ) -> Artifact:
        promoted = [
            item
            for item in self.checkpoints.list_checkpoints(agent)
            if item.status is CheckpointStatus.RETIRED and item.round_index < base.round_index
        ]
        historical = tuple(
            HistoricalCheckpoint(f"round-{item.round_index}", str(item.artifact_id), item.sha256)
            for item in promoted[-limit:]
        )
        content = build_population(round_index, base.sha256, historical).to_bytes()
        return self.coordinator.upload_artifact(
            ArtifactDescriptor(
                _content_id(content),
                ArtifactKind.LEAGUE_POPULATION,
                "application/json",
                POPULATION_SCHEMA,
            ),
            content,
        )

    def _json_output(self, job: Job) -> dict[str, object]:
        content = self.coordinator.read_artifact(str(self._output(job).descriptor.artifact_id))
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("job result must be a JSON object")
        return result

    def _ensure_initial(
        self,
        experiment: Experiment,
        jobs: dict[str, Job],
        agent: AgentId,
        now: datetime,
    ) -> bool:
        target = experiment.config.exploit_target
        if target and not target.startswith("rule:"):
            return self._ensure_exploit_start(experiment, jobs, agent, now)
        key = _key(experiment, "round:0:init")
        job = jobs.get(key)
        if job is None:
            self._create(
                experiment,
                key,
                JobType.TRAINING,
                INIT_PAYLOAD_TYPE,
                NfspInitPayload(
                    "1.0",
                    agent_seed=experiment.config.seed_start,
                    hidden_size=experiment.config.hidden_size,
                    batch_size=experiment.config.batch_size,
                    observation_schema=experiment.config.observation_schema,
                    learning_rate=experiment.config.learning_rate,
                    anticipatory=experiment.config.anticipatory,
                    epsilon=experiment.config.epsilon,
                    target_update_steps=experiment.config.target_update_steps,
                    reward_scale=experiment.config.reward_scale,
                    double_q=experiment.config.double_q,
                    rl_capacity=experiment.config.rl_capacity,
                    sl_capacity=experiment.config.sl_capacity,
                ).to_bytes(),
            )
            return False
        if job.status is not JobStatus.SUCCEEDED:
            return False
        checkpoint = self._register(experiment, agent, 0, None, self._output(job), now)
        if checkpoint.status is CheckpointStatus.CANDIDATE:
            self.checkpoints.decide(checkpoint.checkpoint_id, True, "initial checkpoint", now)
        return True

    def _ensure_exploit_start(
        self,
        experiment: Experiment,
        jobs: dict[str, Job],
        agent: AgentId,
        now: datetime,
    ) -> bool:
        """S4 with a model target: the learner starts as a copy of the target.

        A learner trained from scratch did not get close to exploiting the 10 BB models in
        60,000 tournaments, while the target's own best response already beats its average
        policy clearly (docs/status.md, S4). So round 0 is the target itself, and a round-0
        match measures that best response before any training.
        """
        config = experiment.config
        existing = self.checkpoints.active(agent)
        if existing is None:
            models = self._library_models(config.exploit_target, config.tournament_json)
            if not models:
                raise PipelineFailedError(
                    f"exploit target {config.exploit_target} is not in the model library"
                )
            source = self.coordinator.read_artifact(models[0].artifact_id)
            artifact = self.coordinator.upload_artifact(
                ArtifactDescriptor(
                    _content_id(source),
                    ArtifactKind.MODEL_CHECKPOINT,
                    "application/zip",
                    "tableforge.nfsp-checkpoint.v1",
                ),
                source,
            )
            existing = self._register(experiment, agent, 0, None, artifact, now)
            if existing.status is CheckpointStatus.CANDIDATE:
                self.checkpoints.decide(
                    existing.checkpoint_id, True, "exploit experiment: starts as the target", now
                )
        key = _key(experiment, "round:0:match")
        match = jobs.get(key)
        if match is None:
            pool = self._target_pool(config)
            self._create(
                experiment,
                key,
                JobType.EVALUATION,
                MATCH_PAYLOAD_TYPE,
                _match_payload(
                    config,
                    schema_version="1.0",
                    candidate_artifact_id=str(existing.artifact_id),
                    candidate_sha256=existing.sha256,
                    baseline_artifact_id=str(existing.artifact_id),
                    baseline_sha256=existing.sha256,
                    seed_start=config.seed_start + 50_000_000,
                    min_deals=config.evaluation_deals,
                    max_deals=config.max_evaluation_deals,
                    batch_deals=config.evaluation_deals,
                    target_width=config.target_ci_width,
                    pool_artifact_id=str(pool.descriptor.artifact_id),
                    pool_sha256=pool.sha256,
                    exploit_seeds=config.pool_eval_seeds or config.evaluation_deals,
                ).to_bytes(),
                inputs=(pool,),
            )
            return False
        return match.status is JobStatus.SUCCEEDED

    def _advance_round(
        self,
        experiment: Experiment,
        jobs: dict[str, Job],
        agent: AgentId,
        round_index: int,
        now: datetime,
    ) -> bool:
        config = experiment.config
        prefix = f"round:{round_index}"
        actor_keys = [
            _key(experiment, f"{prefix}:actor:{index}") for index in range(config.actors_per_round)
        ]
        if actor_keys[0] not in jobs:
            base = self.checkpoints.active(agent)
            if base is None:
                raise AssertionError("an active checkpoint must exist before a round")
            population: Artifact | None = None
            if config.league:
                population = self._population(agent, base, round_index, config.historical_limit)
            pool = self._opponent_pool(experiment, agent, base)
            for index, key in enumerate(actor_keys):
                seed = config.seed_start + round_index * 1_000_000 + index * 100_000
                self._create(
                    experiment,
                    key,
                    JobType.SIMULATION,
                    ACTOR_PAYLOAD_TYPE,
                    NfspActorPayload(
                        "1.0",
                        str(base.artifact_id),
                        base.sha256,
                        config.hands_per_job,
                        seed,
                        seed,
                        population_artifact_id=(
                            str(population.descriptor.artifact_id) if population else ""
                        ),
                        population_sha256=population.sha256 if population else "",
                        tournament_json=config.tournament_json,
                        self_play_share=config.self_play_share,
                        hand_horizon=config.hand_horizon,
                        pool_artifact_id=str(pool.descriptor.artifact_id) if pool else "",
                        pool_sha256=pool.sha256 if pool else "",
                    ).to_bytes(),
                    inputs=tuple(item for item in (population, pool) if item is not None),
                )
            return False
        actors = [jobs[key] for key in actor_keys if key in jobs]
        if len(actors) != len(actor_keys) or any(
            job.status is not JobStatus.SUCCEEDED for job in actors
        ):
            return False
        base_payload = NfspActorPayload.from_bytes(actors[0].spec.payload)
        base = self._checkpoint_by_sha(agent, base_payload.checkpoint_sha256)
        learn_key = _key(experiment, f"{prefix}:learn")
        learner = jobs.get(learn_key)
        if learner is None:
            snapshot = self._snapshot(base_payload.checkpoint_sha256, round_index, actors)
            self._create(
                experiment,
                learn_key,
                JobType.TRAINING,
                LEARNER_PAYLOAD_TYPE,
                NfspLearnerPayload(
                    "1.0",
                    base_payload.checkpoint_artifact_id,
                    base_payload.checkpoint_sha256,
                    str(snapshot.descriptor.artifact_id),
                    snapshot.sha256,
                    config.train_steps_per_round,
                    round_index,
                ).to_bytes(),
                inputs=(snapshot,),
                runtime=config.learner_runtime,
            )
            return False
        if learner.status is not JobStatus.SUCCEEDED:
            return False
        candidate = self._register(experiment, agent, round_index, base, self._output(learner), now)
        match_key = _key(experiment, f"{prefix}:match")
        match = jobs.get(match_key)
        if match is None:
            pool_fields: dict[str, Any] = {}
            if config.exploit_target and base_payload.pool_artifact_id:
                pool_fields = {
                    "pool_artifact_id": base_payload.pool_artifact_id,
                    "pool_sha256": base_payload.pool_sha256,
                    "exploit_seeds": config.pool_eval_seeds or config.evaluation_deals,
                }
            elif config.pool_eval_seeds and base_payload.pool_artifact_id:
                pool_fields = {
                    "pool_artifact_id": base_payload.pool_artifact_id,
                    "pool_sha256": base_payload.pool_sha256,
                    "pool_seeds": config.pool_eval_seeds,
                    "pool_matchup_seeds": (
                        max(config.pool_eval_seeds // 3, 2) if round_index == config.rounds else 0
                    ),
                }
            self._create(
                experiment,
                match_key,
                JobType.EVALUATION,
                MATCH_PAYLOAD_TYPE,
                _match_payload(
                    config,
                    **pool_fields,
                    schema_version="1.0",
                    candidate_artifact_id=str(candidate.artifact_id),
                    candidate_sha256=candidate.sha256,
                    baseline_artifact_id=str(base.artifact_id),
                    baseline_sha256=base.sha256,
                    # Tournaments use the same benchmark seeds every round, so the
                    # training curve compares rounds on identical tournaments.
                    seed_start=config.seed_start
                    + 50_000_000
                    + (0 if config.tournament_json else round_index * 1_000_000),
                    min_deals=config.evaluation_deals,
                    max_deals=config.max_evaluation_deals,
                    batch_deals=config.evaluation_deals,
                    target_width=config.target_ci_width,
                ).to_bytes(),
            )
            return False
        if match.status is not JobStatus.SUCCEEDED:
            return False
        if candidate.status is CheckpointStatus.CANDIDATE:
            result = self._match_result(match)
            if config.exploit_target:
                # A best-response learner specialises against its target; results against
                # the rule-bot field are not a reason to go back to an earlier version.
                promote, reason = True, "exploit experiment: keep the newest learner"
            else:
                promote, reason = promotion_decision(result)
            self.checkpoints.decide(candidate.checkpoint_id, promote, reason, now)
        return True

    def _opponent_pool(
        self, experiment: Experiment, agent: AgentId, base: Checkpoint
    ) -> Artifact | None:
        """The round's opponent pool: rule bots, library models and recent checkpoints."""
        config = experiment.config
        if config.exploit_target:
            return self._target_pool(config)
        if not config.tournament_json or not (config.pool_models or config.pool_history):
            return None
        from tableforge.domain.opponent_pool import OpponentPoolSpec, PoolModel
        from tableforge.learning.opponents_catalog import RULE_POOL

        models: list[PoolModel] = []
        if config.pool_models:
            models.extend(self._library_models(config.pool_models, config.tournament_json))
        if config.pool_history:
            earlier = [
                item
                for item in self.checkpoints.list_checkpoints(agent)
                if item.status in (CheckpointStatus.ACTIVE, CheckpointStatus.RETIRED)
                and item.round_index > 0
                and item.round_index <= base.round_index
            ][-config.pool_history :]
            models.extend(
                PoolModel(
                    f"history:round-{item.round_index}",
                    str(item.artifact_id),
                    item.sha256,
                    "history",
                )
                for item in earlier
            )
        content = OpponentPoolSpec(RULE_POOL, tuple(models), config.pfsp).to_bytes()
        from tableforge.domain.opponent_pool import POOL_SCHEMA

        return self.coordinator.upload_artifact(
            ArtifactDescriptor(
                _content_id(content),
                ArtifactKind.LEAGUE_POPULATION,
                "application/json",
                POOL_SCHEMA,
            ),
            content,
        )

    def _target_pool(self, config: ExperimentConfig) -> Artifact:
        """S4: a pool with only the exploit target (a library model or a rule bot)."""
        from tableforge.domain.opponent_pool import POOL_SCHEMA, OpponentPoolSpec

        target = config.exploit_target
        if target.startswith("rule:"):
            spec = OpponentPoolSpec((target[5:],))
        else:
            models = self._library_models(target, config.tournament_json)
            if not models:
                raise PipelineFailedError(f"exploit target {target} is not in the model library")
            spec = OpponentPoolSpec((), tuple(models))
        content = spec.to_bytes()
        return self.coordinator.upload_artifact(
            ArtifactDescriptor(
                _content_id(content),
                ArtifactKind.LEAGUE_POPULATION,
                "application/json",
                POOL_SCHEMA,
            ),
            content,
        )

    def _library_models(self, selection: str, tournament_json: str) -> list[Any]:
        from tableforge.domain.opponent_pool import PoolModel
        from tableforge.learning.pool_benchmark import same_tournament
        from tableforge.models import ModelLibrary
        from tableforge.poker.tournament import TournamentConfig

        if self.models_root is None:
            return []
        library = ModelLibrary(self.models_root)
        wanted = None if selection == "library" else set(selection.split(","))
        target = TournamentConfig.from_json(tournament_json)
        found = []
        for info in library.list():
            if wanted is not None and info.model_id not in wanted:
                continue
            from tableforge.learning.algorithms import algorithm, is_known

            if not is_known(info.algorithm):
                continue
            from tableforge.coordinator.analysis import AnalysisError
            from tableforge.coordinator.analysis import tournament_json as model_tournament

            try:
                model_config = TournamentConfig.from_json(model_tournament(info))
            except AnalysisError:
                continue
            if not same_tournament(model_config, target):
                continue
            content = library.checkpoint(info.model_id)
            artifact = self.coordinator.upload_artifact(
                ArtifactDescriptor(
                    _content_id(content),
                    ArtifactKind.MODEL_CHECKPOINT,
                    "application/zip",
                    algorithm(info.algorithm).checkpoint_schema,
                ),
                content,
            )
            found.append(
                PoolModel(
                    f"model:{info.model_id}",
                    str(artifact.descriptor.artifact_id),
                    artifact.sha256,
                    algorithm=info.algorithm,
                )
            )
        return found

    def _checkpoint_by_sha(self, agent: AgentId, sha256: str) -> Checkpoint:
        for checkpoint in self.checkpoints.list_checkpoints(agent):
            if checkpoint.sha256 == sha256:
                return checkpoint
        raise AssertionError("actor base checkpoint is not registered")

    def _snapshot(self, checkpoint_sha: str, round_index: int, actors: list[Job]) -> Artifact:
        shards = []
        for job in actors:
            output = self._output(job)
            shards.append((str(output.descriptor.artifact_id), output.sha256))
        content = snapshot_bytes(checkpoint_sha, round_index, shards)
        return self.coordinator.upload_artifact(
            ArtifactDescriptor(
                _content_id(content),
                ArtifactKind.DATASET_SNAPSHOT,
                "application/json",
                SNAPSHOT_SCHEMA,
            ),
            content,
        )

    def _register(
        self,
        experiment: Experiment,
        agent: AgentId,
        round_index: int,
        parent: Checkpoint | None,
        artifact: Artifact,
        now: datetime,
    ) -> Checkpoint:
        return self.checkpoints.register_candidate(
            Checkpoint(
                checkpoint_id=new_checkpoint_id(),
                agent_id=agent,
                experiment_id=experiment.experiment_id,
                round_index=round_index,
                parent_id=parent.checkpoint_id if parent else None,
                artifact_id=ArtifactId(str(artifact.descriptor.artifact_id)),
                sha256=artifact.sha256,
                status=CheckpointStatus.CANDIDATE,
                decision_reason="",
                created_at=now,
                updated_at=now,
            )
        )

    def _output(self, job: Job) -> Artifact:
        outputs = self.coordinator.job_outputs(job.spec.job_id)
        if not outputs:
            raise AssertionError(f"succeeded job {job.spec.job_id} has no output")
        return outputs[0].artifact

    def _match_result(self, job: Job) -> dict[str, object]:
        content = self.coordinator.read_artifact(str(self._output(job).descriptor.artifact_id))
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("match result must be a JSON object")
        return result

    def _create(
        self,
        experiment: Experiment,
        key: str,
        job_type: JobType,
        payload_type: str,
        payload: bytes,
        inputs: tuple[Artifact, ...] = (),
        runtime: str = "cpu",
    ) -> None:
        profile = self.profile_for(payload_type, None, runtime)
        self.coordinator.create_job(
            JobSpec(
                schema_version="1.0",
                job_id=new_job_id(),
                experiment_id=experiment.experiment_id,
                type=job_type,
                priority=1,
                resources=profile.minimum,
                payload=payload,
                payload_type=payload_type,
                idempotency_key=key,
                inputs=inputs,
            )
        )
