"""Training algorithms behind one interface (A0).

Everything outside an algorithm's own package asks this registry instead of naming the
algorithm: replays, strategy tables, play and practice, benchmarks, opponent pools and model
comparison load models through `AlgorithmSpec`, and workers register its job executors.
Adding an algorithm means writing an `AlgorithmSpec` and adding it to `ALGORITHMS`; the
rest of the platform follows. NFSP is the first (and so far only) implementation.

An algorithm provides:

* `play_modes`: ways a trained model can play (the first is the default), shown in the UI;
* `explainer(checkpoint)`: a `DecisionExplainer` for one seat (probabilities and the
  algorithm's own numbers, see `learning.explain`); a fresh instance per seat, because
  agents keep per-hand state;
* `player(checkpoint, mode)`: a factory of table players for benchmarks and opponent
  pools (fast, no explanations);
* `executors(fetch)`: worker executors by job payload type (training, evaluation);
* `checkpoint_schema`: the artifact schema of its checkpoints.

Module imports stay light: PyTorch is imported only when a model is actually loaded, so the
coordinator and rule-bot workers can use the registry without the learning extra.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tableforge.learning.explain import PlayMode

if TYPE_CHECKING:
    from tableforge.learning.explain import DecisionExplainer
    from tableforge.poker.table import TableAgent

Fetch = Callable[[str], bytes]


class UnknownAlgorithmError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlgorithmSpec:
    key: str
    label: str
    checkpoint_schema: str
    play_modes: tuple[PlayMode, ...]
    explainer: Callable[[bytes], DecisionExplainer]
    player: Callable[[bytes, str], Callable[[], TableAgent]]
    """(checkpoint, play mode) -> a factory of table players sharing one set of weights."""
    executors: Callable[[Fetch], Mapping[str, Any]]
    """Worker executors by payload type (objects with execute/output_kind/...)."""

    def mode(self, requested: str = "") -> str:
        """The requested play mode, or the default one; unknown modes are errors."""
        keys = [item.key for item in self.play_modes]
        if not requested:
            return keys[0]
        if requested not in keys:
            raise ValueError(
                f"unknown play mode {requested}; {self.label} offers {', '.join(keys)}"
            )
        return requested


# --- NFSP --------------------------------------------------------------------------------

NFSP_AVERAGE = PlayMode(
    "average",
    "Average policy",
    "NFSP's average strategy: mixes actions with these probabilities "
    "(balanced, harder to exploit).",
)
NFSP_BEST_RESPONSE = PlayMode(
    "best_response",
    "Best response",
    "Always the action with the highest Q value (the best reply to the training opponents).",
)


def _nfsp_explainer(checkpoint: bytes) -> DecisionExplainer:
    from tableforge.learning.nfsp_explain import NfspExplainer

    return NfspExplainer(checkpoint)


def _nfsp_player(checkpoint: bytes, mode: str) -> Callable[[], TableAgent]:
    from tableforge.learning.nfsp import NfspAgent
    from tableforge.learning.tournament_training import (
        FrozenTournamentPlayer,
        GreedyTournamentPlayer,
    )

    if mode == NFSP_BEST_RESPONSE.key:
        greedy = NfspAgent.from_actor_bundle(checkpoint)
        return lambda: GreedyTournamentPlayer(greedy)
    frozen = NfspAgent.from_bundle(checkpoint, inference_only=True)
    return lambda: FrozenTournamentPlayer(frozen)


def _nfsp_executors(fetch: Fetch) -> Mapping[str, Any]:
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

    return {
        TRAINING_PAYLOAD_TYPE: NfspTrainingExecutor(),
        EVALUATION_PAYLOAD_TYPE: NfspEvaluationExecutor(fetch),
        INIT_PAYLOAD_TYPE: NfspInitExecutor(),
        ACTOR_PAYLOAD_TYPE: NfspActorExecutor(fetch),
        LEARNER_PAYLOAD_TYPE: NfspLearnerExecutor(fetch),
        MATCH_PAYLOAD_TYPE: NfspMatchExecutor(fetch),
        MATCHUP_PAYLOAD_TYPE: LeagueMatchupExecutor(fetch),
    }


NFSP = AlgorithmSpec(
    key="nfsp",
    label="NFSP",
    checkpoint_schema="tableforge.nfsp-checkpoint.v1",
    play_modes=(NFSP_AVERAGE, NFSP_BEST_RESPONSE),
    explainer=_nfsp_explainer,
    player=_nfsp_player,
    executors=_nfsp_executors,
)

ALGORITHMS: dict[str, AlgorithmSpec] = {NFSP.key: NFSP}


def algorithm(key: str) -> AlgorithmSpec:
    """The algorithm with this key (as stored on models and checkpoints)."""
    try:
        return ALGORITHMS[key]
    except KeyError as error:
        known = ", ".join(sorted(ALGORITHMS))
        raise UnknownAlgorithmError(f"unknown algorithm {key!r} (known: {known})") from error


def is_known(key: str) -> bool:
    return key in ALGORITHMS


def all_executors(fetch: Fetch) -> dict[str, Any]:
    """Every algorithm's worker executors, by payload type (no two may share one)."""
    found: dict[str, Any] = {}
    for spec in ALGORITHMS.values():
        for payload_type, executor in spec.executors(fetch).items():
            if payload_type in found:
                raise AssertionError(f"payload type {payload_type} is registered twice")
            found[payload_type] = executor
    return found
