from __future__ import annotations

from datetime import datetime
from typing import Any

from tableforge.domain.artifacts import Artifact, ArtifactDescriptor, ArtifactKind, ArtifactStatus
from tableforge.domain.ids import ArtifactId


def artifact_record(artifact: Artifact) -> dict[str, object]:
    return {
        "artifact_id": str(artifact.descriptor.artifact_id),
        "kind": artifact.descriptor.kind.value,
        "media_type": artifact.descriptor.media_type,
        "schema_version": artifact.descriptor.schema_version,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "storage_key": artifact.storage_key,
        "created_at": artifact.created_at.isoformat(),
    }


def artifact_from_record(data: dict[str, Any]) -> Artifact:
    return Artifact(
        descriptor=ArtifactDescriptor(
            artifact_id=ArtifactId(data["artifact_id"]),
            kind=ArtifactKind(data["kind"]),
            media_type=data["media_type"],
            schema_version=data["schema_version"],
        ),
        sha256=data["sha256"],
        size_bytes=int(data["size_bytes"]),
        status=ArtifactStatus.AVAILABLE,
        storage_key=data["storage_key"],
        created_at=datetime.fromisoformat(data["created_at"]),
    )
