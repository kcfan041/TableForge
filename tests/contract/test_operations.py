from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.application.ports import LeaseFencedError
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.composition import Repositories
from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
from tableforge.domain.auth import AuthenticationError
from tableforge.domain.ids import NodeId, new_artifact_id, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobType
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.artifact_gc import collect_garbage, referenced_hashes
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sql.migrations import MIGRATIONS, migrate, schema_version
from tableforge.observability import Counters, render_metrics

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def spec(key: str = "k") -> JobSpec:
    profile = BUILTIN_WORKLOADS[0]
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=1,
        resources=profile.minimum,
        payload=SimulationJobPayload("1.0", hands=10, seed_start=0).to_bytes(),
        payload_type=profile.payload_type,
        idempotency_key=key,
        created_at=NOW,
    )


def test_job_transitions_emit_ordered_outbox_events(repositories: Repositories) -> None:
    job = spec()
    repositories.jobs.create(job)
    lease = repositories.jobs.claim_next(NodeId("n"), NOW, timedelta(minutes=5))
    assert lease is not None
    repositories.jobs.complete(job.job_id, lease.token, NOW, "a" * 64)

    events = repositories.events.list_events()

    assert [event.event_type for event in events] == ["JOB_CREATED", "JOB_LEASED", "JOB_SUCCEEDED"]
    assert [event.aggregate_version for event in events] == [0, 1, 2]
    assert all(event.aggregate_id == str(job.job_id) for event in events)
    assert events[0].sequence < events[1].sequence < events[2].sequence
    assert events[2].occurred_at == NOW


def test_rejected_transition_writes_no_event(repositories: Repositories) -> None:
    job = spec()
    repositories.jobs.create(job)
    repositories.jobs.claim_next(NodeId("n"), NOW, timedelta(minutes=5))

    with pytest.raises(LeaseFencedError):
        repositories.jobs.complete(job.job_id, "wrong-token", NOW, "a" * 64)

    log = repositories.events
    events = log.list_events()
    assert [event.event_type for event in events] == ["JOB_CREATED", "JOB_LEASED"]
    assert log.unpublished_count() == 2
    assert log.mark_published(events[-1].sequence, NOW) == 2
    assert log.unpublished_count() == 0
    assert log.list_events(after=events[0].sequence) == (replace(events[1], published_at=NOW),)


def test_concurrent_writes_keep_event_count_consistent(repositories: Repositories) -> None:
    for index in range(10):
        repositories.jobs.create(spec(f"job-{index}"))

    def claim(index: int) -> None:
        repositories.jobs.claim_next(NodeId(f"n{index}"), NOW, timedelta(minutes=5))

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(claim, range(15)))

    types = [event.event_type for event in repositories.events.list_events(limit=1000)]
    assert types.count("JOB_CREATED") == 10
    assert types.count("JOB_LEASED") == 10


def test_metrics_render_prometheus_text(repositories: Repositories) -> None:
    repositories.jobs.create(spec("a"))
    repositories.jobs.create(spec("b"))
    repositories.jobs.claim_next(NodeId("n"), NOW, timedelta(seconds=10))
    counters = Counters()
    counters.increment("tableforge_rpc_requests_total", method="ClaimJob", outcome="ok")

    text = render_metrics(repositories.database, NOW + timedelta(minutes=1), counters)

    assert 'tableforge_jobs{status="queued"} 1' in text
    assert 'tableforge_jobs{status="leased"} 1' in text
    assert "tableforge_expired_active_leases 1" in text
    assert "tableforge_unpublished_events 3" in text
    assert "# TYPE tableforge_rpc_requests_total counter" in text
    assert 'tableforge_rpc_requests_total{method="ClaimJob",outcome="ok"} 1' in text


def _store_blob(store: LocalArtifactStore, content: bytes) -> str:
    descriptor = ArtifactDescriptor(new_artifact_id(), ArtifactKind.DIAGNOSTIC, "text/plain", "1")
    return store.put(descriptor, content).sha256


def test_gc_deletes_only_old_unreferenced_objects(
    repositories: Repositories, tmp_path: Path
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    kept = _store_blob(store, b"referenced")
    orphan = _store_blob(store, b"orphan")
    fresh = _store_blob(store, b"fresh-orphan")
    repositories.jobs.create(
        replace(spec("ref"), payload=json.dumps({"checkpoint_sha256": kept}).encode())
    )
    old = time.time() - 7 * 24 * 3600
    for digest in (kept, orphan):
        path = tmp_path / "artifacts" / "sha256" / digest[:2] / digest[2:4] / digest
        os.utime(path, (old, old))
    referenced = referenced_hashes(repositories.jobs, repositories.checkpoints)

    dry = collect_garbage(store, referenced, timedelta(hours=1), dry_run=True)
    real = collect_garbage(store, referenced, timedelta(hours=1), dry_run=False)

    assert (dry.deleted_files, dry.kept_recent) == (1, 1)
    assert store.exists(orphan) is False
    assert store.exists(kept)
    assert store.exists(fresh)
    assert real.deleted_files == 1


def test_session_rotation_and_revocation(repositories: Repositories) -> None:
    auth = repositories.auth
    token = auth.create_enrollment_token(NOW, timedelta(hours=1))
    first = auth.enroll(token, NodeId("node-a"), NOW, timedelta(days=1))

    rotated = auth.rotate_session(first.token, NOW, timedelta(days=1))

    assert auth.authenticate(rotated.token, NOW) == NodeId("node-a")
    with pytest.raises(AuthenticationError):
        auth.authenticate(first.token, NOW)
    with pytest.raises(AuthenticationError):
        auth.rotate_session(first.token, NOW, timedelta(days=1))
    assert auth.revoke_node(NodeId("node-a")) == 1
    with pytest.raises(AuthenticationError):
        auth.authenticate(rotated.token, NOW)


def test_enrollment_token_is_single_node_and_expires(repositories: Repositories) -> None:
    auth = repositories.auth
    token = auth.create_enrollment_token(NOW, timedelta(minutes=10))

    auth.enroll(token, NodeId("node-a"), NOW, timedelta(days=1))
    again = auth.enroll(token, NodeId("node-a"), NOW, timedelta(days=1))

    assert auth.authenticate(again.token, NOW) == NodeId("node-a")
    with pytest.raises(AuthenticationError, match="already used"):
        auth.enroll(token, NodeId("node-b"), NOW, timedelta(days=1))
    expired = auth.create_enrollment_token(NOW, timedelta(minutes=10))
    with pytest.raises(AuthenticationError, match="expired"):
        auth.enroll(expired, NodeId("node-c"), NOW + timedelta(minutes=10), timedelta(days=1))
    with pytest.raises(AuthenticationError):
        auth.authenticate(again.token, NOW + timedelta(days=1))


def test_enrollment_tokens_are_safe_as_cli_arguments(repositories: Repositories) -> None:
    tokens = [
        repositories.auth.create_enrollment_token(NOW, timedelta(hours=1)) for _ in range(200)
    ]

    assert all(token.startswith("tfe_") for token in tokens)


def test_migrations_are_recorded_and_idempotent(repositories: Repositories) -> None:
    database = repositories.database
    latest = max(migration.version for migration in MIGRATIONS[database.name])

    assert schema_version(database) == latest
    assert migrate(database) == 0


def test_newer_schema_is_refused(repositories: Repositories) -> None:
    database = repositories.database
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (999, "future", NOW.isoformat()),
        )

    with pytest.raises(RuntimeError, match="newer than this TableForge"):
        migrate(database)
