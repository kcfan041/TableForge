from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from tableforge.application.ports import CheckpointRepository, JobRepository


@dataclass(frozen=True, slots=True)
class GcReport:
    referenced: int
    candidates: int
    deleted_files: int
    deleted_bytes: int
    kept_recent: int
    dry_run: bool


def referenced_hashes(
    jobs: JobRepository, checkpoints: CheckpointRepository
) -> set[str]:
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
    artifact_root: Path,
    referenced: set[str],
    grace_seconds: float,
    dry_run: bool,
    now: float | None = None,
) -> GcReport:
    current = time.time() if now is None else now
    objects_root = artifact_root / "sha256"
    metadata_root = artifact_root / "metadata"
    candidates = 0
    deleted_files = 0
    deleted_bytes = 0
    kept_recent = 0
    removed_hashes: set[str] = set()
    if objects_root.exists():
        for path in objects_root.rglob("*"):
            if not path.is_file() or path.name in referenced:
                continue
            candidates += 1
            stat = path.stat()
            if current - stat.st_mtime < grace_seconds:
                kept_recent += 1
                continue
            removed_hashes.add(path.name)
            deleted_bytes += stat.st_size
            deleted_files += 1
            if not dry_run:
                os.remove(path)
    if not dry_run and removed_hashes and metadata_root.exists():
        for metadata in metadata_root.glob("*.json"):
            if metadata.name.endswith(".pending.json"):
                continue
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("sha256") in removed_hashes:
                metadata.unlink(missing_ok=True)
    return GcReport(
        referenced=len(referenced),
        candidates=candidates,
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        kept_recent=kept_recent,
        dry_run=dry_run,
    )
