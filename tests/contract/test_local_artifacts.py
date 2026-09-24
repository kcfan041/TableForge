from __future__ import annotations

import io
from pathlib import Path

import pytest

from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind, ArtifactStatus
from tableforge.domain.ids import new_artifact_id
from tableforge.infrastructure.local_artifacts import ArtifactIntegrityError, LocalArtifactStore


def descriptor() -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=new_artifact_id(),
        kind=ArtifactKind.EXPERIENCE_SHARD,
        media_type="application/octet-stream",
        schema_version="1.0",
    )


def test_upload_commit_and_download(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    grant = store.begin_upload(descriptor())
    sha256, size = store.write_upload(grant, io.BytesIO(b"experience"))

    artifact = store.commit_upload(grant, sha256, size)
    download = store.get_download(str(artifact.descriptor.artifact_id))

    assert artifact.status is ArtifactStatus.AVAILABLE
    assert store.exists(sha256)
    assert download.path.read_bytes() == b"experience"
    assert download.artifact.sha256 == sha256


def test_content_is_deduplicated_by_hash(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifacts = []
    for _ in range(2):
        grant = store.begin_upload(descriptor())
        sha256, size = store.write_upload(grant, io.BytesIO(b"same"))
        artifacts.append(store.commit_upload(grant, sha256, size))

    assert artifacts[0].storage_key == artifacts[1].storage_key


def test_commit_rejects_wrong_digest(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    grant = store.begin_upload(descriptor())
    _, size = store.write_upload(grant, io.BytesIO(b"corrupt"))

    with pytest.raises(ArtifactIntegrityError):
        store.commit_upload(grant, "0" * 64, size)
