from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from tableforge.application.ports import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    StoredObject,
    storage_key_for,
)
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.infrastructure.artifact_metadata import (
    artifact_from_record,
    artifact_record,
)

__all__ = ["ArtifactIntegrityError", "ArtifactNotFoundError", "LocalArtifactStore"]


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._uploads = root / "uploads"
        self._metadata = root / "metadata"
        for directory in (self._uploads, self._metadata, root / "sha256"):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact:
        sha256 = hashlib.sha256(content).hexdigest()
        storage_key = storage_key_for(sha256)
        destination = self._root / storage_key
        if not destination.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._uploads / f"{sha256}.{secrets.token_hex(8)}.part"
            with temporary.open("wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.replace(temporary, destination)
            except OSError:
                temporary.unlink(missing_ok=True)
                if not destination.is_file():
                    raise
        actual_sha256, actual_size = _hash_file(destination)
        if actual_sha256 != sha256 or actual_size != len(content):
            raise ArtifactIntegrityError(storage_key)
        artifact = Artifact.available(descriptor, sha256, len(content), storage_key)
        self._write_metadata(artifact)
        return artifact

    def get(self, artifact_id: str) -> Artifact:
        path = self._metadata_path(artifact_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(artifact_id) from error
        return artifact_from_record(data)

    def read(self, artifact_id: str) -> tuple[Artifact, bytes]:
        artifact = self.get(artifact_id)
        try:
            content = (self._root / artifact.storage_key).read_bytes()
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(artifact_id) from error
        if (
            hashlib.sha256(content).hexdigest() != artifact.sha256
            or len(content) != artifact.size_bytes
        ):
            raise ArtifactIntegrityError(artifact_id)
        return artifact, content

    def path_for(self, artifact: Artifact) -> Path:
        return self._root / artifact.storage_key

    def exists(self, sha256: str) -> bool:
        return (self._root / storage_key_for(sha256)).is_file()

    def list_objects(self) -> Iterator[StoredObject]:
        for path in (self._root / "sha256").rglob("*"):
            if path.is_file():
                stat = path.stat()
                yield StoredObject(
                    storage_key=path.relative_to(self._root).as_posix(),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                )

    def delete_object(self, storage_key: str) -> None:
        sha256 = storage_key.rsplit("/", 1)[-1]
        (self._root / storage_key_for(sha256)).unlink(missing_ok=True)
        for metadata in self._metadata.glob("*.json"):
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("storage_key") == storage_key:
                metadata.unlink(missing_ok=True)

    def _metadata_path(self, artifact_id: str) -> Path:
        if (
            not artifact_id
            or "/" in artifact_id
            or "\\" in artifact_id
            or artifact_id in (".", "..")
        ):
            raise ArtifactNotFoundError(artifact_id)
        return self._metadata / f"{artifact_id}.json"

    def _write_metadata(self, artifact: Artifact) -> None:
        path = self._metadata_path(str(artifact.descriptor.artifact_id))
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(artifact_record(artifact), target, sort_keys=True, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 19:
                    temporary.unlink(missing_ok=True)
                    raise
                time.sleep(0.01 * (attempt + 1))


def _hash_file(path: Path) -> tuple[str, int]:
    for attempt in range(20):
        try:
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            return digest.hexdigest(), size
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01 * (attempt + 1))
    raise AssertionError("unreachable")
