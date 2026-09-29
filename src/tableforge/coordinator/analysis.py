"""Analysis jobs on named models: tournament replays and preflop strategy tables.

These jobs do not belong to an experiment; they are filed under the fixed experiment id
`ANALYSIS` and are idempotent by content, so asking twice for the same replay or table
returns the same job (and its result once it exists).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from tableforge.composition import Services
from tableforge.coordinator.experiments import _profile
from tableforge.coordinator.models import checkpoint_artifact, library
from tableforge.domain.ids import ExperimentId, JobId, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType
from tableforge.models import ModelInfo

ANALYSIS = ExperimentId("analysis")
REPLAY_PAYLOAD_TYPE = "tableforge.v1.TournamentReplayJobPayload"
STRATEGY_PAYLOAD_TYPE = "tableforge.v1.StrategyTableJobPayload"
MIXED_FIELD = ("tight", "call", "aggro", "random", "pushfold", "call")
RULE_BOTS = ("random", "call", "tight", "aggro", "pushfold", "hunter")
_GAME = re.compile(r"^(\d)max-(pko|sng)-(\d+(?:\.\d+)?)bb")


class AnalysisError(ValueError):
    pass


def tournament_json(info: ModelInfo) -> str:
    """The tournament a model plays: from its training settings, or from its game name."""
    stored = info.training.get("tournament_json")
    if isinstance(stored, str) and stored:
        return stored
    match = _GAME.match(info.game)
    if match is None:
        raise AnalysisError(f"model {info.model_id} does not play a known tournament ({info.game})")
    from tableforge.poker.tournament import BountyConfig, preset_config

    players, kind, stack = int(match.group(1)), match.group(2), float(match.group(3))
    return preset_config(
        stack, players=players, bounty=BountyConfig() if kind == "pko" else None
    ).to_json()


def _mode(info: ModelInfo, mode: str) -> str:
    from tableforge.coordinator.models import play_modes

    keys = [item["key"] for item in play_modes(info.algorithm)]
    if not mode:
        return keys[0]
    if mode not in keys:
        raise AnalysisError(f"unknown play mode {mode}; this model offers {', '.join(keys)}")
    return mode


def _create(services: Services, payload_type: str, payload: bytes) -> str:
    key = f"analysis:{payload_type}:{hashlib.sha256(payload).hexdigest()}"
    jobs = services.coordinator.jobs
    existing = jobs.get_by_idempotency_key(ANALYSIS, key)
    if existing is not None and existing.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        return str(existing.spec.job_id)
    if existing is not None:
        key = f"{key}:{new_job_id()}"
    profile = _profile(payload_type)
    created = services.coordinator.create_job(
        JobSpec(
            schema_version="1.0",
            job_id=new_job_id(),
            experiment_id=ANALYSIS,
            type=JobType.EVALUATION,
            priority=2,
            resources=profile.minimum,
            payload=payload,
            payload_type=payload_type,
            idempotency_key=key,
        )
    )
    return str(created.spec.job_id)


def request_replay(services: Services, arguments: dict[str, Any]) -> dict[str, object]:
    """Replay one tournament: the model in `seat`, the other seats from `opponents`.

    `opponents` is ``mixed`` (the benchmark field), ``self`` (copies of the model) or one
    rule bot name for every other seat.
    """
    from tableforge.learning.replay import ReplayJobPayload, SeatSpec

    info = library(services).info(str(arguments["model_id"]))
    mode = _mode(info, str(arguments.get("mode", "")))
    config = tournament_json(info)
    players = json.loads(config)["players"]
    seat = int(arguments.get("seat", 0))
    seed = int(arguments.get("seed", 1))
    opponents = str(arguments.get("opponents", "mixed"))
    if not 0 <= seat < players:
        raise AnalysisError(f"seat must be between 0 and {players - 1}")
    if not 0 <= seed < 2**31:
        raise AnalysisError("seed must be a non-negative number")
    if opponents not in ("mixed", "self", *RULE_BOTS):
        raise AnalysisError("opponents must be mixed, self or a rule bot")
    artifact_id, sha = checkpoint_artifact(services, info.model_id)
    seats = []
    for player in range(players):
        if player == seat or opponents == "self":
            seats.append(SeatSpec("model", info.model_id, artifact_id, sha, mode, info.algorithm))
        else:
            name = MIXED_FIELD[player % len(MIXED_FIELD)] if opponents == "mixed" else opponents
            seats.append(SeatSpec("rule", name))
    payload = ReplayJobPayload("1.0", config, seed, tuple(seats)).to_bytes()
    return {"job_id": _create(services, REPLAY_PAYLOAD_TYPE, payload)}


def request_strategy(services: Services, arguments: dict[str, Any]) -> dict[str, object]:
    from tableforge.learning.strategy import StrategyTablePayload

    info = library(services).info(str(arguments["model_id"]))
    mode = _mode(info, str(arguments.get("mode", "")))
    artifact_id, sha = checkpoint_artifact(services, info.model_id)
    payload = StrategyTablePayload(
        "1.0",
        tournament_json(info),
        artifact_id,
        sha,
        mode,
        info.algorithm,
        level=int(arguments.get("level", 1)),
    ).to_bytes()
    return {"job_id": _create(services, STRATEGY_PAYLOAD_TYPE, payload)}


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    status: str
    kind: str
    result: object | None
    error: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "kind": self.kind,
            "result": self.result,
            "error": self.error,
        }


def analysis_result(services: Services, job_id: str) -> AnalysisResult:
    job = services.coordinator.get_job(JobId(job_id))
    if job.spec.experiment_id != ANALYSIS:
        raise AnalysisError("not an analysis job")
    kind = "replay" if job.spec.payload_type == REPLAY_PAYLOAD_TYPE else "strategy"
    if job.status is not JobStatus.SUCCEEDED:
        error = ""
        if job.status is JobStatus.FAILED:
            error = "the job failed; see the worker log"
        return AnalysisResult(job.status.value, kind, None, error)
    outputs = services.coordinator.job_outputs(job.spec.job_id)
    if not outputs:
        return AnalysisResult("failed", kind, None, "the job has no output")
    content = services.coordinator.read_artifact(str(outputs[0].artifact.descriptor.artifact_id))
    return AnalysisResult(job.status.value, kind, json.loads(content), "")
