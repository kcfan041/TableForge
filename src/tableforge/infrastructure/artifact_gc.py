from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tableforge.application.ports import ArtifactStore, CheckpointRepository, JobRepository


@dataclass(frozen=True, slots=True)
class GcReport:
    referenced: int
    candidates: int
    deleted_files: int
    deleted_bytes: int
    kept_recent: int
    dry_run: bool


def referenced_hashes(jobs: JobRepository, checkpoints: CheckpointRepository) -> set[str]:
    referenced: set[str] = set()
    for job in jobs.list_jobs():
        referenced.update(artifact.sha256 for artifact in job.spec.inputs)
        for output in jobs.outputs(job.spec.job_id):
            referenced.add(output.artifact.sha256)
        _payload_references(job.spec.payload, referenced)
    for checkpoint in checkpoints.list_checkpoints():
        referenced.add(checkpoint.sha256)
    return referenced


def _payload_references(payload: bytes, referenced: set[str]) -> None:
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return
    stack: list[object] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key.endswith("sha256") and isinstance(value, str) and len(value) == 64:
                    referenced.add(value)
                else:
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)


def collect_garbage(
    store: ArtifactStore,
    referenced: set[str],
    grace: timedelta,
    dry_run: bool,
    now: datetime | None = None,
) -> GcReport:
    current = datetime.now(UTC) if now is None else now
    candidates = 0
    deleted_files = 0
    deleted_bytes = 0
    kept_recent = 0
    doomed: list[str] = []
    for item in store.list_objects():
        if item.storage_key.rsplit("/", 1)[-1] in referenced:
            continue
        candidates += 1
        if current - item.modified_at < grace:
            kept_recent += 1
            continue
        doomed.append(item.storage_key)
        deleted_bytes += item.size_bytes
        deleted_files += 1
    if not dry_run:
        for storage_key in doomed:
            store.delete_object(storage_key)
    return GcReport(
        referenced=len(referenced),
        candidates=candidates,
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        kept_recent=kept_recent,
        dry_run=dry_run,
    )
