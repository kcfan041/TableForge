from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from tableforge.domain.artifacts import ArtifactKind
from tableforge.domain.league import MATCHUP_PAYLOAD_TYPE
from tableforge.domain.learning import (
    ACTOR_PAYLOAD_TYPE,
    EVALUATION_PAYLOAD_TYPE,
    INIT_PAYLOAD_TYPE,
    LEARNER_PAYLOAD_TYPE,
    MATCH_PAYLOAD_TYPE,
    TRAINING_PAYLOAD_TYPE,
)
from tableforge.learning.executors import NfspEvaluationExecutor, NfspTrainingExecutor
from tableforge.learning.iterative import (
    NfspActorExecutor,
    NfspInitExecutor,
    NfspLearnerExecutor,
    NfspMatchExecutor,
)
from tableforge.learning.league import LeagueMatchupExecutor
from tableforge.learning.replay import REPLAY_PAYLOAD_TYPE, ReplayExecutor
from tableforge.learning.strategy import STRATEGY_PAYLOAD_TYPE, StrategyTableExecutor
from tableforge.worker.runtime import OutputSpec, RegisteredExecutor


class DescribedExecutor(Protocol):
    @property
    def output_kind(self) -> ArtifactKind: ...

    @property
    def output_media_type(self) -> str: ...

    @property
    def output_schema(self) -> str: ...

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes: ...


def learning_executors(fetch: Callable[[str], bytes]) -> dict[str, RegisteredExecutor]:
    executors: dict[str, DescribedExecutor] = {
        TRAINING_PAYLOAD_TYPE: NfspTrainingExecutor(),
        EVALUATION_PAYLOAD_TYPE: NfspEvaluationExecutor(fetch),
        INIT_PAYLOAD_TYPE: NfspInitExecutor(),
        ACTOR_PAYLOAD_TYPE: NfspActorExecutor(fetch),
        LEARNER_PAYLOAD_TYPE: NfspLearnerExecutor(fetch),
        MATCH_PAYLOAD_TYPE: NfspMatchExecutor(fetch),
        MATCHUP_PAYLOAD_TYPE: LeagueMatchupExecutor(fetch),
        REPLAY_PAYLOAD_TYPE: ReplayExecutor(fetch),
        STRATEGY_PAYLOAD_TYPE: StrategyTableExecutor(fetch),
    }
    return {
        payload_type: RegisteredExecutor(
            executor,
            OutputSpec(executor.output_kind, executor.output_media_type, executor.output_schema),
        )
        for payload_type, executor in executors.items()
    }
