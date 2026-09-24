from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from tableforge.application.ports import (
    ArtifactStore,
    AuthRepository,
    CheckpointRepository,
    ExperimentRepository,
    JobRepository,
    NodeRepository,
)
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.experiments import ExperimentService
from tableforge.coordinator.service import CoordinatorService
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.auth import SQLiteAuthRepository
from tableforge.infrastructure.sqlite.checkpoints import SQLiteCheckpointRepository
from tableforge.infrastructure.sqlite.experiments import SQLiteExperimentRepository
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository
from tableforge.settings import Settings


@dataclass(frozen=True, slots=True)
class Repositories:
    jobs: JobRepository
    nodes: NodeRepository
    experiments: ExperimentRepository
    checkpoints: CheckpointRepository
    auth: AuthRepository


def build_repositories(settings: Settings) -> Repositories:
    if settings.metadata.backend != "sqlite":
        raise NotImplementedError(f"metadata backend '{settings.metadata.backend}'")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = settings.database_path
    return Repositories(
        jobs=SQLiteJobRepository(database),
        nodes=SQLiteNodeRepository(database),
        experiments=SQLiteExperimentRepository(database),
        checkpoints=SQLiteCheckpointRepository(database),
        auth=SQLiteAuthRepository(database),
    )


def build_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.artifacts.backend != "local":
        raise NotImplementedError(f"artifact backend '{settings.artifacts.backend}'")
    return LocalArtifactStore(settings.data_dir / "artifacts")


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    repositories: Repositories
    coordinator: CoordinatorService
    experiments: ExperimentService


def build_services(settings: Settings) -> Services:
    repositories = build_repositories(settings)
    coordinator = CoordinatorService(
        jobs=repositories.jobs,
        artifacts=build_artifact_store(settings),
        workloads=BUILTIN_WORKLOADS,
        nodes=repositories.nodes,
        node_ttl=timedelta(seconds=settings.coordinator.node_ttl_seconds),
    )
    return Services(
        settings=settings,
        repositories=repositories,
        coordinator=coordinator,
        experiments=ExperimentService(
            experiments=repositories.experiments,
            coordinator=coordinator,
            checkpoints=repositories.checkpoints,
        ),
    )
