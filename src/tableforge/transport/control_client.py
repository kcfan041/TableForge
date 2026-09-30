from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from typing import Any, TypeVar

import grpc

from tableforge._generated.tableforge.v1 import control_pb2, control_pb2_grpc
from tableforge.models import ModelInfo, ModelLibrary
from tableforge.transport.errors import ERROR_METADATA_KEY
from tableforge.transport.join import OperatorCredentials

T = TypeVar("T")


class ControlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(error: grpc.RpcError) -> ControlError:
    code = ""
    for item in (error.trailing_metadata() or ()) if hasattr(error, "trailing_metadata") else ():
        if item.key == ERROR_METADATA_KEY:
            code = str(item.value)
    status = error.code() if hasattr(error, "code") else None
    details = error.details() if hasattr(error, "details") else str(error)
    return ControlError(code or (status.name if status else "UNKNOWN"), str(details))


class RemoteControl:
    """Operator-side client for the coordinator's ControlService."""

    def __init__(self, credentials: OperatorCredentials, timeout: float = 30.0) -> None:
        self.credentials = credentials
        self._stub = control_pb2_grpc.ControlServiceStub(credentials.channel())
        self._metadata = (("authorization", f"Bearer {credentials.token}"),)
        self._timeout = timeout

    def _call(self, operation: Callable[..., T], request: Any) -> T:
        try:
            return operation(request, metadata=self._metadata, timeout=self._timeout)
        except grpc.RpcError as error:
            raise _error(error) from error

    def whoami(self) -> control_pb2.WhoAmIResponse:
        return self._call(self._stub.WhoAmI, control_pb2.WhoAmIRequest())

    def presets(self) -> list[str]:
        return list(self._call(self._stub.ListPresets, control_pb2.ListPresetsRequest()).presets)

    def list_experiments(self) -> list[control_pb2.ExperimentView]:
        response = self._call(self._stub.ListExperiments, control_pb2.ListExperimentsRequest())
        return list(response.experiments)

    def get(self, experiment_id: str) -> control_pb2.ExperimentView:
        return self._call(
            self._stub.GetExperiment,
            control_pb2.GetExperimentRequest(experiment_id=experiment_id),
        )

    def create(self, preset: str) -> control_pb2.ExperimentView:
        return self._call(
            self._stub.CreateExperiment, control_pb2.CreateExperimentRequest(preset=preset)
        )

    def start(self, experiment_id: str) -> control_pb2.ExperimentView:
        return self._call(
            self._stub.StartExperiment, control_pb2.ExperimentRequest(experiment_id=experiment_id)
        )

    def stop(self, experiment_id: str) -> control_pb2.ExperimentView:
        return self._call(
            self._stub.StopExperiment, control_pb2.ExperimentRequest(experiment_id=experiment_id)
        )

    def checkpoints(self, agent_id: str = "") -> list[control_pb2.CheckpointView]:
        response = self._call(
            self._stub.ListCheckpoints, control_pb2.ListCheckpointsRequest(agent_id=agent_id)
        )
        return list(response.checkpoints)

    def matrix(self, experiment_id: str) -> dict[str, Any] | None:
        response = self._call(
            self._stub.GetLeagueMatrix, control_pb2.ExperimentRequest(experiment_id=experiment_id)
        )
        if not response.available:
            return None
        decoded: dict[str, Any] = json.loads(response.matrix_json)
        return decoded

    def nodes(self) -> list[control_pb2.NodeView]:
        return list(self._call(self._stub.ListNodes, control_pb2.ListNodesRequest()).nodes)

    def query(self, resource: str, argument: str = "") -> Any:
        response = self._call(
            self._stub.Query, control_pb2.QueryRequest(resource=resource, argument=argument)
        )
        return json.loads(response.json)

    def command(self, action: str, arguments: dict[str, Any]) -> Any:
        response = self._call(
            self._stub.Command,
            control_pb2.CommandRequest(action=action, json=json.dumps(arguments)),
        )
        return json.loads(response.json)


class DownloadError(Exception):
    pass


def download_model(
    command: Callable[[str, dict[str, Any]], Any],
    model_id: str,
    library: ModelLibrary,
    on_progress: Callable[[int, int], None] | None = None,
    replace: bool = False,
) -> ModelInfo:
    """Copy a cluster model into `library`; the checkpoint must match the cluster's sha256.

    `command` sends one control command (``RemoteControl.command`` or the web API).
    """
    parts: list[bytes] = []
    received = 0
    first: dict[str, Any] | None = None
    for _ in range(100_000):
        chunk = command("download_model", {"model_id": model_id, "offset": received})
        if first is None:
            first = chunk
        elif chunk["sha256"] != first["sha256"]:
            raise DownloadError("the model changed on the cluster during the download; try again")
        if int(chunk["offset"]) != received:
            raise DownloadError("the cluster sent a chunk from the wrong place")
        data = base64.b64decode(str(chunk["data"]), validate=True)
        parts.append(data)
        received += len(data)
        if on_progress is not None:
            on_progress(received, int(first["size_bytes"]))
        if chunk["done"]:
            break
        if not data:
            raise DownloadError("the cluster sent an empty chunk")
    else:
        raise DownloadError("the download did not finish")
    assert first is not None
    content = b"".join(parts)
    if len(content) != int(first["size_bytes"]):
        raise DownloadError("the downloaded checkpoint has the wrong size")
    if hashlib.sha256(content).hexdigest() != first["sha256"]:
        raise DownloadError("the downloaded checkpoint does not match its sha256")
    model = first["model"]
    return library.add(
        model_id,
        content,
        str(model["algorithm"]),
        str(model["game"]),
        description=str(model.get("description", "")),
        training=dict(model.get("training") or {}),
        evaluation=dict(model.get("evaluation") or {}),
        source=f"downloaded from cluster ({model.get('source', '')})".replace(" ()", ""),
        replace=replace,
    )


def experiment_json(view: control_pb2.ExperimentView) -> dict[str, object]:
    report: dict[str, object] = {
        "experiment_id": view.experiment_id,
        "jobs": dict(sorted(view.job_counts.items())),
        "preset": view.preset,
        "revision": view.revision,
        "status": view.status,
    }
    if view.evaluation_json:
        report["evaluation"] = json.loads(view.evaluation_json)
    return report
