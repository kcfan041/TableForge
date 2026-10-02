"""Cluster state and actions as JSON-ready documents.

One implementation serves every way of managing a cluster: the coordinator's HTTP API,
the gRPC ``ControlService`` (``Query``/``Command``) used by ``tableforge ui`` on any
machine, and the CLI. Callers check the role first; this module only builds documents
and performs the action.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from tableforge.composition import Services
from tableforge.domain.auth import (
    ROLE_SCOPES,
    InviteScope,
    OperatorIdentity,
    OperatorRole,
    Scope,
    parse_scopes,
)
from tableforge.domain.experiments import BUILTIN_PRESETS, OBSERVATION_SCHEMAS, Experiment
from tableforge.domain.ids import AgentId, ExperimentId, InviteId, NodeId, OperatorId
from tableforge.infrastructure.sql.events import DomainEvent
from tableforge.infrastructure.sql.migrations import schema_version
from tableforge.transport.invite_issue import coordinator_tls, issue_invite
from tableforge.version import MIN_PROTOCOL_VERSION, PROTOCOL_VERSION, package_version


class Clock(Protocol):
    def now(self) -> datetime: ...


class ViewError(Exception):
    """A request the caller can fix (unknown id, invalid state); ``status`` is HTTP-like."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


DOWNLOAD_CHUNK = 2 * 1024 * 1024
"""Bytes per model download chunk (base64 grows it by a third; gRPC allows 8 MB)."""

# Scope required by each query and command. Anything not listed is rejected.
_READ = Scope.EXPERIMENT_READ
QUERY_SCOPES: dict[str, Scope] = {
    "overview": _READ,
    "nodes": _READ,
    "presets": _READ,
    "training_options": _READ,
    "experiments": _READ,
    "experiment": _READ,
    "matrix": _READ,
    "checkpoints": _READ,
    "events": _READ,
    "events_after": _READ,
    "diagnostics": _READ,
    "invites": Scope.ADMIN_MANAGE,
    "operators": Scope.ADMIN_MANAGE,
    "scopes": _READ,
    "models": Scope.MODEL_READ,
    "analysis": Scope.MODEL_READ,
    "comparison": Scope.MODEL_READ,
    "comparison_candidates": Scope.MODEL_READ,
}
COMMAND_SCOPES: dict[str, Scope] = {
    "create_experiment": Scope.EXPERIMENT_CONTROL,
    "start_experiment": Scope.EXPERIMENT_CONTROL,
    "stop_experiment": Scope.EXPERIMENT_CONTROL,
    "create_invite": Scope.ADMIN_MANAGE,
    "revoke_invite": Scope.ADMIN_MANAGE,
    "revoke_operator": Scope.ADMIN_MANAGE,
    "revoke_node": Scope.ADMIN_MANAGE,
    "set_operator_scopes": Scope.ADMIN_MANAGE,
    "save_model": Scope.MODEL_MANAGE,
    "remove_model": Scope.ADMIN_MANAGE,
    # Copy a model to your own computer (in chunks; the receiver checks the sha256).
    "download_model": Scope.MODEL_DOWNLOAD,
    "request_replay": Scope.ANALYSIS_RUN,
    "request_strategy": Scope.ANALYSIS_RUN,
    "request_comparison": Scope.ANALYSIS_RUN,
    # Games against models on the cluster host; each game belongs to its creator.
    "play_start": Scope.PLAY_REMOTE,
    "play_list": Scope.PLAY_REMOTE,
    "play_act": Scope.PLAY_REMOTE,
    "play_state": Scope.PLAY_REMOTE,
    "play_replay": Scope.PLAY_REMOTE,
    "play_history": Scope.PLAY_REMOTE,
    "play_report": Scope.PLAY_REMOTE,
    "play_close": Scope.PLAY_REMOTE,
}


def missing_scope(required: Scope, identity: OperatorIdentity) -> str | None:
    """Why `identity` may not do something that needs `required` (None when it may)."""
    if identity.has(required):
        return None
    return f"requires the {required.value} permission, which this credential does not have"


@dataclass(frozen=True)
class ClusterViews:
    services: Services
    clock: Clock

    # --- queries -------------------------------------------------------------------------

    def query(self, resource: str, argument: str, caller: OperatorIdentity) -> Any:
        if resource == "overview":
            return self.overview()
        if resource == "nodes":
            return self.nodes()
        if resource == "presets":
            return self.presets()
        if resource == "training_options":
            return self.training_options()
        if resource == "experiments":
            return self.experiments()
        if resource == "experiment":
            return self.experiment(argument)
        if resource == "matrix":
            return self.matrix(argument)
        if resource == "checkpoints":
            return self.checkpoints(argument)
        if resource == "events":
            return self.events(_int(argument, 100))
        if resource == "events_after":
            after = _int(argument, -1)
            if after < 0:
                return {"latest": self.services.repositories.events.latest_sequence(), "events": []}
            return {"latest": after, "events": self.events_after(after)}
        if resource == "diagnostics":
            return self.diagnostics()
        if resource == "invites":
            return self.invites()
        if resource == "operators":
            return self.operators(caller)
        if resource == "scopes":
            return self.scope_catalog(caller)
        if resource == "models":
            from tableforge.coordinator.models import library, model_view

            return [model_view(info) for info in library(self.services).list()]
        if resource == "analysis":
            from tableforge.application.ports import JobNotFoundError
            from tableforge.coordinator.analysis import AnalysisError, analysis_result

            try:
                return analysis_result(self.services, argument).as_dict()
            except (JobNotFoundError, KeyError) as error:
                raise ViewError(404, "no such analysis job") from error
            except AnalysisError as error:
                raise ViewError(400, str(error)) from error
        if resource in ("comparison", "comparison_candidates"):
            from tableforge.application.ports import JobNotFoundError
            from tableforge.coordinator.analysis import (
                AnalysisError,
                comparison_candidates,
                comparison_result,
            )

            try:
                if resource == "comparison_candidates":
                    return comparison_candidates(self.services, argument)
                return comparison_result(self.services, argument)
            except (JobNotFoundError, KeyError) as error:
                raise ViewError(404, "no such comparison") from error
            except AnalysisError as error:
                raise ViewError(400, str(error)) from error
        raise ViewError(404, f"unknown resource: {resource}")

    def overview(self) -> dict[str, object]:
        now = self.clock.now()
        nodes = self.services.coordinator.list_nodes()
        online = [node for node in nodes if self._online(node.last_heartbeat_at, now)]
        jobs = self.services.coordinator.jobs.list_jobs()
        experiments = self.services.experiments.list_experiments()
        return {
            "cluster_id": str(self.services.repositories.auth.cluster_id(now)),
            "version": package_version(),
            "nodes": {"total": len(nodes), "online": len(online)},
            "gpus": sum(len(node.capability.gpus) for node in online),
            "experiments": dict(Counter(item.status.value for item in experiments)),
            "jobs": dict(Counter(job.status.value for job in jobs)),
            "latest_event": self.services.repositories.events.latest_sequence(),
        }

    def nodes(self) -> list[dict[str, object]]:
        now = self.clock.now()
        workers: dict[str, list[dict[str, object]]] = {}
        for (
            worker_id,
            node_id,
            label,
            last_seen,
        ) in self.services.repositories.auth.list_worker_slots():
            workers.setdefault(str(node_id), []).append(
                {"worker_id": str(worker_id), "label": label, "last_seen_at": _iso(last_seen)}
            )
        return [
            {
                "node_id": str(node.node_id),
                "online": self._online(node.last_heartbeat_at, now),
                "last_heartbeat_at": _iso(node.last_heartbeat_at),
                "registered_at": _iso(node.registered_at),
                "operating_system": node.capability.operating_system,
                "architecture": node.capability.architecture,
                "logical_cpu_cores": node.capability.logical_cpu_cores,
                "total_memory_bytes": node.capability.total_memory_bytes,
                "available_memory_bytes": node.capability.available_memory_bytes,
                "roles": list(node.capability.enabled_roles),
                "runtimes": list(node.capability.runtime.runtimes),
                "python_version": node.capability.runtime.python_version,
                "capability_revision": node.capability.revision,
                "warnings": list(node.capability.warnings),
                "gpus": [
                    {
                        "device_id": gpu.device_id,
                        "model": gpu.model,
                        "total_memory_bytes": gpu.total_memory_bytes,
                        "available_memory_bytes": gpu.available_memory_bytes,
                        "driver_version": gpu.driver_version,
                        "compute_capability": gpu.compute_capability,
                    }
                    for gpu in node.capability.gpus
                ],
                "workers": workers.get(str(node.node_id), []),
            }
            for node in self.services.coordinator.list_nodes()
        ]

    def presets(self) -> list[dict[str, object]]:
        return [
            {
                "key": preset.key,
                "version": preset.version,
                "kind": preset.kind.value,
                "hands": preset.hands,
                "hands_per_job": preset.hands_per_job,
                "players": preset.players,
                "rounds": preset.rounds,
                "league": preset.league,
                "learner_runtime": preset.learner_runtime,
                "observation_schema": preset.observation_schema,
                "starting_stack_bb": _stack_bb(preset.tournament_json),
            }
            for preset in BUILTIN_PRESETS
        ]

    def experiments(self) -> list[dict[str, object]]:
        items = sorted(
            self.services.experiments.list_experiments(),
            key=lambda item: item.created_at,
            reverse=True,
        )
        return [self._experiment_view(item) for item in items]

    def experiment(self, experiment_id: str) -> dict[str, object]:
        found = self.services.experiments.experiments.get(ExperimentId(experiment_id))
        return self._experiment_view(found, detailed=True)

    def matrix(self, experiment_id: str) -> dict[str, object]:
        found = self.services.experiments.experiments.get(ExperimentId(experiment_id))
        if not found.config.league:
            return {"available": False, "reason": "not_league"}
        cells = self.services.experiments.league_matrix(found.experiment_id)
        if cells is None:
            return {"available": False, "reason": "no_matchups"}
        return {
            "available": True,
            "players": sorted(cells),
            "cells": {
                row: {column: asdict(cell) for column, cell in columns.items()}
                for row, columns in cells.items()
            },
        }

    def checkpoints(self, agent: str = "") -> list[dict[str, object]]:
        items = self.services.repositories.checkpoints.list_checkpoints(
            AgentId(agent) if agent else None
        )
        return [
            {
                "checkpoint_id": str(item.checkpoint_id),
                "agent_id": str(item.agent_id),
                "experiment_id": str(item.experiment_id),
                "round_index": item.round_index,
                "status": item.status.value,
                "decision_reason": item.decision_reason,
                "parent_id": str(item.parent_id) if item.parent_id else None,
                "sha256": item.sha256,
                "created_at": _iso(item.created_at),
                "updated_at": _iso(item.updated_at),
            }
            for item in sorted(items, key=lambda item: item.created_at, reverse=True)
        ]

    def events(self, limit: int = 100) -> list[dict[str, object]]:
        limit = max(1, min(limit, 500))
        return [event_view(item) for item in self.services.repositories.events.recent_events(limit)]

    def events_after(self, after: int, limit: int = 100) -> list[dict[str, object]]:
        return [
            event_view(item)
            for item in self.services.repositories.events.list_events(
                after, max(1, min(limit, 500))
            )
        ]

    def diagnostics(self) -> dict[str, object]:
        settings = self.services.settings
        material = coordinator_tls(settings)
        certificate: dict[str, object] = {"present": material.certificate_path.is_file()}
        if material.certificate_path.is_file():
            certificate["hosts"] = list(material.hosts())
            certificate["fingerprint"] = material.fingerprint()
        return {
            "version": package_version(),
            "protocol": PROTOCOL_VERSION,
            "min_protocol": MIN_PROTOCOL_VERSION,
            "schema_version": schema_version(self.services.repositories.database),
            "metadata_backend": settings.metadata.backend,
            "artifact_backend": settings.artifacts.backend,
            "direct_transfer": settings.artifacts.direct_transfer,
            "grpc_listen": settings.coordinator.listen,
            "http_listen": settings.coordinator.http_listen,
            "node_ttl_seconds": settings.coordinator.node_ttl_seconds,
            "certificate": certificate,
            "unpublished_events": self.services.repositories.events.unpublished_count(),
            "advertised_address": self.advertised_address(),
        }

    def advertised_address(self) -> str:
        """The address remote machines should use: the first non-loopback certificate host."""
        material = coordinator_tls(self.services.settings)
        port = self.services.settings.coordinator.listen.rpartition(":")[2] or "7443"
        if not material.certificate_path.is_file():
            return ""
        hosts = [host for host in material.hosts() if host not in ("localhost", "127.0.0.1")]
        return f"{(hosts or ['127.0.0.1'])[0]}:{port}"

    def invites(self) -> list[dict[str, object]]:
        now = self.clock.now()
        auth = self.services.repositories.auth
        return [
            {
                "invite_id": str(item.invite_id),
                "scope": item.scope.encode(),
                "role": item.scope.operator_role.value if item.scope.operator_role else None,
                "scopes": sorted(scope.value for scope in item.scope.scopes),
                "node": item.scope.node,
                "label": item.label,
                "created_at": _iso(item.created_at),
                "expires_at": _iso(item.expires_at),
                "redeemed_at": _iso(item.redeemed_at) if item.redeemed_at else None,
                "redeemed_by": item.redeemed_by,
                "state": "revoked"
                if item.revoked
                else "used"
                if item.redeemed_at
                else "expired"
                if not item.usable(now)
                else "open",
            }
            for item in sorted(auth.list_invites(), key=lambda item: item.created_at, reverse=True)
        ]

    def scope_catalog(self, caller: OperatorIdentity) -> dict[str, object]:
        """Every scope, the role presets, and the caller's own scopes (for the UI)."""
        return {
            "scopes": [scope.value for scope in Scope],
            "roles": {
                role.value: sorted(scope.value for scope in scopes)
                for role, scopes in ROLE_SCOPES.items()
                if role is not OperatorRole.CUSTOM
            },
            "mine": caller.scope_list(),
        }

    def operators(self, caller: OperatorIdentity) -> list[dict[str, object]]:
        return [
            {
                "operator_id": str(record.identity.operator_id),
                "role": record.identity.role.value,
                "scopes": record.identity.scope_list(),
                "label": record.identity.label,
                "created_at": _iso(record.created_at),
                "expires_at": _iso(record.expires_at),
                "current": record.identity.operator_id == caller.operator_id,
            }
            for record in self.services.repositories.auth.list_operator_records()
        ]

    # --- commands ------------------------------------------------------------------------

    def command(self, action: str, arguments: dict[str, Any], caller: OperatorIdentity) -> Any:
        try:
            return self._command(action, arguments, caller)
        except KeyError as error:
            raise ViewError(400, f"missing argument: {error.args[0]}") from error

    def _command(self, action: str, arguments: dict[str, Any], caller: OperatorIdentity) -> Any:
        now = self.clock.now()
        experiments = self.services.experiments
        auth = self.services.repositories.auth
        if action == "create_experiment":
            return self.create_experiment(arguments, now)
        if action == "start_experiment":
            started = experiments.start(ExperimentId(str(arguments["experiment_id"])), now)
            return self._experiment_view(started)
        if action == "stop_experiment":
            stopped = experiments.stop(ExperimentId(str(arguments["experiment_id"])), now)
            return self._experiment_view(stopped)
        if action == "create_invite":
            return self.create_invite(arguments)
        if action == "revoke_invite":
            if not auth.revoke_invite(InviteId(str(arguments["invite_id"]))):
                raise ViewError(404, "no open invite with that id")
            return {"ok": True}
        if action == "revoke_operator":
            operator_id = str(arguments["operator_id"])
            if operator_id == str(caller.operator_id):
                raise ViewError(400, "you cannot revoke the credential you are signed in with")
            try:
                revoked = auth.revoke_operator(OperatorId(operator_id))
            except ValueError as error:
                raise ViewError(409, str(error)) from error
            if not revoked:
                raise ViewError(404, "no active credential with that id")
            return {"ok": True}
        if action == "set_operator_scopes":
            return self.set_operator_scopes(arguments, caller, now)
        if action == "revoke_node":
            return {"revoked_sessions": auth.revoke_node(NodeId(str(arguments["node_id"])))}
        if action in ("save_model", "remove_model"):
            return self.model_command(action, arguments)
        if action == "download_model":
            return self.download_model(arguments)
        if action.startswith("play_"):
            return self.play_command(action, arguments, caller)
        if action in ("request_replay", "request_strategy", "request_comparison"):
            from tableforge.coordinator.analysis import (
                AnalysisError,
                request_comparison,
                request_replay,
                request_strategy,
            )

            handler = {
                "request_replay": request_replay,
                "request_strategy": request_strategy,
                "request_comparison": request_comparison,
            }[action]
            try:
                return handler(self.services, arguments)
            except KeyError as error:
                raise ViewError(404, f"no such model: {error}") from error
            except (AnalysisError, ValueError, TypeError) as error:
                raise ViewError(400, str(error)) from error
        raise ViewError(404, f"unknown action: {action}")

    def play_command(
        self, action: str, arguments: dict[str, Any], caller: OperatorIdentity
    ) -> object:
        from tableforge.coordinator.play import play_command, remote_play
        from tableforge.learning.play import PlayError

        service, seats = remote_play(self.services.settings.data_dir)
        try:
            return play_command(service, seats, str(caller.operator_id), action, arguments)
        except KeyError as error:
            raise ViewError(404, "no such game (it may have expired)") from error
        except (PlayError, TypeError, ValueError) as error:
            raise ViewError(400, str(error)) from error

    def download_model(self, arguments: dict[str, Any]) -> dict[str, object]:
        """One chunk of a model's checkpoint, base64 encoded, with its description.

        The first call (offset 0) also returns the model description; the caller joins the
        chunks and checks the result against `sha256` before using it.
        """
        import base64

        from tableforge.coordinator.models import library, model_view

        model_id = str(arguments.get("model_id", "")).strip()
        offset = _int(str(arguments.get("offset", 0)), 0)
        try:
            info = library(self.services).info(model_id)
            content = library(self.services).checkpoint(model_id)
        except KeyError as error:
            raise ViewError(404, f"no such model: {model_id}") from error
        except ValueError as error:
            raise ViewError(409, str(error)) from error
        if not 0 <= offset <= len(content):
            raise ViewError(400, "offset is outside the checkpoint")
        chunk = content[offset : offset + DOWNLOAD_CHUNK]
        document: dict[str, object] = {
            "model_id": info.model_id,
            "sha256": info.sha256,
            "size_bytes": len(content),
            "offset": offset,
            "data": base64.b64encode(chunk).decode("ascii"),
            "done": offset + len(chunk) >= len(content),
        }
        if offset == 0:
            document["model"] = model_view(info)
        return document

    def model_command(self, action: str, arguments: dict[str, Any]) -> object:
        from tableforge.coordinator.models import library, model_view, save_experiment_model

        model_id = str(arguments.get("model_id", "")).strip()
        try:
            if action == "remove_model":
                library(self.services).remove(model_id)
                return {"ok": True}
            info = save_experiment_model(
                self.services,
                str(arguments["experiment_id"]),
                model_id,
                str(arguments.get("description", ""))[:300],
            )
        except KeyError as error:
            raise ViewError(404, f"no such model or experiment: {error}") from error
        except FileExistsError as error:
            raise ViewError(409, str(error)) from error
        except ValueError as error:
            raise ViewError(400, str(error)) from error
        return model_view(info)

    def create_experiment(self, arguments: dict[str, Any], now: datetime) -> object:
        """Create one experiment, or a group of experiments with the given seeds.

        ``settings`` changes training settings of iterative NFSP presets; ``seeds`` (a list)
        creates one experiment per seed that share a group id.
        """
        preset = str(arguments["preset"])
        settings = arguments.get("settings") or {}
        if not isinstance(settings, dict):
            raise ViewError(400, "settings must be an object")
        seeds = arguments.get("seeds")
        experiments = self.services.experiments
        try:
            if seeds:
                if not isinstance(seeds, list):
                    raise ViewError(400, "seeds must be a list of numbers")
                created = experiments.create_group(
                    preset, [int(seed) for seed in seeds], now, settings
                )
                return {"group": [self._experiment_view(item) for item in created]}
            return self._experiment_view(experiments.create(preset, now, settings))
        except (TypeError, ValueError) as error:
            raise ViewError(400, str(error)) from error

    def training_options(self) -> dict[str, object]:
        """Settings that can be changed for each iterative NFSP preset, with their defaults."""
        from dataclasses import asdict

        from tableforge.coordinator.experiments import (
            ALGORITHM_ONLY_SETTINGS,
            OVERRIDE_LIMITS,
            TOURNAMENT_FIELDS,
            TRAINING_OVERRIDES,
        )
        from tableforge.domain.experiments import ExperimentKind
        from tableforge.poker.tournament import TournamentConfig
        from tableforge.poker.tournament_spec import spec_from_config

        presets = {}
        tournaments = {}
        for preset in BUILTIN_PRESETS:
            if preset.tournament_json:
                config_view = spec_from_config(TournamentConfig.from_json(preset.tournament_json))
                config_view["opponents"] = preset.opponents or "mixed"
                tournaments[preset.key] = config_view
            if preset.kind is not ExperimentKind.NFSP_ITERATIVE:
                continue
            config = asdict(preset.config())
            presets[preset.key] = {
                name: config[name]
                for name in TRAINING_OVERRIDES
                if name not in ALGORITHM_ONLY_SETTINGS
                or preset.algorithm in ALGORITHM_ONLY_SETTINGS[name]
            }
        return {
            "tournaments": tournaments,
            "tournament_fields": list(TOURNAMENT_FIELDS),
            "fields": {
                name: {
                    "type": kind.__name__,
                    "min": OVERRIDE_LIMITS.get(name, (None, None))[0],
                    "max": OVERRIDE_LIMITS.get(name, (None, None))[1],
                }
                for name, kind in TRAINING_OVERRIDES.items()
            },
            "observation_schemas": list(OBSERVATION_SCHEMAS),
            "action_schemas": ["nlhe-discrete-v1", "nlhe-discrete-v2"],
            "presets": presets,
        }

    def set_operator_scopes(
        self, arguments: dict[str, Any], caller: OperatorIdentity, now: datetime
    ) -> dict[str, object]:
        """Give an operator credential exactly these scopes (a role name or a scope list)."""
        operator_id = OperatorId(str(arguments.get("operator_id", "")))
        try:
            scopes = _requested_scopes(arguments)
        except ValueError as error:
            raise ViewError(400, str(error)) from error
        if scopes is None or not scopes:
            raise ViewError(400, "give a role or at least one scope")
        if operator_id == caller.operator_id and Scope.ADMIN_MANAGE not in scopes:
            raise ViewError(400, "you cannot remove admin:manage from your own credential")
        try:
            identity = self.services.repositories.auth.set_operator_scopes(operator_id, scopes, now)
        except KeyError as error:
            raise ViewError(404, "no active credential with that id") from error
        except ValueError as error:
            raise ViewError(409, str(error)) from error
        return {
            "operator_id": str(identity.operator_id),
            "role": identity.role.value,
            "scopes": identity.scope_list(),
        }

    def create_invite(self, arguments: dict[str, Any]) -> dict[str, object]:
        from tableforge.transport.invite_issue import InviteIssueError

        role_value = arguments.get("role")
        try:
            role = OperatorRole(role_value) if role_value else None
            custom = parse_scopes(arguments["scopes"]) if arguments.get("scopes") else None
        except ValueError as error:
            raise ViewError(400, str(error)) from error
        if custom is not None and role is not None:
            raise ViewError(400, "choose a role or a list of scopes, not both")
        ttl_hours = float(arguments.get("ttl_hours", 24.0))
        if not 0 < ttl_hours <= 24 * 30:
            raise ViewError(400, "the invite lifetime must be between 0 and 720 hours")
        address = str(arguments.get("address") or "").strip() or self.advertised_address()
        try:
            scope = InviteScope(
                node=bool(arguments.get("node", False)), role=role, custom_scopes=custom
            )
            issued = issue_invite(
                self.services.repositories.auth,
                self.services.settings,
                address,
                scope,
                timedelta(hours=ttl_hours),
                str(arguments.get("label", ""))[:200],
                self.clock.now(),
                confirm_admin=bool(arguments.get("confirm_admin", False)),
            )
        except (InviteIssueError, ValueError) as error:
            raise ViewError(400, str(error)) from error
        return {
            "invite_id": str(issued.invite.invite_id),
            "invite": issued.code,
            "scope": scope.encode(),
            "expires_at": _iso(issued.invite.expires_at),
            "warnings": list(issued.warnings),
        }

    # --- helpers -------------------------------------------------------------------------

    def _online(self, last_heartbeat: datetime, now: datetime) -> bool:
        ttl = timedelta(seconds=self.services.settings.coordinator.node_ttl_seconds)
        return now - last_heartbeat <= ttl

    def _experiment_view(self, experiment: Experiment, detailed: bool = False) -> dict[str, object]:
        coordinator = self.services.coordinator
        jobs = coordinator.jobs.list_jobs(experiment.experiment_id)
        view: dict[str, object] = {
            "experiment_id": str(experiment.experiment_id),
            "preset": experiment.config.preset_key,
            "kind": experiment.config.kind.value,
            "players": experiment.config.players,
            "status": experiment.status.value,
            "revision": experiment.revision,
            "created_at": _iso(experiment.created_at),
            "updated_at": _iso(experiment.updated_at),
            "job_counts": dict(Counter(job.status.value for job in jobs)),
            "league": experiment.config.league,
            "group_id": experiment.config.group_id or None,
            "seed": experiment.config.seed_start,
        }
        if detailed:
            view["config"] = {
                key: value.value if hasattr(value, "value") else value
                for key, value in asdict(experiment.config).items()
            }
            view["jobs"] = [
                {
                    "job_id": str(job.spec.job_id),
                    "type": job.spec.type.value,
                    "payload_type": job.spec.payload_type,
                    "status": job.status.value,
                    "created_at": _iso(job.spec.created_at),
                }
                for job in jobs
            ]
            view["evaluation"] = self.services.experiments.evaluation_result(
                experiment.experiment_id
            )
            view["results"] = self._simulation_results(jobs)
            view["tournament"] = self.services.experiments.tournament_result(
                experiment.experiment_id
            )
            view["curve"] = self.services.experiments.training_curve(experiment.experiment_id)
        return view

    def _simulation_results(self, jobs: Any) -> dict[str, object] | None:
        totals: dict[str, Any] = {"hands": 0, "actions": 0, "showdowns": 0, "folds": 0}
        winnings: list[int] = []
        found = False
        for job in jobs:
            if job.spec.payload_type != "tableforge.v1.SimulationJobPayload":
                continue
            if job.status.value != "succeeded":
                continue
            outputs = self.services.coordinator.job_outputs(job.spec.job_id)
            if not outputs:
                continue
            content = self.services.coordinator.read_artifact(
                str(outputs[0].artifact.descriptor.artifact_id)
            )
            summary = json.loads(content)
            found = True
            for key in totals:
                totals[key] += int(summary.get(key, 0))
            values = [int(value) for value in summary.get("winnings", [])]
            if len(winnings) < len(values):
                winnings.extend([0] * (len(values) - len(winnings)))
            for index, value in enumerate(values):
                winnings[index] += value
        if not found:
            return None
        return {**totals, "winnings": winnings}


def _iso(value: datetime) -> str:
    return value.isoformat()


def _stack_bb(tournament_json: str) -> float | None:
    if not tournament_json:
        return None
    return float(json.loads(tournament_json)["starting_stack_bb"])


def _int(value: str, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError as error:
        raise ViewError(400, f"expected a number, got {value!r}") from error


def event_view(event: DomainEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "version": event.aggregate_version,
        "payload": event.payload,
        "occurred_at": _iso(event.occurred_at),
    }


def _requested_scopes(arguments: dict[str, Any]) -> frozenset[Scope] | None:
    """Scopes from ``{"role": "operator"}`` or ``{"scopes": [...]}`` (None when neither)."""
    if arguments.get("scopes"):
        return parse_scopes(arguments["scopes"])
    role = arguments.get("role")
    if role:
        preset = OperatorRole(str(role))
        if preset is OperatorRole.CUSTOM:
            raise ValueError("give the scopes of a custom credential explicitly")
        return ROLE_SCOPES[preset]
    return None
