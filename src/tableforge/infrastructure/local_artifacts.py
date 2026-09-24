from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import BinaryIO

from tableforge.application.ports import DownloadGrant, UploadGrant
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor, ArtifactKind
from tableforge.domain.ids import ArtifactId


class ArtifactNotFoundError(Exception):
    pass


class ArtifactIntegrityError(Exception):
    pass


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._objects = root / "objects"
        self._uploads = root / "uploads"
        self._metadata = root / "metadata"
        for directory in (self._objects, self._uploads, self._metadata):
            directory.mkdir(parents=True, exist_ok=True)

    def begin_upload(self, descriptor: ArtifactDescriptor) -> UploadGrant:
        path = self._uploads / f"{descriptor.artifact_id}.{secrets.token_hex(8)}.part"
        _write_json_atomic(
            _pending_path(path),
            {
                "artifact_id": str(descriptor.artifact_id),
                "kind": descriptor.kind.value,
                "media_type": descriptor.media_type,
                "schema_version": descriptor.schema_version,
            },
        )
        return UploadGrant(artifact_id=str(descriptor.artifact_id), temporary_path=path)

    def write_upload(self, grant: UploadGrant, source: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with grant.temporary_path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        return digest.hexdigest(), size

    def commit_upload(self, grant: UploadGrant, sha256: str, size_bytes: int) -> Artifact:
        pending_path = _pending_path(grant.temporary_path)
        if not pending_path.exists() or not grant.temporary_path.exists():
            raise ArtifactNotFoundError(grant.artifact_id)
        actual_sha256, actual_size = _hash_file(grant.temporary_path)
        if actual_sha256 != sha256 or actual_size != size_bytes:
            raise ArtifactIntegrityError(grant.artifact_id)
        descriptor_data = json.loads(pending_path.read_text(encoding="utf-8"))
        descriptor = ArtifactDescriptor(
            artifact_id=ArtifactId(descriptor_data["artifact_id"]),
            kind=ArtifactKind(descriptor_data["kind"]),
            media_type=descriptor_data["media_type"],
            schema_version=descriptor_data["schema_version"],
        )
        storage_key = f"sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"
        destination = self._root / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            try:
                os.replace(grant.temporary_path, destination)
            except OSError:
                if not destination.exists():
                    raise
        if grant.temporary_path.exists():
            existing_sha256, existing_size = _hash_file(destination)
            if existing_sha256 != sha256 or existing_size != size_bytes:
                raise ArtifactIntegrityError(storage_key)
            grant.temporary_path.unlink(missing_ok=True)
        artifact = Artifact.available(descriptor, sha256, size_bytes, storage_key)
        _write_json_atomic(
            self._metadata / f"{grant.artifact_id}.json",
            {
                "artifact_id": grant.artifact_id,
                "kind": descriptor.kind.value,
                "media_type": descriptor.media_type,
                "schema_version": descriptor.schema_version,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "storage_key": storage_key,
                "created_at": artifact.created_at.isoformat(),
            },
        )
        pending_path.unlink(missing_ok=True)
        return artifact

    def get_download(self, artifact_id: str) -> DownloadGrant:
        metadata_path = self._metadata / f"{artifact_id}.json"
        if not metadata_path.exists():
            raise ArtifactNotFoundError(artifact_id)
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        path = self._root / data["storage_key"]
        actual_sha256, actual_size = _hash_file(path)
        if actual_sha256 != data["sha256"] or actual_size != data["size_bytes"]:
            raise ArtifactIntegrityError(artifact_id)
        descriptor = ArtifactDescriptor(
            artifact_id=ArtifactId(data["artifact_id"]),
            kind=ArtifactKind(data["kind"]),
            media_type=data["media_type"],
            schema_version=data["schema_version"],
        )
        artifact = Artifact.available(
            descriptor=descriptor,
            sha256=data["sha256"],
            size_bytes=data["size_bytes"],
            storage_key=data["storage_key"],
        )
        return DownloadGrant(artifact=artifact, path=path)

    def exists(self, sha256: str) -> bool:
        return (self._root / f"sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}").is_file()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except FileNotFoundError as error:
        raise ArtifactNotFoundError(str(path)) from error
    return digest.hexdigest(), size


def _pending_path(upload: Path) -> Path:
    return upload.with_name(upload.name + ".pending.json")


def _write_json_atomic(path: Path, data: object) -> None:
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as target:
        json.dump(data, target, sort_keys=True, separators=(",", ":"))
        target.flush()
        os.fsync(target.fileno())
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01 * (attempt + 1))
