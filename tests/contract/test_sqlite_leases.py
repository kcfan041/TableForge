from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.application.ports import LeaseConflictError, LeaseFencedError
from tableforge.domain.ids import NodeId, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType, ResourceRequest
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LEASE = timedelta(minutes=5)


def make_spec(priority: int = 0, max_attempts: int = 3) -> JobSpec:
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=priority,
        resources=ResourceRequest(required_role="simulation"),
        payload=b"payload",
        payload_type="tableforge.v1.SimulationJobPayload",
        idempotency_key=str(new_job_id()),
        max_attempts=max_attempts,
        created_at=NOW,
    )


def repository(tmp_path: Path) -> SQLiteJobRepository:
    return SQLiteJobRepository(tmp_path / "tableforge.db")


def test_claim_prefers_higher_priority(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    low = make_spec(priority=1)
    high = make_spec(priority=10)
    jobs.create(low)
    jobs.create(high)

    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)

    assert lease is not None
    assert lease.job.spec.job_id == high.job_id
    assert lease.job.status is JobStatus.LEASED
    assert lease.attempt_number == 1


def test_compatible_claim_skips_incompatible_high_priority_job(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    cpu = make_spec(priority=1)
    gpu = replace(
        make_spec(priority=10),
        resources=ResourceRequest(
            gpu_count=1,
            required_role="nfsp_learner",
            required_runtime="cuda",
        ),
    )
    jobs.create(cpu)
    jobs.create(gpu)

    lease = jobs.claim_compatible(
        NodeId("node-a"),
        NOW,
        LEASE,
        lambda job: job.spec.resources.required_runtime == "cpu",
    )

    assert lease is not None
    assert lease.job.spec.job_id == cpu.job_id
    assert jobs.get(gpu.job_id).status is JobStatus.QUEUED


def test_claim_returns_none_when_no_queued_job(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    jobs.create(make_spec())
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None

    assert jobs.claim_next(NodeId("node-b"), NOW, LEASE) is None


def test_concurrent_claim_has_one_winner(tmp_path: Path) -> None:
    database = tmp_path / "tableforge.db"
    SQLiteJobRepository(database).create(make_spec())

    def claim(node: str) -> object:
        return SQLiteJobRepository(database).claim_next(NodeId(node), NOW, LEASE)

    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = list(executor.map(claim, ("node-a", "node-b")))

    assert sum(lease is not None for lease in leases) == 1


def test_renew_extends_active_lease(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    renewed = jobs.renew(spec.job_id, lease.token, NOW + timedelta(minutes=1), LEASE)

    assert renewed.attempt_id == lease.attempt_id
    assert renewed.expires_at == NOW + timedelta(minutes=6)


def test_wrong_token_is_fenced(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = make_spec()
    jobs.create(spec)
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None

    with pytest.raises(LeaseFencedError):
        jobs.renew(spec.job_id, "wrong", NOW + timedelta(minutes=1), LEASE)


def test_expired_job_is_requeued_and_old_attempt_is_fenced(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = make_spec()
    jobs.create(spec)
    first = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert first is not None

    second = jobs.claim_next(NodeId("node-b"), NOW + LEASE, LEASE)

    assert second is not None
    assert second.job.spec.job_id == spec.job_id
    assert second.attempt_number == 2
    with pytest.raises(LeaseFencedError):
        jobs.complete(spec.job_id, first.token, NOW + LEASE, "old-result")


def test_job_fails_after_max_attempts_expire(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = make_spec(max_attempts=1)
    jobs.create(spec)
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None

    assert jobs.requeue_expired(NOW + LEASE) == 1
    assert jobs.get(spec.job_id).status is JobStatus.FAILED
    assert jobs.claim_next(NodeId("node-b"), NOW + LEASE, LEASE) is None


def test_complete_is_idempotent_for_same_result(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    first = jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=1), "result-a")
    second = jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=2), "result-a")

    assert first.status is JobStatus.SUCCEEDED
    assert second == first


def test_complete_conflicts_when_result_changes(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None
    jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=1), "result-a")

    with pytest.raises(LeaseConflictError):
        jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=2), "result-b")


def test_not_before_job_is_not_claimed_early(tmp_path: Path) -> None:
    jobs = repository(tmp_path)
    spec = replace(make_spec(), not_before=NOW + timedelta(hours=1))
    jobs.create(spec)

    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is None
    assert jobs.claim_next(NodeId("node-a"), NOW + timedelta(hours=1), LEASE) is not None
