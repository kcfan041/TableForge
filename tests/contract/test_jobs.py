from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.application.ports import (
    IdempotencyConflictError,
    JobNotFoundError,
    LeaseConflictError,
    LeaseFencedError,
)
from tableforge.domain.ids import JobId, NodeId, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType, ResourceRequest
from tableforge.infrastructure.sql import Database, SqlJobRepository
from tests.contract.conftest import DatabaseFactory

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LEASE = timedelta(minutes=5)


def make_spec(priority: int = 0, max_attempts: int = 3) -> JobSpec:
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=priority,
        resources=ResourceRequest(cpu_cores=2, memory_bytes=1024, required_role="simulation"),
        payload=b"payload",
        payload_type="tableforge.v1.SimulationJobPayload",
        idempotency_key=str(new_job_id()),
        max_attempts=max_attempts,
        created_at=NOW,
    )


@pytest.fixture
def jobs(database: Database) -> SqlJobRepository:
    return SqlJobRepository(database)


def test_create_and_get_job(jobs: SqlJobRepository) -> None:
    spec = make_spec()

    created = jobs.create(spec)

    assert created.status is JobStatus.QUEUED
    assert jobs.get(spec.job_id) == created


def test_same_idempotency_request_returns_existing_job(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    first = jobs.create(spec)

    assert jobs.create(replace(spec, job_id=new_job_id())) == first


def test_changed_idempotency_request_conflicts(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)

    with pytest.raises(IdempotencyConflictError):
        jobs.create(replace(spec, job_id=new_job_id(), payload=b"different"))


def test_concurrent_idempotent_create_returns_one_job(
    database_factory: DatabaseFactory,
) -> None:
    spec = make_spec()
    database = database_factory()

    def create(index: int) -> JobId:
        return SqlJobRepository(database).create(replace(spec, job_id=new_job_id())).spec.job_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        created = set(executor.map(create, range(16)))

    assert len(created) == 1
    assert len(SqlJobRepository(database).list_jobs()) == 1


def test_missing_job_raises(jobs: SqlJobRepository) -> None:
    with pytest.raises(JobNotFoundError):
        jobs.get(JobId("missing"))


def test_binary_payload_round_trips(jobs: SqlJobRepository) -> None:
    spec = replace(make_spec(), payload=bytes(range(256)) * 4)
    jobs.create(spec)

    assert jobs.get(spec.job_id).spec.payload == spec.payload


def test_claim_prefers_higher_priority(jobs: SqlJobRepository) -> None:
    low = make_spec(priority=1)
    high = make_spec(priority=10)
    jobs.create(low)
    jobs.create(high)

    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)

    assert lease is not None
    assert lease.job.spec.job_id == high.job_id
    assert lease.job.status is JobStatus.LEASED
    assert lease.attempt_number == 1


def test_compatible_claim_skips_incompatible_high_priority_job(jobs: SqlJobRepository) -> None:
    cpu = make_spec(priority=1)
    gpu = replace(
        make_spec(priority=10),
        resources=ResourceRequest(
            gpu_count=1, required_role="nfsp_learner", required_runtime="cuda"
        ),
    )
    jobs.create(cpu)
    jobs.create(gpu)

    lease = jobs.claim_compatible(
        NodeId("node-a"), NOW, LEASE, lambda job: job.spec.resources.required_runtime == "cpu"
    )

    assert lease is not None
    assert lease.job.spec.job_id == cpu.job_id
    assert jobs.get(gpu.job_id).status is JobStatus.QUEUED


def test_compatible_claim_scans_past_first_batch(jobs: SqlJobRepository) -> None:
    blocked = [
        replace(make_spec(priority=10), resources=ResourceRequest(required_runtime="cuda"))
        for _ in range(70)
    ]
    for spec in blocked:
        jobs.create(spec)
    runnable = make_spec(priority=1)
    jobs.create(runnable)

    lease = jobs.claim_compatible(
        NodeId("node-a"), NOW, LEASE, lambda job: job.spec.resources.required_runtime == "cpu"
    )

    assert lease is not None and lease.job.spec.job_id == runnable.job_id


def test_claim_records_provenance(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)

    lease = jobs.claim_compatible(
        NodeId("node-a"),
        NOW,
        LEASE,
        lambda job: True,
        capability_revision=3,
        workload_identity=lambda job: ("hu-rule-simulation", "1"),
        execution_plan=lambda job: '{"runtime": "cpu"}',
    )

    assert lease is not None and lease.execution_plan_json == '{"runtime": "cpu"}'
    provenance = jobs.attempt_provenance(spec.job_id)
    assert provenance[0].capability_revision == 3
    assert provenance[0].workload_key == "hu-rule-simulation"
    assert jobs.reserved_resources(NodeId("node-a")).cpu_cores == 2


def test_claim_returns_none_when_no_queued_job(jobs: SqlJobRepository) -> None:
    jobs.create(make_spec())
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None

    assert jobs.claim_next(NodeId("node-b"), NOW, LEASE) is None


def test_concurrent_claims_never_share_a_job(database_factory: DatabaseFactory) -> None:
    database = database_factory()
    jobs = SqlJobRepository(database)
    specs = [make_spec() for _ in range(12)]
    for spec in specs:
        jobs.create(spec)

    def claim(index: int) -> JobId | None:
        lease = SqlJobRepository(database).claim_next(NodeId(f"node-{index}"), NOW, LEASE)
        return lease.job.spec.job_id if lease else None

    with ThreadPoolExecutor(max_workers=8) as executor:
        claimed = [job_id for job_id in executor.map(claim, range(20)) if job_id is not None]

    assert len(claimed) == len(specs)
    assert set(claimed) == {spec.job_id for spec in specs}


def test_renew_extends_active_lease(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    renewed = jobs.renew(spec.job_id, lease.token, NOW + timedelta(minutes=1), LEASE)

    assert renewed.attempt_id == lease.attempt_id
    assert renewed.expires_at == NOW + timedelta(minutes=6)
    assert renewed.leased_at == NOW


def test_wrong_token_is_fenced(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None

    with pytest.raises(LeaseFencedError):
        jobs.renew(spec.job_id, "wrong", NOW + timedelta(minutes=1), LEASE)


def test_expired_job_is_requeued_and_old_attempt_is_fenced(jobs: SqlJobRepository) -> None:
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


def test_job_fails_after_max_attempts_expire(jobs: SqlJobRepository) -> None:
    spec = make_spec(max_attempts=1)
    jobs.create(spec)
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None

    assert jobs.requeue_expired(NOW + LEASE) == 1
    assert jobs.get(spec.job_id).status is JobStatus.FAILED
    assert jobs.claim_next(NodeId("node-b"), NOW + LEASE, LEASE) is None


def test_complete_is_idempotent_for_same_result(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    first = jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=1), "result-a")
    second = jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=2), "result-a")

    assert first.status is JobStatus.SUCCEEDED
    assert second == first


def test_complete_conflicts_when_result_changes(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None
    jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=1), "result-a")

    with pytest.raises(LeaseConflictError):
        jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=2), "result-b")


def test_retryable_failure_requeues_and_records_error(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    failed = jobs.fail(
        spec.job_id,
        lease.token,
        NOW + timedelta(minutes=1),
        "TEMPORARY_IO",
        "temporary object store error",
        retryable=True,
    )

    assert failed.status is JobStatus.QUEUED
    errors = jobs.attempt_errors(spec.job_id)
    assert len(errors) == 1
    assert errors[0].error_code == "TEMPORARY_IO"
    assert errors[0].retryable
    assert errors[0].failed_at == NOW + timedelta(minutes=1)
    with pytest.raises(LeaseFencedError):
        jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=2), "late")


def test_non_retryable_failure_is_terminal(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    failed = jobs.fail(
        spec.job_id,
        lease.token,
        NOW + timedelta(minutes=1),
        "INVALID_SCHEMA",
        "payload schema is invalid",
        retryable=False,
    )

    assert failed.status is JobStatus.FAILED
    assert jobs.claim_next(NodeId("node-b"), NOW + timedelta(minutes=2), LEASE) is None


def test_retryable_failure_respects_max_attempts(jobs: SqlJobRepository) -> None:
    spec = make_spec(max_attempts=1)
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    failed = jobs.fail(
        spec.job_id, lease.token, NOW + timedelta(minutes=1), "TEMPORARY_IO", "x", True
    )

    assert failed.status is JobStatus.FAILED


def test_queued_job_cancels_immediately(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)

    cancelled = jobs.cancel(spec.job_id, NOW)

    assert cancelled.status is JobStatus.CANCELLED
    assert jobs.cancel(spec.job_id, NOW) == cancelled


def test_leased_job_uses_cooperative_cancellation(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW, LEASE)
    assert lease is not None

    requested = jobs.cancel(spec.job_id, NOW + timedelta(minutes=1))
    assert requested.status is JobStatus.CANCEL_REQUESTED
    with pytest.raises(LeaseConflictError):
        jobs.complete(spec.job_id, lease.token, NOW + timedelta(minutes=2), "result")

    cancelled = jobs.acknowledge_cancel(spec.job_id, lease.token, NOW + timedelta(minutes=2))
    assert cancelled.status is JobStatus.CANCELLED


def test_cancel_requested_job_expires_to_cancelled(jobs: SqlJobRepository) -> None:
    spec = make_spec()
    jobs.create(spec)
    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is not None
    jobs.cancel(spec.job_id, NOW + timedelta(minutes=1))

    assert jobs.requeue_expired(NOW + LEASE) == 1
    assert jobs.get(spec.job_id).status is JobStatus.CANCELLED


def test_not_before_job_is_not_claimed_early(jobs: SqlJobRepository) -> None:
    spec = replace(make_spec(), not_before=NOW + timedelta(hours=1))
    jobs.create(spec)

    assert jobs.claim_next(NodeId("node-a"), NOW, LEASE) is None
    assert jobs.claim_next(NodeId("node-a"), NOW + timedelta(hours=1), LEASE) is not None


def test_timestamps_with_other_offsets_compare_correctly(jobs: SqlJobRepository) -> None:
    from datetime import timezone

    taipei = timezone(timedelta(hours=8))
    spec = make_spec()
    jobs.create(spec)
    lease = jobs.claim_next(NodeId("node-a"), NOW.astimezone(taipei), LEASE)
    assert lease is not None

    assert jobs.requeue_expired(NOW + LEASE - timedelta(seconds=1)) == 0
    assert jobs.requeue_expired(NOW + LEASE) == 1


def test_legacy_sqlite_attempt_table_gets_additive_columns(tmp_path: Path) -> None:
    import sqlite3

    from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE job_attempts (
                attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, node_id TEXT NOT NULL,
                token_hash TEXT NOT NULL, attempt_number INTEGER NOT NULL,
                leased_at TEXT NOT NULL, expires_at TEXT NOT NULL, completed_at TEXT,
                result_hash TEXT, active INTEGER NOT NULL
            )
            """
        )

    SQLiteJobRepository(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(job_attempts)")}
    assert {"error_code", "error_summary", "retryable", "failed_at"} <= columns
