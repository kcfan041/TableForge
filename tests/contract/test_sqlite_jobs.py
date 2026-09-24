from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tableforge.application.ports import IdempotencyConflictError, JobNotFoundError
from tableforge.domain.ids import JobId, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType, ResourceRequest
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository


def make_spec() -> JobSpec:
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=10,
        resources=ResourceRequest(cpu_cores=2, memory_bytes=1024, required_role="simulation"),
        payload=b"payload",
        payload_type="tableforge.v1.SimulationJobPayload",
        idempotency_key="simulation-1",
    )


def test_create_and_get_job(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "tableforge.db")
    spec = make_spec()

    created = repository.create(spec)
    loaded = repository.get(spec.job_id)

    assert created.status is JobStatus.QUEUED
    assert loaded == created


def test_same_idempotency_request_returns_existing_job(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "tableforge.db")
    spec = make_spec()
    first = repository.create(spec)
    retry_spec = replace(spec, job_id=new_job_id())

    retried = repository.create(retry_spec)

    assert retried == first


def test_changed_idempotency_request_conflicts(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "tableforge.db")
    spec = make_spec()
    repository.create(spec)

    with pytest.raises(IdempotencyConflictError):
        repository.create(replace(spec, job_id=new_job_id(), payload=b"different"))


def test_missing_job_raises(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "tableforge.db")

    with pytest.raises(JobNotFoundError):
        repository.get(JobId("missing"))
