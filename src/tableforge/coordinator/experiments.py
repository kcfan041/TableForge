from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from tableforge.application.ports import ExperimentRepository
from tableforge.capability.workloads import BUILTIN_WORKLOADS
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
from tableforge.domain.learning import (
    EVALUATION_PAYLOAD_TYPE,
    TRAINING_PAYLOAD_TYPE,
    NfspEvaluationPayload,
    NfspTrainingPayload,
)
from tableforge.domain.simulation import SimulationJobPayload

_TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


def _profile(payload_type: str, key: str | None = None) -> WorkloadProfile:
    for profile in BUILTIN_WORKLOADS:
        if profile.payload_type == payload_type and (key is None or profile.key == key):
            return profile
    raise ValueError(f"no workload profile for {payload_type}")


@dataclass(frozen=True, slots=True)
class ExperimentService:
    experiments: ExperimentRepository
    coordinator: CoordinatorService

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
        else:
            self._create_simulation_jobs(experiment)
        return self.experiments.update_status(
            experiment_id,
            experiment.revision,
            ExperimentStatus.RUNNING,
            now,
        )

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
        if experiment.status in (ExperimentStatus.STOPPED, ExperimentStatus.COMPLETED):
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
