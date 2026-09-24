from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from tableforge.application.ports import (
    IdempotencyConflictError,
    JobNotFoundError,
    LeaseConflictError,
    LeaseFencedError,
)
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor, ArtifactKind, ArtifactStatus
from tableforge.domain.ids import (
    ArtifactId,
    AttemptId,
    ExperimentId,
    JobId,
    NodeId,
    new_attempt_id,
)
from tableforge.domain.jobs import (
    Job,
    JobAttemptError,
    JobAttemptProvenance,
    JobLease,
    JobOutput,
    JobSpec,
    JobStatus,
    JobType,
    ResourceRequest,
)
from tableforge.infrastructure.sqlite.events import ensure_events_table, record_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    job_type TEXT NOT NULL,
    priority INTEGER NOT NULL,
    resources_json TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    not_before TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE (experiment_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS job_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    leased_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    completed_at TEXT,
    result_hash TEXT,
    error_code TEXT,
    error_summary TEXT,
    retryable INTEGER,
    failed_at TEXT,
    diagnostic_artifact_id TEXT,
    capability_revision INTEGER NOT NULL DEFAULT 0,
    workload_key TEXT NOT NULL DEFAULT '',
    workload_version TEXT NOT NULL DEFAULT '',
    execution_plan_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id),
    UNIQUE (job_id, attempt_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_job
ON job_attempts(job_id) WHERE active = 1;
CREATE TABLE IF NOT EXISTS job_outputs (
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    kind TEXT NOT NULL,
    media_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, name),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);
"""


class SQLiteJobRepository:
    def __init__(self, database: Path) -> None:
        self._database = database
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            ensure_events_table(connection)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(job_attempts)").fetchall()
            }
            additions = {
                "error_code": "TEXT",
                "error_summary": "TEXT",
                "retryable": "INTEGER",
                "failed_at": "TEXT",
                "diagnostic_artifact_id": "TEXT",
                "capability_revision": "INTEGER NOT NULL DEFAULT 0",
                "workload_key": "TEXT NOT NULL DEFAULT ''",
                "workload_version": "TEXT NOT NULL DEFAULT ''",
                "execution_plan_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, column_type in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE job_attempts ADD COLUMN {name} {column_type}"
                    )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, spec: JobSpec) -> Job:
        request_hash = _request_hash(spec)
        resources_json = _resources_json(spec.resources)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE experiment_id = ? AND idempotency_key = ?",
                (str(spec.experiment_id), spec.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflictError(spec.idempotency_key)
                return _row_to_job(existing)
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, experiment_id, schema_version, job_type, priority,
                    resources_json, payload, payload_type, idempotency_key,
                    request_hash, max_attempts, not_before, created_at, status, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(spec.job_id),
                    str(spec.experiment_id),
                    spec.schema_version,
                    spec.type.value,
                    spec.priority,
                    resources_json,
                    spec.payload,
                    spec.payload_type,
                    spec.idempotency_key,
                    request_hash,
                    spec.max_attempts,
                    spec.not_before.isoformat() if spec.not_before else None,
                    spec.created_at.isoformat(),
                    JobStatus.QUEUED.value,
                    0,
                ),
            )
            record_event(
                connection,
                "JOB_CREATED",
                "job",
                str(spec.job_id),
                0,
                {
                    "experiment_id": str(spec.experiment_id),
                    "payload_type": spec.payload_type,
                    "status": JobStatus.QUEUED.value,
                },
                spec.created_at,
            )
        return Job(spec=spec, status=JobStatus.QUEUED, version=0)

    def get(self, job_id: JobId) -> Job:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(str(job_id))
        return _row_to_job(row)

    def get_by_idempotency_key(
        self, experiment_id: ExperimentId, idempotency_key: str
    ) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE experiment_id = ? AND idempotency_key = ?",
                (str(experiment_id), idempotency_key),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def claim_next(
        self, node_id: NodeId, now: datetime, duration: timedelta
    ) -> JobLease | None:
        return self.claim_compatible(node_id, now, duration, lambda job: True)

    def claim_compatible(
        self,
        node_id: NodeId,
        now: datetime,
        duration: timedelta,
        compatible: Callable[[Job], bool],
        capability_revision: int = 0,
        workload_identity: Callable[[Job], tuple[str, str]] | None = None,
        execution_plan: Callable[[Job], str] | None = None,
    ) -> JobLease | None:
        _validate_lease_time(now, duration)
        with self._transaction() as connection:
            self._requeue_expired(connection, now)
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ? AND (not_before IS NULL OR not_before <= ?)
                ORDER BY priority DESC, created_at, job_id
                """,
                (JobStatus.QUEUED.value, now.isoformat()),
            ).fetchall()
            row = next(
                (candidate for candidate in rows if compatible(_row_to_job(candidate))),
                None,
            )
            if row is None:
                return None
            attempt_number = connection.execute(
                "SELECT COUNT(*) FROM job_attempts WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()[0] + 1
            selected_job = _row_to_job(row)
            workload_key, workload_version = (
                workload_identity(selected_job) if workload_identity is not None else ("", "")
            )
            plan_json = execution_plan(selected_job) if execution_plan is not None else "{}"
            token = secrets.token_urlsafe(32)
            attempt_id = new_attempt_id()
            expires_at = now + duration
            connection.execute(
                """
                INSERT INTO job_attempts (
                    attempt_id, job_id, node_id, token_hash, attempt_number,
                    leased_at, expires_at, capability_revision, workload_key,
                    workload_version, execution_plan_json, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    str(attempt_id),
                    row["job_id"],
                    str(node_id),
                    _token_hash(token),
                    attempt_number,
                    now.isoformat(),
                    expires_at.isoformat(),
                    capability_revision,
                    workload_key,
                    workload_version,
                    plan_json,
                ),
            )
            _set_status(connection, JobStatus.LEASED.value, row["job_id"], now)
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            return JobLease(
                job=_row_to_job(updated),
                attempt_id=attempt_id,
                node_id=node_id,
                token=token,
                attempt_number=attempt_number,
                leased_at=now,
                expires_at=expires_at,
            )

    def renew(
        self, job_id: JobId, token: str, now: datetime, duration: timedelta
    ) -> JobLease:
        _validate_lease_time(now, duration)
        with self._transaction() as connection:
            attempt = self._active_attempt(connection, job_id, token, now)
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            if JobStatus(row["status"]) is not JobStatus.LEASED:
                raise LeaseConflictError(str(job_id))
            expires_at = now + duration
            connection.execute(
                "UPDATE job_attempts SET expires_at = ? WHERE attempt_id = ?",
                (expires_at.isoformat(), attempt["attempt_id"]),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return JobLease(
                job=_row_to_job(row),
                attempt_id=AttemptId(attempt["attempt_id"]),
                node_id=NodeId(attempt["node_id"]),
                token=token,
                attempt_number=attempt["attempt_number"],
                leased_at=datetime.fromisoformat(attempt["leased_at"]),
                expires_at=expires_at,
            )

    def complete(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if not result_hash:
            raise ValueError("result_hash cannot be empty")
        token_hash = _token_hash(token)
        with self._transaction() as connection:
            attempt = connection.execute(
                "SELECT * FROM job_attempts WHERE job_id = ? AND token_hash = ?",
                (str(job_id), token_hash),
            ).fetchone()
            if attempt is None:
                raise LeaseFencedError(str(job_id))
            if attempt["completed_at"] is not None:
                if attempt["result_hash"] != result_hash:
                    raise LeaseConflictError(str(job_id))
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
                ).fetchone()
                return _row_to_job(row)
            if not attempt["active"] or datetime.fromisoformat(attempt["expires_at"]) <= now:
                raise LeaseFencedError(str(job_id))
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            if JobStatus(job_row["status"]) is not JobStatus.LEASED:
                raise LeaseConflictError(str(job_id))
            connection.execute(
                """
                UPDATE job_attempts
                SET active = 0, completed_at = ?, result_hash = ?
                WHERE attempt_id = ?
                """,
                (now.isoformat(), result_hash, attempt["attempt_id"]),
            )
            for index, artifact in enumerate(outputs):
                connection.execute(
                    """
                    INSERT INTO job_outputs (
                        job_id, name, artifact_id, sha256, size_bytes, kind,
                        media_type, schema_version, storage_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(job_id),
                        f"output-{index}",
                        str(artifact.descriptor.artifact_id),
                        artifact.sha256,
                        artifact.size_bytes,
                        artifact.descriptor.kind.value,
                        artifact.descriptor.media_type,
                        artifact.descriptor.schema_version,
                        artifact.storage_key,
                        artifact.created_at.isoformat(),
                    ),
                )
            _set_status(connection, JobStatus.SUCCEEDED.value, str(job_id), now)
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return _row_to_job(row)

    def fail(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        error_code: str,
        error_summary: str,
        retryable: bool,
        diagnostic_artifact_id: str | None = None,
    ) -> Job:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if not error_code or not error_summary:
            raise ValueError("error code and summary cannot be empty")
        with self._transaction() as connection:
            attempt = self._active_attempt(connection, job_id, token, now)
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            current_status = JobStatus(job_row["status"])
            if current_status is JobStatus.CANCEL_REQUESTED:
                next_status = JobStatus.CANCELLED
            elif current_status is not JobStatus.LEASED:
                raise LeaseConflictError(str(job_id))
            else:
                next_status = (
                    JobStatus.QUEUED
                    if retryable and attempt["attempt_number"] < job_row["max_attempts"]
                    else JobStatus.FAILED
                )
            connection.execute(
                """
                UPDATE job_attempts
                SET active = 0, error_code = ?, error_summary = ?, retryable = ?,
                    failed_at = ?, diagnostic_artifact_id = ?
                WHERE attempt_id = ?
                """,
                (
                    error_code,
                    error_summary,
                    int(retryable),
                    now.isoformat(),
                    diagnostic_artifact_id,
                    attempt["attempt_id"],
                ),
            )
            _set_status(connection, next_status.value, str(job_id), now)
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return _row_to_job(updated)

    def cancel(self, job_id: JobId, now: datetime) -> Job:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(str(job_id))
            status = JobStatus(row["status"])
            if status is JobStatus.QUEUED:
                next_status = JobStatus.CANCELLED
            elif status in (JobStatus.LEASED, JobStatus.RUNNING):
                next_status = JobStatus.CANCEL_REQUESTED
            elif status in (
                JobStatus.CANCEL_REQUESTED,
                JobStatus.CANCELLED,
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
            ):
                return _row_to_job(row)
            else:
                next_status = JobStatus.CANCELLED
            _set_status(connection, next_status.value, str(job_id), now)
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return _row_to_job(updated)

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with self._transaction() as connection:
            attempt = self._active_attempt(connection, job_id, token, now)
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            if JobStatus(row["status"]) is not JobStatus.CANCEL_REQUESTED:
                raise LeaseConflictError(str(job_id))
            connection.execute(
                "UPDATE job_attempts SET active = 0 WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            )
            _set_status(connection, JobStatus.CANCELLED.value, str(job_id), now)
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return _row_to_job(updated)

    def attempt_errors(self, job_id: JobId) -> tuple[JobAttemptError, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_attempts
                WHERE job_id = ? AND error_code IS NOT NULL
                ORDER BY attempt_number
                """,
                (str(job_id),),
            ).fetchall()
        return tuple(
            JobAttemptError(
                attempt_id=AttemptId(row["attempt_id"]),
                error_code=row["error_code"],
                error_summary=row["error_summary"],
                retryable=bool(row["retryable"]),
                failed_at=datetime.fromisoformat(row["failed_at"]),
                diagnostic_artifact_id=row["diagnostic_artifact_id"],
            )
            for row in rows
        )

    def attempt_provenance(self, job_id: JobId) -> tuple[JobAttemptProvenance, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_attempts
                WHERE job_id = ? ORDER BY attempt_number
                """,
                (str(job_id),),
            ).fetchall()
        return tuple(
            JobAttemptProvenance(
                attempt_id=AttemptId(row["attempt_id"]),
                node_id=NodeId(row["node_id"]),
                capability_revision=row["capability_revision"],
                workload_key=row["workload_key"],
                workload_version=row["workload_version"],
                execution_plan_json=row["execution_plan_json"],
            )
            for row in rows
        )

    def reserved_resources(self, node_id: NodeId) -> ResourceRequest:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT j.resources_json
                FROM job_attempts a
                JOIN jobs j ON j.job_id = a.job_id
                WHERE a.node_id = ? AND a.active = 1
                """,
                (str(node_id),),
            ).fetchall()
        resources = [ResourceRequest(**json.loads(row["resources_json"])) for row in rows]
        return ResourceRequest(
            cpu_cores=sum(resource.cpu_cores for resource in resources),
            memory_bytes=sum(resource.memory_bytes for resource in resources),
            gpu_count=sum(resource.gpu_count for resource in resources),
            gpu_memory_bytes=sum(resource.gpu_memory_bytes for resource in resources),
            storage_bytes=sum(resource.storage_bytes for resource in resources),
            exclusive_gpu=any(resource.exclusive_gpu for resource in resources),
        )

    def outputs(self, job_id: JobId) -> tuple[JobOutput, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_outputs WHERE job_id = ? ORDER BY name",
                (str(job_id),),
            ).fetchall()
        return tuple(_row_to_output(row) for row in rows)

    def list_jobs(self, experiment_id: ExperimentId | None = None) -> tuple[Job, ...]:
        with self._connect() as connection:
            if experiment_id is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at, job_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE experiment_id = ?
                    ORDER BY created_at, job_id
                    """,
                    (str(experiment_id),),
                ).fetchall()
        return tuple(_row_to_job(row) for row in rows)

    def requeue_expired(self, now: datetime) -> int:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with self._transaction() as connection:
            return self._requeue_expired(connection, now)

    def _requeue_expired(self, connection: sqlite3.Connection, now: datetime) -> int:
        attempts = connection.execute(
            """
            SELECT a.attempt_id, a.job_id, a.attempt_number, j.max_attempts,
                   j.status AS job_status
            FROM job_attempts a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.active = 1 AND a.expires_at <= ?
            """,
            (now.isoformat(),),
        ).fetchall()
        for attempt in attempts:
            current_status = JobStatus(attempt["job_status"])
            if current_status is JobStatus.CANCEL_REQUESTED:
                status = JobStatus.CANCELLED
            else:
                status = (
                    JobStatus.FAILED
                    if attempt["attempt_number"] >= attempt["max_attempts"]
                    else JobStatus.QUEUED
                )
            connection.execute(
                "UPDATE job_attempts SET active = 0 WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            )
            _set_status(connection, status.value, attempt["job_id"], now)
        return len(attempts)

    def _active_attempt(
        self, connection: sqlite3.Connection, job_id: JobId, token: str, now: datetime
    ) -> sqlite3.Row:
        attempt = connection.execute(
            "SELECT * FROM job_attempts WHERE job_id = ? AND active = 1",
            (str(job_id),),
        ).fetchone()
        if (
            attempt is None
            or not secrets.compare_digest(attempt["token_hash"], _token_hash(token))
            or datetime.fromisoformat(attempt["expires_at"]) <= now
        ):
            raise LeaseFencedError(str(job_id))
        return cast(sqlite3.Row, attempt)


def _set_status(
    connection: sqlite3.Connection, status: str, job_id: str, now: datetime
) -> None:
    connection.execute(
        "UPDATE jobs SET status = ?, version = version + 1 WHERE job_id = ?",
        (status, job_id),
    )
    row = connection.execute(
        "SELECT experiment_id, version, payload_type FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    record_event(
        connection,
        f"JOB_{status.upper()}",
        "job",
        job_id,
        int(row["version"]),
        {
            "experiment_id": row["experiment_id"],
            "payload_type": row["payload_type"],
            "status": status,
        },
        now,
    )


def _resources_json(resources: ResourceRequest) -> str:
    return json.dumps(
        {
            "cpu_cores": resources.cpu_cores,
            "memory_bytes": resources.memory_bytes,
            "gpu_count": resources.gpu_count,
            "gpu_memory_bytes": resources.gpu_memory_bytes,
            "storage_bytes": resources.storage_bytes,
            "required_role": resources.required_role,
            "required_runtime": resources.required_runtime,
            "exclusive_gpu": resources.exclusive_gpu,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _validate_lease_time(now: datetime, duration: timedelta) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if duration <= timedelta(0):
        raise ValueError("lease duration must be positive")


def _request_hash(spec: JobSpec) -> str:
    digest = hashlib.sha256()
    values = (
        spec.schema_version,
        str(spec.experiment_id),
        spec.type.value,
        str(spec.priority),
        _resources_json(spec.resources),
        spec.payload_type,
        spec.payload.hex(),
        str(spec.max_attempts),
        spec.not_before.isoformat() if spec.not_before else "",
        ",".join(artifact.sha256 for artifact in spec.inputs),
    )
    digest.update("\x1f".join(values).encode())
    return digest.hexdigest()


def _row_to_output(row: sqlite3.Row) -> JobOutput:
    descriptor = ArtifactDescriptor(
        artifact_id=ArtifactId(row["artifact_id"]),
        kind=ArtifactKind(row["kind"]),
        media_type=row["media_type"],
        schema_version=row["schema_version"],
    )
    artifact = Artifact(
        descriptor=descriptor,
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        status=ArtifactStatus.AVAILABLE,
        storage_key=row["storage_key"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
    return JobOutput(job_id=JobId(row["job_id"]), artifact=artifact, name=row["name"])


def _row_to_job(row: sqlite3.Row) -> Job:
    resources_data = json.loads(row["resources_json"])
    spec = JobSpec(
        schema_version=row["schema_version"],
        job_id=JobId(row["job_id"]),
        experiment_id=ExperimentId(row["experiment_id"]),
        type=JobType(row["job_type"]),
        priority=row["priority"],
        resources=ResourceRequest(**resources_data),
        payload=row["payload"],
        payload_type=row["payload_type"],
        idempotency_key=row["idempotency_key"],
        max_attempts=row["max_attempts"],
        not_before=datetime.fromisoformat(row["not_before"]) if row["not_before"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
    return Job(spec=spec, status=JobStatus(row["status"]), version=row["version"])
