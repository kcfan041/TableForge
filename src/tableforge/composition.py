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
from tableforge.infrastructure.sql import (
    Database,
    SqlAuthRepository,
    SqlCheckpointRepository,
    SqlEventLog,
    SqlExperimentRepository,
    SQLiteDatabase,
    SqlJobRepository,
    SqlNodeRepository,
)
from tableforge.settings import Settings


@dataclass(frozen=True, slots=True)
class Repositories:
    database: Database
    jobs: JobRepository
    nodes: NodeRepository
    experiments: ExperimentRepository
    checkpoints: CheckpointRepository
    auth: AuthRepository
    events: SqlEventLog


def open_database(settings: Settings) -> Database:
    if settings.metadata.backend == "postgres":
        from tableforge.infrastructure.sql.postgres import PostgresDatabase

        return PostgresDatabase(settings.metadata.url)
    return SQLiteDatabase(settings.database_path)


def build_repositories(settings: Settings) -> Repositories:
    return repositories_for(open_database(settings))


def repositories_for(database: Database) -> Repositories:
    return Repositories(
        database=database,
        jobs=SqlJobRepository(database),
        nodes=SqlNodeRepository(database),
        experiments=SqlExperimentRepository(database),
        checkpoints=SqlCheckpointRepository(database),
        auth=SqlAuthRepository(database),
        events=SqlEventLog(database),
    )


def build_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.artifacts.backend == "s3":
        from tableforge.infrastructure.s3_artifacts import S3ArtifactStore

        return S3ArtifactStore.from_settings(settings.artifacts)
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
        direct_transfer=settings.artifacts.direct_transfer,
        presign_ttl=timedelta(seconds=settings.artifacts.presign_seconds),
    )
    return Services(
        settings=settings,
        repositories=repositories,
        coordinator=coordinator,
        experiments=ExperimentService(
            experiments=repositories.experiments,
            coordinator=coordinator,
            checkpoints=repositories.checkpoints,
            models_root=settings.data_dir / "models",
        ),
    )
