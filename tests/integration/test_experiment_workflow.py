from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.experiments import ExperimentService
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.capabilities import (
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.experiments import ExperimentStatus
from tableforge.domain.ids import NodeId
from tableforge.domain.jobs import JobStatus
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.experiments import SQLiteExperimentRepository
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository
from tableforge.worker.loop import WorkerLoop
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def services(tmp_path: Path) -> tuple[CoordinatorService, ExperimentService]:
    database = tmp_path / "tableforge.db"
    coordinator = CoordinatorService(
        jobs=SQLiteJobRepository(database),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
        nodes=SQLiteNodeRepository(database),
    )
    return coordinator, ExperimentService(
        SQLiteExperimentRepository(database), coordinator
    )


def capability() -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId("node-a"),
        revision=1,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=4,
        physical_cpu_cores=2,
        total_memory_bytes=8 * 1024**3,
        available_memory_bytes=8 * 1024**3,
        gpus=(),
        storage=(StorageCapability("data", 20 * 1024**3),),
        runtime=RuntimeCapability("3.12", ("cpu",)),
        enabled_roles=("simulation",),
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_experiment_runs_to_completion_across_service_restart(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator, experiments = services(tmp_path)
    created = experiments.create("hu-rule-smoke", now)
    started = experiments.start(created.experiment_id, now)
    assert started.status is ExperimentStatus.RUNNING
    assert len(coordinator.jobs.list_jobs(created.experiment_id)) == 4

    restarted_coordinator, restarted_experiments = services(tmp_path)
    logger = CapturingLogger()
    runtime = WorkerRuntime(
        node_id=NodeId("node-a"),
        capability=capability(),
        coordinator=restarted_coordinator,
        clock=FixedClock(now),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    )
    processed = WorkerLoop(runtime, logger, idle_seconds=0).run(max_jobs=10)
    completed = restarted_experiments.status(created.experiment_id, now)

    assert processed == 4
    assert completed.status is ExperimentStatus.COMPLETED
    assert all(
        job.status is JobStatus.SUCCEEDED
        for job in restarted_coordinator.jobs.list_jobs(created.experiment_id)
    )
    assert [event for event, _ in logger.events].count("job_finished") == 4


def test_start_is_idempotent_and_stop_cancels_jobs(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator, experiments = services(tmp_path)
    created = experiments.create("hu-rule-smoke", now)
    first = experiments.start(created.experiment_id, now)
    second = experiments.start(created.experiment_id, now)

    assert first == second
    assert len(coordinator.jobs.list_jobs(created.experiment_id)) == 4

    stopped = experiments.stop(created.experiment_id, now)
    assert stopped.status is ExperimentStatus.STOPPED
    assert all(
        job.status is JobStatus.CANCELLED
        for job in coordinator.jobs.list_jobs(created.experiment_id)
    )
