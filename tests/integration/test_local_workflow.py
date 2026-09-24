from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.capabilities import (
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor


@dataclass
class FakeClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


class AdvancingClock:
    def __init__(self, value: datetime, step: timedelta) -> None:
        self.value = value
        self.step = step
        self.calls = 0

    def now(self) -> datetime:
        current = self.value
        self.value += self.step
        self.calls += 1
        return current


class FailingExecutor(SimulationExecutor):
    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        del payload_bytes, on_progress
        raise RuntimeError("simulated crash")


def capability(
    node_id: str,
    roles: tuple[str, ...] = ("simulation",),
) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId(node_id),
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
        enabled_roles=roles,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def simulation_spec(hands: int = 100) -> JobSpec:
    profile = BUILTIN_WORKLOADS[0]
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=1,
        resources=profile.minimum,
        payload=SimulationJobPayload(
            schema_version="1.0",
            hands=hands,
            seed_start=1000,
        ).to_bytes(),
        payload_type=profile.payload_type,
        idempotency_key="simulation-e2e",
    )


def coordinator(tmp_path: Path) -> CoordinatorService:
    return CoordinatorService(
        jobs=SQLiteJobRepository(tmp_path / "tableforge.db"),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
    )


def worker(
    service: CoordinatorService,
    clock: FakeClock,
    node_id: str = "node-a",
    executor: SimulationExecutor | None = None,
    roles: tuple[str, ...] = ("simulation",),
) -> WorkerRuntime:
    return WorkerRuntime(
        node_id=NodeId(node_id),
        capability=capability(node_id, roles),
        coordinator=service,
        clock=clock,
        lease_duration=timedelta(minutes=5),
        simulation_executor=executor or SimulationExecutor(),
    )


def test_simulation_job_runs_end_to_end(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec()
    service.create_job(spec)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    lease = worker(service, clock).run_once()

    assert lease is not None
    completed = service.get_job(spec.job_id)
    expected_output = SimulationExecutor().execute(spec.payload)
    expected_hash = hashlib.sha256(expected_output).hexdigest()
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.version == 2
    assert service.artifacts.exists(expected_hash)
    outputs = service.job_outputs(spec.job_id)
    assert len(outputs) == 1
    assert outputs[0].artifact.sha256 == expected_hash


def test_long_simulation_renews_lease(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec(hands=500)
    service.create_job(spec)
    clock = AdvancingClock(
        datetime(2026, 1, 1, tzinfo=UTC),
        timedelta(minutes=2),
    )
    runtime = WorkerRuntime(
        node_id=NodeId("node-a"),
        capability=capability("node-a"),
        coordinator=service,
        clock=clock,
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(progress_interval_hands=100),
        renew_margin=timedelta(minutes=2),
    )

    runtime.run_once()

    assert service.get_job(spec.job_id).status is JobStatus.SUCCEEDED
    assert clock.calls >= 6


def test_incompatible_worker_does_not_claim_job(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec()
    service.create_job(spec)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    lease = worker(service, clock, roles=("evaluation",)).run_once()

    assert lease is None
    assert service.get_job(spec.job_id).status is JobStatus.QUEUED


def test_executor_crash_leaves_job_for_expiry_and_retry(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec(hands=10)
    service.create_job(spec)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(RuntimeError, match="simulated crash"):
        worker(service, clock, executor=FailingExecutor()).run_once()
    assert service.get_job(spec.job_id).status is JobStatus.LEASED

    clock.value += timedelta(minutes=5)
    retried = worker(service, clock, node_id="node-b").run_once()

    assert retried is not None
    assert retried.attempt_number == 2
    assert service.get_job(spec.job_id).status is JobStatus.SUCCEEDED


def test_simulation_executor_is_content_deterministic() -> None:
    payload = SimulationJobPayload(
        schema_version="1.0",
        hands=100,
        seed_start=42,
    ).to_bytes()

    assert SimulationExecutor().execute(payload) == SimulationExecutor().execute(payload)
