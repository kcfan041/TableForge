from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

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
from tableforge.infrastructure.sql.database import (
    Connection,
    Database,
    optional_timestamp,
    parse_timestamp,
)
from tableforge.infrastructure.sql.events import record_event

CLAIM_BATCH = 64


class SqlJobRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def create(self, spec: JobSpec) -> Job:
        request_hash = _request_hash(spec)
        for _ in range(2):
            try:
                with self._db.transaction() as connection:
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
                            _resources_json(spec.resources),
                            spec.payload,
                            spec.payload_type,
                            spec.idempotency_key,
                            request_hash,
                            spec.max_attempts,
                            self._db.timestamp(spec.not_before) if spec.not_before else None,
                            self._db.timestamp(spec.created_at),
                            JobStatus.QUEUED.value,
                            0,
                        ),
                    )
                    record_event(
                        self._db,
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
            except self._db.integrity_errors:
                continue
        raise IdempotencyConflictError(spec.idempotency_key)

    def get(self, job_id: JobId) -> Job:
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(str(job_id))
        return _row_to_job(row)

    def get_by_idempotency_key(
        self, experiment_id: ExperimentId, idempotency_key: str
    ) -> Job | None:
        with self._db.read() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE experiment_id = ? AND idempotency_key = ?",
                (str(experiment_id), idempotency_key),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def claim_next(self, node_id: NodeId, now: datetime, duration: timedelta) -> JobLease | None:
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
        with self._db.transaction() as connection:
            self._requeue_expired(connection, now)
            after: tuple[int, Any, str] | None = None
            while True:
                rows = self._queued_batch(connection, now, after)
                if not rows:
                    return None
                for row in rows:
                    job = _row_to_job(row)
                    if not compatible(job):
                        continue
                    locked = connection.execute(
                        f"""
                        SELECT * FROM jobs WHERE job_id = ? AND status = ?
                        {self._db.lock_skip_locked}
                        """,
                        (row["job_id"], JobStatus.QUEUED.value),
                    ).fetchone()
                    if locked is None:
                        continue
                    return self._lease(
                        connection,
                        _row_to_job(locked),
                        node_id,
                        now,
                        duration,
                        capability_revision,
                        workload_identity(job) if workload_identity else ("", ""),
                        execution_plan(job) if execution_plan else "{}",
                    )
                last = rows[-1]
                after = (int(last["priority"]), last["created_at"], last["job_id"])
                if len(rows) < CLAIM_BATCH:
                    return None

    def _queued_batch(
        self, connection: Connection, now: datetime, after: tuple[int, Any, str] | None
    ) -> list[Any]:
        where = "status = ? AND (not_before IS NULL OR not_before <= ?)"
        parameters: list[object] = [JobStatus.QUEUED.value, self._db.timestamp(now)]
        if after is not None:
            where += (
                " AND (priority < ? OR (priority = ? AND (created_at > ?"
                " OR (created_at = ? AND job_id > ?))))"
            )
            priority, created_at, job_id = after
            parameters += [priority, priority, created_at, created_at, job_id]
        parameters.append(CLAIM_BATCH)
        return list(
            connection.execute(
                f"""
                SELECT * FROM jobs WHERE {where}
                ORDER BY priority DESC, created_at, job_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        )

    def _lease(
        self,
        connection: Connection,
        job: Job,
        node_id: NodeId,
        now: datetime,
        duration: timedelta,
        capability_revision: int,
        workload: tuple[str, str],
        plan_json: str,
    ) -> JobLease:
        job_id = str(job.spec.job_id)
        attempt_number = (
            int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM job_attempts WHERE job_id = ?", (job_id,)
                ).fetchone()["count"]
            )
            + 1
        )
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
                job_id,
                str(node_id),
                _token_hash(token),
                attempt_number,
                self._db.timestamp(now),
                self._db.timestamp(expires_at),
                capability_revision,
                workload[0],
                workload[1],
                plan_json,
            ),
        )
        updated = self._set_status(connection, JobStatus.LEASED, job_id, now)
        return JobLease(
            job=updated,
            attempt_id=attempt_id,
            node_id=node_id,
            token=token,
            attempt_number=attempt_number,
            leased_at=now,
            expires_at=expires_at,
            execution_plan_json=plan_json,
        )

    def renew(self, job_id: JobId, token: str, now: datetime, duration: timedelta) -> JobLease:
        _validate_lease_time(now, duration)
        with self._db.transaction() as connection:
            job_row = self._locked_job(connection, job_id)
            attempt = self._active_attempt(connection, job_id, token, now)
            if JobStatus(job_row["status"]) is not JobStatus.LEASED:
                raise LeaseConflictError(str(job_id))
            expires_at = now + duration
            connection.execute(
                "UPDATE job_attempts SET expires_at = ? WHERE attempt_id = ?",
                (self._db.timestamp(expires_at), attempt["attempt_id"]),
            )
            return JobLease(
                job=_row_to_job(job_row),
                attempt_id=AttemptId(attempt["attempt_id"]),
                node_id=NodeId(attempt["node_id"]),
                token=token,
                attempt_number=int(attempt["attempt_number"]),
                leased_at=parse_timestamp(attempt["leased_at"]),
                expires_at=expires_at,
                execution_plan_json=attempt["execution_plan_json"],
            )

    def complete(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job:
        _require_aware(now)
        if not result_hash:
            raise ValueError("result_hash cannot be empty")
        with self._db.transaction() as connection:
            job_row = self._locked_job(connection, job_id)
            attempt = connection.execute(
                "SELECT * FROM job_attempts WHERE job_id = ? AND token_hash = ?",
                (str(job_id), _token_hash(token)),
            ).fetchone()
            if attempt is None:
                raise LeaseFencedError(str(job_id))
            if attempt["completed_at"] is not None:
                if attempt["result_hash"] != result_hash:
                    raise LeaseConflictError(str(job_id))
                return _row_to_job(job_row)
            if not attempt["active"] or parse_timestamp(attempt["expires_at"]) <= now:
                raise LeaseFencedError(str(job_id))
            if JobStatus(job_row["status"]) is not JobStatus.LEASED:
                raise LeaseConflictError(str(job_id))
            connection.execute(
                """
                UPDATE job_attempts
                SET active = 0, completed_at = ?, result_hash = ?
                WHERE attempt_id = ?
                """,
                (self._db.timestamp(now), result_hash, attempt["attempt_id"]),
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
                        self._db.timestamp(artifact.created_at),
                    ),
                )
            return self._set_status(connection, JobStatus.SUCCEEDED, str(job_id), now)

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
        _require_aware(now)
        if not error_code or not error_summary:
            raise ValueError("error code and summary cannot be empty")
        with self._db.transaction() as connection:
            job_row = self._locked_job(connection, job_id)
            attempt = self._active_attempt(connection, job_id, token, now)
            current_status = JobStatus(job_row["status"])
            if current_status is JobStatus.CANCEL_REQUESTED:
                next_status = JobStatus.CANCELLED
            elif current_status is not JobStatus.LEASED:
                raise LeaseConflictError(str(job_id))
            else:
                next_status = (
                    JobStatus.QUEUED
                    if retryable and int(attempt["attempt_number"]) < int(job_row["max_attempts"])
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
                    self._db.timestamp(now),
                    diagnostic_artifact_id,
                    attempt["attempt_id"],
                ),
            )
            return self._set_status(connection, next_status, str(job_id), now)

    def cancel(self, job_id: JobId, now: datetime) -> Job:
        _require_aware(now)
        with self._db.transaction() as connection:
            row = self._locked_job(connection, job_id)
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
            return self._set_status(connection, next_status, str(job_id), now)

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job:
        _require_aware(now)
        with self._db.transaction() as connection:
            row = self._locked_job(connection, job_id)
            attempt = self._active_attempt(connection, job_id, token, now)
            if JobStatus(row["status"]) is not JobStatus.CANCEL_REQUESTED:
                raise LeaseConflictError(str(job_id))
            connection.execute(
                "UPDATE job_attempts SET active = 0 WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            )
            return self._set_status(connection, JobStatus.CANCELLED, str(job_id), now)

    def attempt_errors(self, job_id: JobId) -> tuple[JobAttemptError, ...]:
        with self._db.read() as connection:
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
                failed_at=parse_timestamp(row["failed_at"]),
                diagnostic_artifact_id=row["diagnostic_artifact_id"],
            )
            for row in rows
        )

    def attempt_provenance(self, job_id: JobId) -> tuple[JobAttemptProvenance, ...]:
        with self._db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM job_attempts WHERE job_id = ? ORDER BY attempt_number",
                (str(job_id),),
            ).fetchall()
        return tuple(
            JobAttemptProvenance(
                attempt_id=AttemptId(row["attempt_id"]),
                node_id=NodeId(row["node_id"]),
                capability_revision=int(row["capability_revision"]),
                workload_key=row["workload_key"],
                workload_version=row["workload_version"],
                execution_plan_json=row["execution_plan_json"],
            )
            for row in rows
        )

    def reserved_resources(self, node_id: NodeId) -> ResourceRequest:
        with self._db.read() as connection:
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
        with self._db.read() as connection:
            rows = connection.execute(
                "SELECT * FROM job_outputs WHERE job_id = ? ORDER BY name",
                (str(job_id),),
            ).fetchall()
        return tuple(_row_to_output(row) for row in rows)

    def list_jobs(self, experiment_id: ExperimentId | None = None) -> tuple[Job, ...]:
        with self._db.read() as connection:
            if experiment_id is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at, job_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE experiment_id = ? ORDER BY created_at, job_id",
                    (str(experiment_id),),
                ).fetchall()
        return tuple(_row_to_job(row) for row in rows)

    def requeue_expired(self, now: datetime) -> int:
        _require_aware(now)
        with self._db.transaction() as connection:
            return self._requeue_expired(connection, now)

    def _requeue_expired(self, connection: Connection, now: datetime) -> int:
        expired = connection.execute(
            f"""
            SELECT j.* FROM jobs j
            WHERE EXISTS (
                SELECT 1 FROM job_attempts a
                WHERE a.job_id = j.job_id AND a.active = 1 AND a.expires_at <= ?
            )
            ORDER BY j.job_id{self._db.lock_skip_locked}
            """,
            (self._db.timestamp(now),),
        ).fetchall()
        count = 0
        for job_row in expired:
            attempt = connection.execute(
                """
                SELECT attempt_id, attempt_number FROM job_attempts
                WHERE job_id = ? AND active = 1 AND expires_at <= ?
                """,
                (job_row["job_id"], self._db.timestamp(now)),
            ).fetchone()
            if attempt is None:
                continue
            if JobStatus(job_row["status"]) is JobStatus.CANCEL_REQUESTED:
                status = JobStatus.CANCELLED
            elif int(attempt["attempt_number"]) >= int(job_row["max_attempts"]):
                status = JobStatus.FAILED
            else:
                status = JobStatus.QUEUED
            connection.execute(
                "UPDATE job_attempts SET active = 0 WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            )
            self._set_status(connection, status, job_row["job_id"], now)
            count += 1
        return count

    def _locked_job(self, connection: Connection, job_id: JobId) -> Any:
        row = connection.execute(
            f"SELECT * FROM jobs WHERE job_id = ?{self._db.lock_for_update}", (str(job_id),)
        ).fetchone()
        if row is None:
            raise JobNotFoundError(str(job_id))
        return row

    def _active_attempt(
        self, connection: Connection, job_id: JobId, token: str, now: datetime
    ) -> Any:
        attempt = connection.execute(
            "SELECT * FROM job_attempts WHERE job_id = ? AND active = 1",
            (str(job_id),),
        ).fetchone()
        if (
            attempt is None
            or not secrets.compare_digest(attempt["token_hash"], _token_hash(token))
            or parse_timestamp(attempt["expires_at"]) <= now
        ):
            raise LeaseFencedError(str(job_id))
        return attempt

    def _set_status(
        self, connection: Connection, status: JobStatus, job_id: str, now: datetime
    ) -> Job:
        connection.execute(
            "UPDATE jobs SET status = ?, version = version + 1 WHERE job_id = ?",
            (status.value, job_id),
        )
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        record_event(
            self._db,
            connection,
            f"JOB_{status.value.upper()}",
            "job",
            job_id,
            int(row["version"]),
            {
                "experiment_id": row["experiment_id"],
                "payload_type": row["payload_type"],
                "status": status.value,
            },
            now,
        )
        return _row_to_job(row)


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


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")


def _validate_lease_time(now: datetime, duration: timedelta) -> None:
    _require_aware(now)
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


def _row_to_output(row: Any) -> JobOutput:
    descriptor = ArtifactDescriptor(
        artifact_id=ArtifactId(row["artifact_id"]),
        kind=ArtifactKind(row["kind"]),
        media_type=row["media_type"],
        schema_version=row["schema_version"],
    )
    artifact = Artifact(
        descriptor=descriptor,
        sha256=row["sha256"],
        size_bytes=int(row["size_bytes"]),
        status=ArtifactStatus.AVAILABLE,
        storage_key=row["storage_key"],
        created_at=parse_timestamp(row["created_at"]),
    )
    return JobOutput(job_id=JobId(row["job_id"]), artifact=artifact, name=row["name"])


def _row_to_job(row: Any) -> Job:
    spec = JobSpec(
        schema_version=row["schema_version"],
        job_id=JobId(row["job_id"]),
        experiment_id=ExperimentId(row["experiment_id"]),
        type=JobType(row["job_type"]),
        priority=int(row["priority"]),
        resources=ResourceRequest(**json.loads(row["resources_json"])),
        payload=bytes(row["payload"]),
        payload_type=row["payload_type"],
        idempotency_key=row["idempotency_key"],
        max_attempts=int(row["max_attempts"]),
        not_before=optional_timestamp(row["not_before"]),
        created_at=parse_timestamp(row["created_at"]),
    )
    return Job(spec=spec, status=JobStatus(row["status"]), version=int(row["version"]))
