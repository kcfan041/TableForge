from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tableforge.application.ports import ExperimentRepository
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.experiments import (
    BUILTIN_PRESETS,
    Experiment,
    ExperimentPreset,
    ExperimentStatus,
)
from tableforge.domain.ids import ExperimentId, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType
from tableforge.domain.simulation import SimulationJobPayload


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
        source_revision = experiment.revision
        profile = BUILTIN_WORKLOADS[0]
        config = experiment.config
        for segment, hand_start in enumerate(
            range(0, config.hands, config.hands_per_job)
        ):
            hands = min(config.hands_per_job, config.hands - hand_start)
            self.coordinator.create_job(
                JobSpec(
                    schema_version="1.0",
                    job_id=new_job_id(),
                    experiment_id=experiment_id,
                    type=JobType.SIMULATION,
                    priority=1,
                    resources=profile.minimum,
                    payload=SimulationJobPayload(
                        schema_version="1.0",
                        hands=hands,
                        seed_start=config.seed_start + hand_start,
                    ).to_bytes(),
                    payload_type=profile.payload_type,
                    idempotency_key=(
                        f"experiment:{experiment_id}:revision:{source_revision}:segment:{segment}"
                    ),
                )
            )
        return self.experiments.update_status(
            experiment_id,
            source_revision,
            ExperimentStatus.RUNNING,
            now,
        )

    def status(self, experiment_id: ExperimentId, now: datetime) -> Experiment:
        experiment = self.experiments.get(experiment_id)
        jobs = self.coordinator.jobs.list_jobs(experiment_id)
        if (
            experiment.status is ExperimentStatus.RUNNING
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
            if job.status not in (
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            ):
                self.coordinator.cancel_job(job.spec.job_id, now)
        return self.experiments.update_status(
            experiment_id,
            experiment.revision,
            ExperimentStatus.STOPPED,
            now,
        )

    def list_experiments(self) -> tuple[Experiment, ...]:
        return self.experiments.list_experiments()


def _preset(key: str) -> ExperimentPreset:
    for preset in BUILTIN_PRESETS:
        if preset.key == key:
            return preset
    raise ValueError(f"unknown preset: {key}")
