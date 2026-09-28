"""A local library of trained models, kept outside the repository.

Layout: ``<home>/models/<model id>/checkpoint.zip`` plus ``model.json`` with how the model
was trained and how it was evaluated. The checkpoint is content-addressed (sha256 in
``model.json``) and checked on every read, so a damaged or replaced file is detected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL_SCHEMA = "tableforge.model.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_id: str
    algorithm: str
    game: str
    """What the model plays, e.g. ``6max-pko-10bb`` or ``hu-cash-100bb``."""
    sha256: str
    size_bytes: int
    created_at: str
    description: str = ""
    training: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {"schema_version": MODEL_SCHEMA, **asdict(self)}, indent=2, sort_keys=True
        )

    @classmethod
    def from_json(cls, text: str) -> ModelInfo:
        data = json.loads(text)
        if data.pop("schema_version", None) != MODEL_SCHEMA:
            raise ValueError("unsupported model description")
        return cls(**data)


class ModelLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root

    def add(
        self,
        model_id: str,
        checkpoint: bytes,
        algorithm: str,
        game: str,
        description: str = "",
        training: dict[str, Any] | None = None,
        evaluation: dict[str, Any] | None = None,
        source: str = "",
        replace: bool = False,
    ) -> ModelInfo:
        if not _ID.match(model_id):
            raise ValueError("model id: lowercase letters, digits, '.', '_' or '-', at most 64")
        folder = self.root / model_id
        if folder.exists() and not replace:
            raise FileExistsError(f"model {model_id} already exists")
        info = ModelInfo(
            model_id=model_id,
            algorithm=algorithm,
            game=game,
            sha256=hashlib.sha256(checkpoint).hexdigest(),
            size_bytes=len(checkpoint),
            created_at=datetime.now(UTC).isoformat(),
            description=description,
            training=training or {},
            evaluation=evaluation or {},
            source=source,
        )
        staging = self.root / f".{model_id}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        (staging / "checkpoint.zip").write_bytes(checkpoint)
        (staging / "model.json").write_text(info.to_json(), encoding="utf-8")
        if folder.exists():
            shutil.rmtree(folder)
        os.replace(staging, folder)
        return info

    def list(self) -> list[ModelInfo]:
        if not self.root.is_dir():
            return []
        models = []
        for folder in sorted(self.root.iterdir()):
            description = folder / "model.json"
            if folder.name.startswith(".") or not description.is_file():
                continue
            try:
                models.append(ModelInfo.from_json(description.read_text(encoding="utf-8")))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return models

    def info(self, model_id: str) -> ModelInfo:
        path = self.root / model_id / "model.json"
        if not _ID.match(model_id) or not path.is_file():
            raise KeyError(model_id)
        return ModelInfo.from_json(path.read_text(encoding="utf-8"))

    def checkpoint(self, model_id: str) -> bytes:
        info = self.info(model_id)
        content = (self.root / model_id / "checkpoint.zip").read_bytes()
        if hashlib.sha256(content).hexdigest() != info.sha256:
            raise ValueError(f"checkpoint of model {model_id} does not match its sha256")
        return content

    def remove(self, model_id: str) -> None:
        self.info(model_id)
        shutil.rmtree(self.root / model_id)
