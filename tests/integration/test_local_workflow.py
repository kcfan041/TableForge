from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository
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


class FailingExecutor:
    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        del payload_bytes, on_progress
        raise RuntimeError("simulated crash")


class CancelOnProgressExecutor:
    def __init__(self, service: CoordinatorService, job_id: str, clock: FakeClock) -> None:
        self._service = service
        self._job_id = job_id
        self._clock = clock

    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        del payload_bytes
        from tableforge.domain.ids import JobId

        self._service.cancel_job(JobId(self._job_id), self._clock.now())
        if on_progress is not None:
            on_progress(1)
        raise AssertionError("progress callback should stop cancelled execution")


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
        nodes=SQLiteNodeRepository(tmp_path / "tableforge.db"),
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


def test_executor_crash_is_reported_and_retried_immediately(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec(hands=10)
    service.create_job(spec)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    first = worker(service, clock, executor=FailingExecutor()).run_once()
    assert first is not None
    assert service.get_job(spec.job_id).status is JobStatus.QUEUED
    errors = service.job_attempt_errors(spec.job_id)
    assert len(errors) == 1
    assert errors[0].error_code == "EXECUTOR_ERROR"
    assert errors[0].retryable

    retried = worker(service, clock, node_id="node-b").run_once()

    assert retried is not None
    assert retried.attempt_number == 2
    assert service.get_job(spec.job_id).status is JobStatus.SUCCEEDED


def test_worker_acknowledges_cooperative_cancel(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec(hands=10)
    service.create_job(spec)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    executor = CancelOnProgressExecutor(service, str(spec.job_id), clock)

    lease = worker(service, clock, executor=executor).run_once()

    assert lease is not None
    assert service.get_job(spec.job_id).status is JobStatus.CANCELLED
    assert service.job_outputs(spec.job_id) == ()


def test_stale_registered_node_cannot_claim(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec(hands=10)
    service.create_job(spec)
    node_capability = capability("node-a")
    service.register_node(node_capability, datetime(2026, 1, 1, tzinfo=UTC))

    lease = service.claim_job(
        NodeId("node-a"),
        node_capability,
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=3),
        timedelta(minutes=5),
    )

    assert lease is None
    assert service.get_job(spec.job_id).status is JobStatus.QUEUED


def test_attempt_records_capability_and_workload_provenance(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    spec = simulation_spec(hands=10)
    service.create_job(spec)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))

    worker(service, clock).run_once()

    provenance = service.jobs.attempt_provenance(spec.job_id)
    assert len(provenance) == 1
    assert provenance[0].capability_revision == 1
    assert provenance[0].workload_key == "hu-rule-simulation"
    assert provenance[0].workload_version == "1"
    assert '"runtime": "cpu"' in provenance[0].execution_plan_json


def test_active_lease_reserves_node_resources(tmp_path: Path) -> None:
    service = coordinator(tmp_path)
    first = simulation_spec(hands=10)
    second = replace(
        simulation_spec(hands=10),
        job_id=new_job_id(),
        idempotency_key="simulation-second",
    )
    service.create_job(first)
    service.create_job(second)
    node_capability = replace(capability("node-a"), logical_cpu_cores=1)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service.register_node(node_capability, now)

    claimed = service.claim_job(
        NodeId("node-a"), node_capability, now, timedelta(minutes=5)
    )
    blocked = service.claim_job(
        NodeId("node-a"), node_capability, now, timedelta(minutes=5)
    )

    assert claimed is not None
    assert blocked is None
    reserved = service.jobs.reserved_resources(NodeId("node-a"))
    assert reserved.cpu_cores == 1


def test_simulation_executor_is_content_deterministic() -> None:
    payload = SimulationJobPayload(
        schema_version="1.0",
        hands=100,
        seed_start=42,
    ).to_bytes()

    assert SimulationExecutor().execute(payload) == SimulationExecutor().execute(payload)
