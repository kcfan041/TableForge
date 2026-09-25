from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tableforge.infrastructure.sql.database import Connection, Database

SQLITE_V1 = """
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
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    current_capability_revision INTEGER NOT NULL,
    registered_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    available_memory_bytes INTEGER
);
CREATE TABLE IF NOT EXISTS node_capability_snapshots (
    node_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    capability_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (node_id, revision),
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    current_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_revisions (
    experiment_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (experiment_id, revision),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    parent_id TEXT,
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (agent_id, round_index)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_checkpoint_per_agent
ON checkpoints(agent_id) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS enrollment_tokens (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_by_node TEXT,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS node_sessions (
    token_hash TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    published_at TEXT
)
"""

SQLITE_V2 = """
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, priority DESC, created_at, job_id);
CREATE INDEX IF NOT EXISTS jobs_experiment ON jobs(experiment_id, created_at);
CREATE INDEX IF NOT EXISTS job_attempts_active_expiry ON job_attempts(active, expires_at);
CREATE INDEX IF NOT EXISTS job_attempts_node_active ON job_attempts(node_id, active);
CREATE INDEX IF NOT EXISTS node_sessions_node ON node_sessions(node_id)
"""

POSTGRES_V1 = """
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    job_type TEXT NOT NULL,
    priority INTEGER NOT NULL,
    resources_json TEXT NOT NULL,
    payload BYTEA NOT NULL,
    payload_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    not_before TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE (experiment_id, idempotency_key)
);
CREATE TABLE job_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    node_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    leased_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    result_hash TEXT,
    error_code TEXT,
    error_summary TEXT,
    retryable INTEGER,
    failed_at TIMESTAMPTZ,
    diagnostic_artifact_id TEXT,
    capability_revision INTEGER NOT NULL DEFAULT 0,
    workload_key TEXT NOT NULL DEFAULT '',
    workload_version TEXT NOT NULL DEFAULT '',
    execution_plan_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL,
    UNIQUE (job_id, attempt_number)
);
CREATE UNIQUE INDEX one_active_attempt_per_job ON job_attempts(job_id) WHERE active = 1;
CREATE TABLE job_outputs (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    name TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    kind TEXT NOT NULL,
    media_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (job_id, name)
);
CREATE TABLE nodes (
    node_id TEXT PRIMARY KEY,
    current_capability_revision INTEGER NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL,
    available_memory_bytes BIGINT
);
CREATE TABLE node_capability_snapshots (
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    revision INTEGER NOT NULL,
    capability_json TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (node_id, revision)
);
CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    current_revision INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE experiment_revisions (
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (experiment_id, revision)
);
CREATE TABLE checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    parent_id TEXT,
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (agent_id, round_index)
);
CREATE UNIQUE INDEX one_active_checkpoint_per_agent
ON checkpoints(agent_id) WHERE status = 'active';
CREATE TABLE enrollment_tokens (
    token_hash TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_by_node TEXT,
    used_at TIMESTAMPTZ
);
CREATE TABLE node_sessions (
    token_hash TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE events (
    sequence BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ
)
"""

POSTGRES_V2 = """
CREATE INDEX jobs_queue ON jobs(status, priority DESC, created_at, job_id);
CREATE INDEX jobs_experiment ON jobs(experiment_id, created_at);
CREATE INDEX job_attempts_active_expiry ON job_attempts(active, expires_at);
CREATE INDEX job_attempts_node_active ON job_attempts(node_id, active);
CREATE INDEX node_sessions_node ON node_sessions(node_id)
"""

SQLITE_V3 = """
ALTER TABLE enrollment_tokens ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS enrollment_token_uses (
    token_hash TEXT NOT NULL,
    node_id TEXT NOT NULL,
    used_at TEXT NOT NULL,
    PRIMARY KEY (token_hash, node_id)
);
INSERT INTO enrollment_token_uses (token_hash, node_id, used_at)
SELECT token_hash, used_by_node, used_at FROM enrollment_tokens WHERE used_by_node IS NOT NULL
"""

POSTGRES_V3 = """
ALTER TABLE enrollment_tokens ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1;
CREATE TABLE enrollment_token_uses (
    token_hash TEXT NOT NULL REFERENCES enrollment_tokens(token_hash),
    node_id TEXT NOT NULL,
    used_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (token_hash, node_id)
);
INSERT INTO enrollment_token_uses (token_hash, node_id, used_at)
SELECT token_hash, used_by_node, used_at FROM enrollment_tokens WHERE used_by_node IS NOT NULL
"""

_LEGACY_ATTEMPT_COLUMNS = {
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


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]


def _script(script: str) -> Callable[[Connection], None]:
    statements = [part.strip() for part in script.split(";") if part.strip()]

    def apply(connection: Connection) -> None:
        for statement in statements:
            connection.execute(statement)

    return apply


def _sqlite_v1(connection: Connection) -> None:
    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'job_attempts'"
    ).fetchone()
    if existing is not None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(job_attempts)").fetchall()
        }
        for name, column_type in _LEGACY_ATTEMPT_COLUMNS.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE job_attempts ADD COLUMN {name} {column_type}")
    _script(SQLITE_V1)(connection)


MIGRATIONS: dict[str, tuple[Migration, ...]] = {
    "sqlite": (
        Migration(1, "initial schema", _sqlite_v1),
        Migration(2, "query indexes", _script(SQLITE_V2)),
        Migration(3, "multi-use enrollment tokens", _script(SQLITE_V3)),
    ),
    "postgres": (
        Migration(1, "initial schema", _script(POSTGRES_V1)),
        Migration(2, "query indexes", _script(POSTGRES_V2)),
        Migration(3, "multi-use enrollment tokens", _script(POSTGRES_V3)),
    ),
}

_ADVISORY_LOCK_KEY = 7_415_526_110


def migrate(database: Database) -> int:
    migrations = MIGRATIONS[database.name]
    with database.transaction() as connection:
        if database.name == "postgres":
            connection.execute("SELECT pg_advisory_xact_lock(?)", (_ADVISORY_LOCK_KEY,))
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        latest = max(migration.version for migration in migrations)
        unknown = {version for version in applied if version > latest}
        if unknown:
            raise RuntimeError(
                f"metadata schema version {max(unknown)} is newer than this TableForge "
                f"({latest}); upgrade TableForge before using this database"
            )
        count = 0
        for migration in migrations:
            if migration.version in applied:
                continue
            migration.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, datetime.now(UTC).isoformat()),
            )
            count += 1
    return count


def schema_version(database: Database) -> int:
    with database.read() as connection:
        row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    return int(row["version"] or 0)
