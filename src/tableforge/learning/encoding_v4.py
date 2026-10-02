"""Observation encoding v4 (T5): v3 plus where the whole MTT stands.

Everything in v3 (`encoding_v3`) is kept, in the same order; v4 appends `FIELD_FEATURES`
numbers about the field around our table (`TournamentHandInfo.stage`). For a single-table
tournament they say so (first number 0) and the rest is 0, so a v4 model plays single
tables like a v3 model would.

For a stage, the payout v3 sees is the field's payout as seen from our table (the table
ladder: what finishing 1st, 2nd... among our table is worth), so the bubble shows there
too; the field block adds the distance to the money and the field's stacks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tableforge.learning import encoding_v3 as v3
from tableforge.poker.observation import Observation
from tableforge.poker.table import TableObservation

SCHEMA = "poker-nlhe-vector-v4"
MAX_PLAYERS = v3.MAX_PLAYERS
FIELD_FEATURES = 8
FEATURE_SIZE = v3.FEATURE_SIZE + FIELD_FEATURES
_FIELD_SCALE = 1_000.0


@dataclass(frozen=True, slots=True)
class FieldContext:
    """The MTT around our table (from `tournament.StageInfo`)."""

    field_size: int
    field_left: int
    paid: int
    field_average_stack: float
    min_cash: float
    next_prize: float


@dataclass(frozen=True, slots=True)
class StageContext:
    structure: v3.StructureContext
    field: FieldContext | None = None


def field_features(field: FieldContext | None, my_chips: float, big_blind: float) -> list[float]:
    if field is None:
        return [0.0] * FIELD_FEATURES
    to_money = max(0, field.field_left - field.paid)
    return [
        1.0,
        math.log1p(field.field_left) / math.log1p(_FIELD_SCALE),
        field.paid / max(field.field_size, 1),
        # Distance to the money: 0 in the money, 1 for 100 or more players to go.
        min(to_money, 100) / 100,
        1.0 if to_money == 0 else 0.0,
        min(my_chips / max(field.field_average_stack, 1.0), 5.0) / 5,
        min(field.field_average_stack / big_blind, 200.0) / 200,
        # The next place's prize in min-cashes (0 off the money); capped at 20.
        min(field.next_prize / field.min_cash, 20.0) / 20 if field.min_cash > 0 else 0.0,
    ]


def encode(
    observation: Observation | TableObservation,
    big_blind: int,
    small_blind: int | None = None,
    tournament: StageContext | None = None,
) -> list[float]:
    structure = tournament.structure if tournament is not None else None
    features = v3.encode(observation, big_blind, small_blind, structure)
    field = tournament.field if tournament is not None else None
    if field is None:
        return features + [0.0] * FIELD_FEATURES
    me = observation.players[int(observation.observer)]
    chips = me.stack + me.total_contribution
    return features + field_features(field, float(chips), float(big_blind))
