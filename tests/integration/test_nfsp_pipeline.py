from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.capability.workloads import BUILTIN_WORKLOADS  # noqa: E402
from tableforge.coordinator.experiments import ExperimentService  # noqa: E402
from tableforge.coordinator.service import CoordinatorService  # noqa: E402
from tableforge.domain.capabilities import (  # noqa: E402
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.experiments import (  # noqa: E402
    ExperimentConfig,
    ExperimentKind,
    ExperimentStatus,
)
from tableforge.domain.ids import NodeId, new_experiment_id  # noqa: E402
from tableforge.domain.jobs import JobStatus  # noqa: E402
from tableforge.domain.learning import (  # noqa: E402
    EVALUATION_PAYLOAD_TYPE,
    TRAINING_PAYLOAD_TYPE,
)
from tableforge.infrastructure.local_artifacts import LocalArtifactStore  # noqa: E402
from tableforge.infrastructure.sqlite.experiments import (  # noqa: E402
    SQLiteExperimentRepository,
)
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository  # noqa: E402
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository  # noqa: E402
from tableforge.learning.executors import (  # noqa: E402
    NfspEvaluationExecutor,
    NfspTrainingExecutor,
)
from tableforge.worker.runtime import (  # noqa: E402
    OutputSpec,
    RegisteredExecutor,
    WorkerRuntime,
)
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def worker(service: CoordinatorService) -> WorkerRuntime:
    training = NfspTrainingExecutor()
    evaluation = NfspEvaluationExecutor(service.read_artifact)
    node_id = NodeId("learner")
    return WorkerRuntime(
        node_id=node_id,
        capability=NodeCapability(
            schema_version="1.0",
            node_id=node_id,
            revision=1,
            operating_system="test",
            architecture="x86_64",
            logical_cpu_cores=8,
            physical_cpu_cores=4,
            total_memory_bytes=32 * 1024**3,
            available_memory_bytes=32 * 1024**3,
            gpus=(),
            storage=(StorageCapability("data", 100 * 1024**3),),
            runtime=RuntimeCapability("3.12", ("cpu",)),
            enabled_roles=("simulation", "evaluation", "nfsp_learner"),
            captured_at=NOW,
        ),
        coordinator=service,
        clock=Clock(),
        lease_duration=timedelta(minutes=30),
        simulation_executor=SimulationExecutor(),
        extra_executors={
            TRAINING_PAYLOAD_TYPE: RegisteredExecutor(
                training,
                OutputSpec(
                    training.output_kind, training.output_media_type, training.output_schema
                ),
            ),
            EVALUATION_PAYLOAD_TYPE: RegisteredExecutor(
                evaluation,
                OutputSpec(
                    evaluation.output_kind, evaluation.output_media_type, evaluation.output_schema
                ),
            ),
        },
    )


def test_nfsp_pipeline_trains_then_evaluates(tmp_path: Path) -> None:
    database = tmp_path / "tableforge.db"
    service = CoordinatorService(
        jobs=SQLiteJobRepository(database),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
        nodes=SQLiteNodeRepository(database),
    )
    experiments = ExperimentService(SQLiteExperimentRepository(database), service)
    repository = SQLiteExperimentRepository(database)
    created = repository.create(
        new_experiment_id(),
        ExperimentConfig(
            schema_version="1.0",
            preset_key="test-nfsp",
            preset_version="1",
            hands=300,
            hands_per_job=300,
            seed_start=0,
            kind=ExperimentKind.NFSP_PIPELINE,
            evaluation_deals=20,
        ),
        NOW,
    )
    experiments.start(created.experiment_id, NOW)
    runtime = worker(service)

    assert runtime.run_once() is not None
    assert experiments.status(created.experiment_id, NOW).status is ExperimentStatus.RUNNING
    jobs = service.jobs.list_jobs(created.experiment_id)
    assert [job.spec.payload_type for job in jobs].count(EVALUATION_PAYLOAD_TYPE) == 1

    assert runtime.run_once() is not None
    final = experiments.status(created.experiment_id, NOW)
    result = experiments.evaluation_result(created.experiment_id)

    assert final.status is ExperimentStatus.COMPLETED
    jobs = service.jobs.list_jobs(created.experiment_id)
    assert all(job.status is JobStatus.SUCCEEDED for job in jobs)
    assert isinstance(result, dict)
    assert set(result["opponents"]) == {"call", "random"}
    for summary in result["opponents"].values():
        assert summary["hands"] == 40
        assert summary["confidence_low"] <= summary["bb_per_100"] <= summary["confidence_high"]
    assert json.dumps(result)


def test_training_executor_output_is_deterministic() -> None:
    from tableforge.domain.learning import NfspTrainingPayload

    payload = NfspTrainingPayload("1.0", hands=120, seed_start=3, agent_seed=3).to_bytes()

    assert NfspTrainingExecutor().execute(payload) == NfspTrainingExecutor().execute(payload)
