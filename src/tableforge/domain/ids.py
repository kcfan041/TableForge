from __future__ import annotations

import secrets
import time
from typing import NewType, TypeVar

NodeId = NewType("NodeId", str)
ExperimentId = NewType("ExperimentId", str)
JobId = NewType("JobId", str)
AttemptId = NewType("AttemptId", str)
AgentId = NewType("AgentId", str)
CheckpointId = NewType("CheckpointId", str)
ArtifactId = NewType("ArtifactId", str)
MatchId = NewType("MatchId", str)
EventId = NewType("EventId", str)
ClusterId = NewType("ClusterId", str)
WorkerId = NewType("WorkerId", str)
InviteId = NewType("InviteId", str)
OperatorId = NewType("OperatorId", str)

_Id = TypeVar("_Id", bound=str)


def uuid7() -> str:
    timestamp_ms = time.time_ns() // 1_000_000
    if timestamp_ms >= 1 << 48:
        raise OverflowError("timestamp exceeds UUIDv7 range")
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    hexadecimal = f"{value:032x}"
    return (
        f"{hexadecimal[:8]}-{hexadecimal[8:12]}-{hexadecimal[12:16]}-"
        f"{hexadecimal[16:20]}-{hexadecimal[20:]}"
    )


def new_node_id() -> NodeId:
    return NodeId(uuid7())


def new_experiment_id() -> ExperimentId:
    return ExperimentId(uuid7())


def new_job_id() -> JobId:
    return JobId(uuid7())


def new_attempt_id() -> AttemptId:
    return AttemptId(uuid7())


def new_agent_id() -> AgentId:
    return AgentId(uuid7())


def new_checkpoint_id() -> CheckpointId:
    return CheckpointId(uuid7())


def new_artifact_id() -> ArtifactId:
    return ArtifactId(uuid7())


def new_match_id() -> MatchId:
    return MatchId(uuid7())


def new_event_id() -> EventId:
    return EventId(uuid7())


def new_cluster_id() -> ClusterId:
    return ClusterId(uuid7())


def new_worker_id() -> WorkerId:
    return WorkerId(uuid7())


def new_invite_id() -> InviteId:
    return InviteId(uuid7())


def new_operator_id() -> OperatorId:
    return OperatorId(uuid7())
