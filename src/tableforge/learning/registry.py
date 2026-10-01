from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from tableforge.domain.artifacts import ArtifactKind
from tableforge.learning.algorithms import all_executors
from tableforge.learning.comparison import COMPARISON_PAYLOAD_TYPE, ComparisonExecutor
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
    """Every learning executor: each algorithm's own jobs (`learning.algorithms`), and the
    analysis jobs that work with any algorithm (replays, strategy tables, comparisons)."""
    executors: dict[str, DescribedExecutor] = {
        **all_executors(fetch),
        REPLAY_PAYLOAD_TYPE: ReplayExecutor(fetch),
        STRATEGY_PAYLOAD_TYPE: StrategyTableExecutor(fetch),
        COMPARISON_PAYLOAD_TYPE: ComparisonExecutor(fetch),
    }
    return {
        payload_type: RegisteredExecutor(
            executor,
            OutputSpec(executor.output_kind, executor.output_media_type, executor.output_schema),
        )
        for payload_type, executor in executors.items()
    }
