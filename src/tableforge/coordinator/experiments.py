from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from tableforge.application.ports import CheckpointRepository, ExperimentRepository
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.nfsp_iterative import (
    IterativeNfspOrchestrator,
    PipelineFailedError,
)
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.capabilities import WorkloadProfile
from tableforge.domain.experiments import (
    BUILTIN_PRESETS,
    Experiment,
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

_TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


def _profile(
    payload_type: str, key: str | None = None, runtime: str = "cpu"
) -> WorkloadProfile:
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

    def _orchestrator(self) -> IterativeNfspOrchestrator:
        if self.checkpoints is None:
            raise ValueError("iterative NFSP experiments require a checkpoint repository")
        return IterativeNfspOrchestrator(self.coordinator, self.checkpoints, _profile)

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

    def create(self, preset_key: str, now: datetime) -> Experiment:
        preset = _preset(preset_key)
        return self.experiments.create(new_experiment_id(), preset.config(), now)

    def start(self, experiment_id: ExperimentId, now: datetime) -> Experiment:
        experiment = self.experiments.get(experiment_id)
        if experiment.status is ExperimentStatus.RUNNING:
            return experiment
        if experiment.status is not ExperimentStatus.CREATED:
            raise ValueError(f"cannot start experiment in {experiment.status.value}")
        if experiment.config.kind is ExperimentKind.NFSP_PIPELINE:
            self._create_training_job(experiment)
        elif experiment.config.kind is ExperimentKind.NFSP_ITERATIVE:
            self._orchestrator()
        else:
            self._create_simulation_jobs(experiment)
        running = self.experiments.update_status(
            experiment_id,
            experiment.revision,
            ExperimentStatus.RUNNING,
            now,
        )
        if experiment.config.kind is ExperimentKind.NFSP_ITERATIVE:
            self._orchestrator().reconcile(running, now)
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
                    ).to_bytes(),
                    payload_type=profile.payload_type,
                    idempotency_key=self._key(experiment, f"segment:{segment}"),
                )
            )

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
                finished = self._orchestrator().reconcile(experiment, now)
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
        if (
            finished
            and jobs
            and all(job.status is JobStatus.SUCCEEDED for job in jobs)
        ):
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
        return self._orchestrator().matrix(experiment)

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


def _preset(key: str) -> ExperimentPreset:
    for preset in BUILTIN_PRESETS:
        if preset.key == key:
            return preset
    raise ValueError(f"unknown preset: {key}")
