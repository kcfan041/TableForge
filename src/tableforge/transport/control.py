from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import NoReturn, Protocol, TypeVar

import grpc

from tableforge._generated.tableforge.v1 import control_pb2, control_pb2_grpc
from tableforge.application.ports import (
    AuthRepository,
    CheckpointRepository,
    IdempotencyConflictError,
    JobNotFoundError,
)
from tableforge.coordinator.experiments import ExperimentService
from tableforge.domain.auth import AuthenticationError, OperatorIdentity, OperatorRole
from tableforge.domain.experiments import BUILTIN_PRESETS, Experiment
from tableforge.domain.ids import AgentId, ExperimentId
from tableforge.transport import convert
from tableforge.transport.errors import ERROR_METADATA_KEY

T = TypeVar("T")


class Clock(Protocol):
    def now(self) -> datetime: ...


def _abort(
    context: grpc.ServicerContext, status: grpc.StatusCode, code: str, message: str
) -> NoReturn:
    context.set_trailing_metadata(((ERROR_METADATA_KEY, code),))
    context.abort(status, message)
    raise AssertionError("unreachable")


class ControlServicer(control_pb2_grpc.ControlServiceServicer):
    """Remote control plane. Only operator credentials are accepted; node sessions are not."""

    def __init__(
        self,
        experiments: ExperimentService,
        checkpoints: CheckpointRepository,
        auth: AuthRepository,
        clock: Clock,
    ) -> None:
        self._experiments = experiments
        self._checkpoints = checkpoints
        self._auth = auth
        self._clock = clock

    def _operator(self, context: grpc.ServicerContext, required: OperatorRole) -> OperatorIdentity:
        header = dict(context.invocation_metadata()).get("authorization", "")
        if not isinstance(header, str) or not header.startswith("Bearer "):
            _abort(context, grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", "missing token")
        try:
            identity = self._auth.authenticate_operator(
                header.removeprefix("Bearer "), self._clock.now()
            )
        except AuthenticationError as error:
            _abort(context, grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", str(error))
        if not identity.role.allows(required):
            _abort(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "ROLE_REQUIRED",
                f"requires the {required.value} role; this credential is {identity.role.value}",
            )
        return identity

    def _run(self, context: grpc.ServicerContext, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except JobNotFoundError as error:
            _abort(context, grpc.StatusCode.NOT_FOUND, "NOT_FOUND", str(error))
        except IdempotencyConflictError as error:
            _abort(context, grpc.StatusCode.ABORTED, "CONFLICT", str(error))
        except ValueError as error:
            _abort(context, grpc.StatusCode.FAILED_PRECONDITION, "INVALID_STATE", str(error))

    def _view(self, experiment: Experiment) -> control_pb2.ExperimentView:
        jobs = self._experiments.coordinator.jobs.list_jobs(experiment.experiment_id)
        counts = Counter(job.status.value for job in jobs)
        evaluation = self._experiments.evaluation_result(experiment.experiment_id)
        return control_pb2.ExperimentView(
            experiment_id=str(experiment.experiment_id),
            preset=experiment.config.preset_key,
            status=experiment.status.value,
            revision=experiment.revision,
            job_counts=dict(counts),
            created_at=convert.timestamp_to_proto(experiment.created_at),
            updated_at=convert.timestamp_to_proto(experiment.updated_at),
            evaluation_json=json.dumps(evaluation, sort_keys=True) if evaluation else "",
        )

    def WhoAmI(
        self, request: control_pb2.WhoAmIRequest, context: grpc.ServicerContext
    ) -> control_pb2.WhoAmIResponse:
        identity = self._operator(context, OperatorRole.VIEWER)
        return control_pb2.WhoAmIResponse(
            cluster_id=str(self._auth.cluster_id(self._clock.now())),
            operator_id=str(identity.operator_id),
            role=identity.role.value,
            label=identity.label,
        )

    def ListPresets(
        self, request: control_pb2.ListPresetsRequest, context: grpc.ServicerContext
    ) -> control_pb2.ListPresetsResponse:
        self._operator(context, OperatorRole.VIEWER)
        return control_pb2.ListPresetsResponse(presets=[preset.key for preset in BUILTIN_PRESETS])

    def ListExperiments(
        self, request: control_pb2.ListExperimentsRequest, context: grpc.ServicerContext
    ) -> control_pb2.ListExperimentsResponse:
        self._operator(context, OperatorRole.VIEWER)
        experiments = self._experiments.list_experiments()
        return control_pb2.ListExperimentsResponse(
            experiments=[self._view(experiment) for experiment in experiments]
        )

    def GetExperiment(
        self, request: control_pb2.GetExperimentRequest, context: grpc.ServicerContext
    ) -> control_pb2.ExperimentView:
        self._operator(context, OperatorRole.VIEWER)
        experiment = self._run(
            context,
            lambda: self._experiments.experiments.get(ExperimentId(request.experiment_id)),
        )
        return self._view(experiment)

    def CreateExperiment(
        self, request: control_pb2.CreateExperimentRequest, context: grpc.ServicerContext
    ) -> control_pb2.ExperimentView:
        self._operator(context, OperatorRole.OPERATOR)
        experiment = self._run(
            context, lambda: self._experiments.create(request.preset, self._clock.now())
        )
        return self._view(experiment)

    def StartExperiment(
        self, request: control_pb2.ExperimentRequest, context: grpc.ServicerContext
    ) -> control_pb2.ExperimentView:
        self._operator(context, OperatorRole.OPERATOR)
        experiment = self._run(
            context,
            lambda: self._experiments.start(ExperimentId(request.experiment_id), self._clock.now()),
        )
        return self._view(experiment)

    def StopExperiment(
        self, request: control_pb2.ExperimentRequest, context: grpc.ServicerContext
    ) -> control_pb2.ExperimentView:
        self._operator(context, OperatorRole.OPERATOR)
        experiment = self._run(
            context,
            lambda: self._experiments.stop(ExperimentId(request.experiment_id), self._clock.now()),
        )
        return self._view(experiment)

    def ListCheckpoints(
        self, request: control_pb2.ListCheckpointsRequest, context: grpc.ServicerContext
    ) -> control_pb2.ListCheckpointsResponse:
        self._operator(context, OperatorRole.VIEWER)
        agent = AgentId(request.agent_id) if request.agent_id else None
        return control_pb2.ListCheckpointsResponse(
            checkpoints=[
                control_pb2.CheckpointView(
                    checkpoint_id=str(item.checkpoint_id),
                    agent_id=str(item.agent_id),
                    experiment_id=str(item.experiment_id),
                    round_index=item.round_index,
                    status=item.status.value,
                    decision_reason=item.decision_reason,
                    sha256=item.sha256,
                    parent_id=str(item.parent_id) if item.parent_id else "",
                )
                for item in self._checkpoints.list_checkpoints(agent)
            ]
        )

    def GetLeagueMatrix(
        self, request: control_pb2.ExperimentRequest, context: grpc.ServicerContext
    ) -> control_pb2.LeagueMatrixView:
        self._operator(context, OperatorRole.VIEWER)
        matrix = self._run(
            context,
            lambda: self._experiments.league_matrix(ExperimentId(request.experiment_id)),
        )
        if matrix is None:
            return control_pb2.LeagueMatrixView(available=False)
        encoded = {
            row: {column: asdict(cell) for column, cell in cells.items()}
            for row, cells in matrix.items()
        }
        return control_pb2.LeagueMatrixView(
            available=True, matrix_json=json.dumps(encoded, sort_keys=True)
        )

    def ListNodes(
        self, request: control_pb2.ListNodesRequest, context: grpc.ServicerContext
    ) -> control_pb2.ListNodesResponse:
        self._operator(context, OperatorRole.VIEWER)
        return control_pb2.ListNodesResponse(
            nodes=[
                control_pb2.NodeView(
                    node_id=str(node.node_id),
                    capability_revision=node.capability.revision,
                    last_heartbeat_at=convert.timestamp_to_proto(node.last_heartbeat_at),
                    operating_system=node.capability.operating_system,
                    logical_cpu_cores=node.capability.logical_cpu_cores or 0,
                    gpus=[f"{gpu.device_id} {gpu.model}".strip() for gpu in node.capability.gpus],
                )
                for node in self._experiments.coordinator.list_nodes()
            ]
        )
