"""Runs `TournamentJobPayload` jobs: rule-bot tournament benchmarks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tableforge.agents.rule import AggroBot, CallBot, HeuristicPushFoldBot, RandomBot, TightBot
from tableforge.domain.artifacts import ArtifactKind
from tableforge.domain.tournament import TOURNAMENT_OUTPUT_SCHEMA, TournamentJobPayload
from tableforge.poker.table import TableAgent
from tableforge.poker.tournament_benchmark import collect_samples


def rule_bot(name: str, seed: int) -> TableAgent:
    if name == "random":
        return RandomBot(seed)
    if name == "call":
        return CallBot()
    if name == "tight":
        return TightBot()
    if name == "aggro":
        return AggroBot()
    if name == "pushfold":
        return HeuristicPushFoldBot()
    raise ValueError(f"unknown rule bot: {name}")


_MIXED = ("tight", "call", "aggro", "random", "pushfold", "call")


def field_factory(name: str) -> Callable[[int, int], TableAgent]:
    """Opponents by player id. `mixed` gives every seat a different kind of bot."""

    def build(player: int, seed: int) -> TableAgent:
        kind = _MIXED[player % len(_MIXED)] if name == "mixed" else name
        return rule_bot(kind, seed)

    return build


@dataclass(frozen=True, slots=True)
class TournamentExecutor:
    output_kind: ArtifactKind = ArtifactKind.EVALUATION_RESULT
    output_media_type: str = "application/json"
    output_schema: str = TOURNAMENT_OUTPUT_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        payload = TournamentJobPayload.from_bytes(payload_bytes)
        samples = collect_samples(
            lambda: rule_bot(payload.candidate, payload.seed_start),
            field_factory(payload.field),
            payload.config,
            payload.seeds,
            payload.seed_start,
            payload.rotate_seats,
            on_progress,
        )
        return samples.to_json()
