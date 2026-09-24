from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from tableforge.application.ports import IdempotencyConflictError, JobNotFoundError
from tableforge.domain.ids import ExperimentId, JobId
from tableforge.domain.jobs import Job, JobSpec, JobStatus, JobType, ResourceRequest

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


def _resources_json(resources: ResourceRequest) -> str:
    return json.dumps(
        {
            "cpu_cores": resources.cpu_cores,
            "memory_bytes": resources.memory_bytes,
            "gpu_count": resources.gpu_count,
            "gpu_memory_bytes": resources.gpu_memory_bytes,
            "required_role": resources.required_role,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


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
