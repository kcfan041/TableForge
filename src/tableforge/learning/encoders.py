"""Observation encoders for learned agents, selected by schema name.

Checkpoints and experience shards record the schema they were produced with, so old
models keep loading with the encoder they were trained on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tableforge.learning import encoding as v1
from tableforge.learning import encoding_v2 as v2
from tableforge.learning import encoding_v3 as v3
from tableforge.poker.observation import Observation
from tableforge.poker.table import TableObservation

AnyObservation = Observation | TableObservation


@dataclass(frozen=True, slots=True)
class Encoder:
    schema: str
    feature_size: int
    encode: Callable[..., list[float]]
    """``encode(observation, big_blind, small_blind=None, tournament=None)``."""
    supports_tables: bool = False
    max_players: int = 6
    """Largest table the encoder has seats for (v1 is heads-up only)."""


def _v1(
    observation: AnyObservation,
    big_blind: int,
    small_blind: int | None = None,
    tournament: v2.TournamentContext | None = None,
) -> list[float]:
    del small_blind
    if not isinstance(observation, Observation):
        raise ValueError(f"{v1.SCHEMA} only encodes heads-up observations")
    if tournament is not None:
        raise ValueError(f"{v1.SCHEMA} has no tournament fields")
    return v1.encode(observation, big_blind)


def _v2(
    observation: AnyObservation,
    big_blind: int,
    small_blind: int | None = None,
    tournament: v2.TournamentContext | None = None,
) -> list[float]:
    return v2.encode(observation, big_blind, small_blind, tournament)


def _v3(
    observation: AnyObservation,
    big_blind: int,
    small_blind: int | None = None,
    tournament: v3.StructureContext | None = None,
) -> list[float]:
    return v3.encode(observation, big_blind, small_blind, tournament)


ENCODERS: dict[str, Encoder] = {
    v1.SCHEMA: Encoder(v1.SCHEMA, v1.FEATURE_SIZE, _v1),
    v2.SCHEMA: Encoder(v2.SCHEMA, v2.FEATURE_SIZE, _v2, supports_tables=True),
    v3.SCHEMA: Encoder(v3.SCHEMA, v3.FEATURE_SIZE, _v3, supports_tables=True, max_players=9),
}
DEFAULT_SCHEMA = v1.SCHEMA
LATEST_SCHEMA = v2.SCHEMA


def encoder(schema: str) -> Encoder:
    try:
        return ENCODERS[schema]
    except KeyError as error:
        raise ValueError(f"unsupported observation schema: {schema}") from error
