"""Named models of a cluster: the library on the cluster host, and checkpoints for jobs.

Models live in `model_library_root(settings.data_dir)` (`tableforge.models.ModelLibrary`).
Jobs that use a model (replays, strategy tables) reference it as a content-addressed
artifact, uploaded on demand, so every worker verifies what it loads.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from tableforge.composition import Services
from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
from tableforge.domain.ids import AgentId, ArtifactId, ExperimentId
from tableforge.domain.learning import CHECKPOINT_SCHEMA
from tableforge.models import ModelInfo, ModelLibrary
from tableforge.web.cluster import model_library_root


def library(services: Services) -> ModelLibrary:
    return ModelLibrary(model_library_root(services.settings.data_dir))


def model_view(info: ModelInfo) -> dict[str, object]:
    data = asdict(info)
    data["play_modes"] = play_modes(info.algorithm)
    return data


def play_modes(algorithm: str) -> list[dict[str, str]]:
    """Play modes the UI can offer; the first is the default."""
    if algorithm == "nfsp":
        from tableforge.learning.nfsp_explain import AVERAGE, BEST_RESPONSE

        return [asdict(AVERAGE), asdict(BEST_RESPONSE)]
    return [{"key": "default", "label": "Default", "description": ""}]


def checkpoint_artifact(services: Services, model_id: str) -> tuple[str, str]:
    """(artifact id, sha256) of a model's checkpoint in the cluster's artifact store."""
    content = library(services).checkpoint(model_id)
    sha = hashlib.sha256(content).hexdigest()
    artifact = services.coordinator.upload_artifact(
        ArtifactDescriptor(
            ArtifactId(f"content-{sha}"),
            ArtifactKind.MODEL_CHECKPOINT,
            "application/zip",
            CHECKPOINT_SCHEMA,
        ),
        content,
    )
    return str(artifact.descriptor.artifact_id), artifact.sha256


def save_experiment_model(
    services: Services, experiment_id: str, model_id: str, description: str
) -> ModelInfo:
    """Store the active checkpoint of a finished NFSP experiment as a named model."""
    experiments = services.experiments
    experiment = experiments.experiments.get(ExperimentId(experiment_id))
    agent = AgentId(f"nfsp:{experiment.experiment_id}")
    checkpoint = services.repositories.checkpoints.active(agent)
    if checkpoint is None:
        raise ValueError("this experiment has no trained checkpoint yet")
    content = services.coordinator.read_artifact(str(checkpoint.artifact_id))
    curve = experiments.training_curve(experiment.experiment_id)
    config = experiment.config
    game = config.preset_key.removesuffix("-train") if config.tournament_json else "hu-cash-100bb"
    training: dict[str, Any] = {
        key: value.value if hasattr(value, "value") else value
        for key, value in asdict(config).items()
        if key != "schema_version"
    }
    training["experiment_id"] = str(experiment.experiment_id)
    training["round"] = checkpoint.round_index
    return library(services).add(
        model_id,
        content,
        "nfsp",
        game,
        description=description,
        training=training,
        evaluation={"curve": curve[-1] if curve else None},
        source=f"experiment {experiment.experiment_id}, round {checkpoint.round_index}",
    )
