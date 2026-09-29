from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tableforge.domain.artifacts import Artifact
from tableforge.transport.client import GrpcCoordinatorClient


def test_cache_tolerates_another_worker_writing_the_same_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"checkpoint"
    sha = hashlib.sha256(body).hexdigest()
    artifact = Artifact.__new__(Artifact)
    object.__setattr__(artifact, "sha256", sha)
    real_replace = os.replace

    def racing_replace(source: str | Path, target: str | Path) -> None:
        # Another worker finished writing the same file first and still holds it open.
        Path(target).write_bytes(body)
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", racing_replace)
    path = GrpcCoordinatorClient._store_in_cache(None, tmp_path, artifact, body)  # type: ignore[arg-type]
    monkeypatch.setattr(os, "replace", real_replace)

    assert path.read_bytes() == body
    assert not list(tmp_path.glob("*.part"))


def test_cache_still_fails_when_nothing_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = Artifact.__new__(Artifact)
    object.__setattr__(artifact, "sha256", "0" * 64)

    def failing_replace(source: str | Path, target: str | Path) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(PermissionError):
        GrpcCoordinatorClient._store_in_cache(None, tmp_path, artifact, b"x")  # type: ignore[arg-type]
    assert not list(tmp_path.glob("*.part"))
