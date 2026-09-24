from __future__ import annotations

from dataclasses import dataclass

from tableforge.poker.betting import (
    ActionType,
    BettingStatus,
    LegalActions,
    Seat,
    Street,
)
from tableforge.poker.cards import Card


@dataclass(frozen=True, slots=True)
class PublicAction:
    sequence: int
    street: Street
    actor: Seat
    action_type: ActionType
    raise_to: int | None
    contribution: int
    pot_after: int


@dataclass(frozen=True, slots=True)
class PlayerObservation:
    seat: Seat
    stack: int
    street_contribution: int
    total_contribution: int
    folded: bool
    all_in: bool


@dataclass(frozen=True, slots=True)
class Observation:
    schema_version: str
    observer: Seat
    button: Seat
    street: Street
    status: BettingStatus
    current_actor: Seat | None
    hole_cards: tuple[Card, Card]
    board: tuple[Card, ...]
    pot: int
    current_bet: int
    players: tuple[PlayerObservation, PlayerObservation]
    action_history: tuple[PublicAction, ...]
    legal_actions: LegalActions | None
