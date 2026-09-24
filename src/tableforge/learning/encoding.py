from __future__ import annotations

from tableforge.poker.betting import ActionType, BettingStatus, Seat, Street
from tableforge.poker.cards import Card, Rank, Suit
from tableforge.poker.observation import Observation

SCHEMA = "poker-hunlhe-vector-v1"
HISTORY_LENGTH = 16
_CARD_FEATURES = 52
_ACTION_TYPES = (ActionType.FOLD, ActionType.CHECK, ActionType.CALL, ActionType.RAISE_TO)
_HISTORY_FEATURES = 1 + 1 + len(Street) + len(_ACTION_TYPES) + 2
FEATURE_SIZE = (
    _CARD_FEATURES * 2
    + len(Street)
    + 2
    + 9
    + HISTORY_LENGTH * _HISTORY_FEATURES
)


def _card_index(card: Card) -> int:
    return list(Suit).index(card.suit) * 13 + (int(card.rank) - int(Rank.TWO))


def encode(observation: Observation, big_blind: int) -> list[float]:
    if big_blind <= 0:
        raise ValueError("big_blind must be positive")
    features: list[float] = []
    hole = [0.0] * _CARD_FEATURES
    for card in observation.hole_cards:
        hole[_card_index(card)] = 1.0
    board = [0.0] * _CARD_FEATURES
    for card in observation.board:
        board[_card_index(card)] = 1.0
    features.extend(hole)
    features.extend(board)
    features.extend(1.0 if observation.street is street else 0.0 for street in Street)
    features.append(1.0 if observation.button is observation.observer else 0.0)
    features.append(1.0 if observation.status is BettingStatus.ACTIVE else 0.0)
    me = observation.players[int(observation.observer)]
    opponent = observation.players[int(observation.observer.other)]
    scale = 100.0 * big_blind
    pot = max(observation.pot, 1)
    to_call = max(0, observation.current_bet - me.street_contribution)
    features.extend(
        (
            me.stack / scale,
            opponent.stack / scale,
            observation.pot / scale,
            observation.current_bet / scale,
            me.street_contribution / scale,
            opponent.street_contribution / scale,
            to_call / pot,
            min(me.stack, opponent.stack) / pot,
            1.0 if opponent.all_in else 0.0,
        )
    )
    history = observation.action_history[-HISTORY_LENGTH:]
    padding = HISTORY_LENGTH - len(history)
    features.extend([0.0] * (padding * _HISTORY_FEATURES))
    for action in history:
        features.append(1.0)
        features.append(1.0 if action.actor is observation.observer else 0.0)
        features.extend(1.0 if action.street is street else 0.0 for street in Street)
        features.extend(1.0 if action.action_type is kind else 0.0 for kind in _ACTION_TYPES)
        features.append(action.contribution / scale)
        features.append(action.pot_after / scale)
    if len(features) != FEATURE_SIZE:
        raise AssertionError("encoding size mismatch")
    return features


def seat_player(observation: Observation) -> Seat:
    return observation.observer
