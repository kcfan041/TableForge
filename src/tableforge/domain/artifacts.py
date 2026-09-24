from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from tableforge.domain.ids import ArtifactId


class ArtifactKind(StrEnum):
    UNSPECIFIED = "unspecified"
    MODEL_CHECKPOINT = "model_checkpoint"
    TRAINING_STATE = "training_state"
    EXPERIENCE_SHARD = "experience_shard"
    HAND_HISTORY = "hand_history"
    EVALUATION_RESULT = "evaluation_result"
    DIAGNOSTIC = "diagnostic"
    DATASET_SNAPSHOT = "dataset_snapshot"
    LEAGUE_POPULATION = "league_population"


class ArtifactStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    AVAILABLE = "available"
    RETIRED = "retired"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    artifact_id: ArtifactId
    kind: ArtifactKind
    media_type: str
    schema_version: str

    def __post_init__(self) -> None:
        if not self.media_type:
            raise ValueError("media_type cannot be empty")
        if not self.schema_version:
            raise ValueError("schema_version cannot be empty")


@dataclass(frozen=True, slots=True)
class Artifact:
    descriptor: ArtifactDescriptor
    sha256: str
    size_bytes: int
    status: ArtifactStatus
    storage_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        invalid_character = any(
            character not in "0123456789abcdef" for character in self.sha256
        )
        if len(self.sha256) != 64 or invalid_character:
            raise ValueError("sha256 must be a lowercase hexadecimal digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    @classmethod
    def available(
        cls,
        descriptor: ArtifactDescriptor,
        sha256: str,
        size_bytes: int,
        storage_key: str,
    ) -> Artifact:
        return cls(
            descriptor=descriptor,
            sha256=sha256,
            size_bytes=size_bytes,
            status=ArtifactStatus.AVAILABLE,
            storage_key=storage_key,
            created_at=datetime.now(UTC),
        )
