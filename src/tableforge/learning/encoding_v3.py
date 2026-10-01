"""`poker-nlhe-vector-v3`: observation encoding for 2-9 players and any tournament structure.

Builds on v2 (`encoding_v2.py`, unchanged for existing checkpoints):

* up to 9 players: eight opponent slots ordered by seat after the player;
* position as distance from the button and as distance to the button (both scaled by table
  size), so the same seat means the same thing at 6-max and 9-max;
* the tournament *structure*, so one model can play many structures (S3): starting stack in
  big blinds, blind growth per level, hands per level, ante relative to the big blind,
  number of paid places, the payout of first place and of the money bubble, players left
  relative to the field, bounty share of the buy-in and cash share of a knockout, and the
  player's own bounty relative to the average bounty at the table;
* the payout vector for 9 places;
* everything else as in v2 (cards, hand strength, stacks in big blinds, action history).

Fields that a cash game does not have are zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tableforge.learning import encoding_v2 as v2
from tableforge.poker.betting import BettingStatus, Street
from tableforge.poker.evaluator import HandCategory
from tableforge.poker.observation import Observation
from tableforge.poker.strength import postflop_features, preflop_features
from tableforge.poker.table import TableObservation

SCHEMA = "poker-nlhe-vector-v3"
MAX_PLAYERS = 9
OPPONENT_SLOTS = MAX_PLAYERS - 1
HISTORY_LENGTH = 24

_CARD_FEATURES = 52
_PREFLOP_FEATURES = 7
_POSTFLOP_FEATURES = len(HandCategory) + 13
_SELF_FEATURES = 10
_OPPONENT_FEATURES = 10
_TABLE_FEATURES = 9
_STRUCTURE_FEATURES = 16
_HISTORY_FEATURES = v2._HISTORY_FEATURES
FEATURE_SIZE = (
    _CARD_FEATURES * 2
    + len(Street)
    + _PREFLOP_FEATURES
    + _POSTFLOP_FEATURES
    + _SELF_FEATURES
    + OPPONENT_SLOTS * _OPPONENT_FEATURES
    + _TABLE_FEATURES
    + _STRUCTURE_FEATURES
    + MAX_PLAYERS
    + HISTORY_LENGTH * _HISTORY_FEATURES
)
_STACK_SCALE_BB = 100.0
_MAX_POT_RATIO = 20.0


@dataclass(frozen=True, slots=True)
class StructureContext:
    """The tournament as every player sees it at the start of a hand (v3)."""

    starting_stack: int
    level: int
    hands_to_next_level: int
    hands_per_level: int
    players_left: int
    field_size: int
    ante: int = 0
    payout: tuple[float, ...] = ()
    bounties: tuple[float, ...] = field(default=())
    """Bounty on each seat's head, relative to the starting bounty."""
    bounty_share: float = 0.0
    """Part of the buy-in that is bounty (0 without PKO)."""
    cash_share: float = 0.0
    blind_growth: float = 1.25
    """Big blind of the next level over this one."""

    def __post_init__(self) -> None:
        if self.starting_stack <= 0:
            raise ValueError("starting_stack must be positive")
        if len(self.payout) > MAX_PLAYERS:
            raise ValueError(f"payout has more than {MAX_PLAYERS} places")


def encode(
    observation: Observation | TableObservation,
    big_blind: int,
    small_blind: int | None = None,
    tournament: StructureContext | None = None,
) -> list[float]:
    """Encode what ``observation.observer`` can see into ``FEATURE_SIZE`` floats."""
    if big_blind <= 0:
        raise ValueError("big_blind must be positive")
    view = v2._view(observation)
    bb = float(big_blind)
    sb = float(small_blind if small_blind is not None else big_blind / 2)
    players = view.players_at_table
    if players > MAX_PLAYERS:
        raise ValueError(f"{SCHEMA} encodes at most {MAX_PLAYERS} players")
    features: list[float] = []

    hole = [0.0] * _CARD_FEATURES
    for card in view.hole_cards:
        hole[v2._card_index(card)] = 1.0
    board = [0.0] * _CARD_FEATURES
    for card in view.board:
        board[v2._card_index(card)] = 1.0
    features.extend(hole)
    features.extend(board)
    features.extend(1.0 if view.street is street else 0.0 for street in Street)

    pre = preflop_features(view.hole_cards)
    features.extend(
        (
            v2._rank(pre.high_rank),
            v2._rank(pre.low_rank),
            1.0 if pre.suited else 0.0,
            1.0 if pre.pair else 0.0,
            min(pre.gap, 12) / 12,
            (pre.chen + 1) / 21,
            1.0 if pre.gap == 1 else 0.0,
        )
    )
    if len(view.board) >= 3:
        post = postflop_features(view.hole_cards, view.board)
        features.extend(1.0 if post.category is category else 0.0 for category in HandCategory)
        features.extend(
            (
                1.0,
                v2._rank(post.primary_rank),
                1.0 if post.pair_uses_hole else 0.0,
                1.0 if post.overpair else 0.0,
                1.0 if post.top_pair else 0.0,
                1.0 if post.flush_draw else 0.0,
                1.0 if post.open_ended_draw else 0.0,
                1.0 if post.gutshot else 0.0,
                1.0 if post.board_paired else 0.0,
                1.0 if post.board_trips else 0.0,
                1.0 if post.board_three_suited else 0.0,
                1.0 if post.board_four_suited else 0.0,
                1.0 if post.board_straight_possible else 0.0,
            )
        )
    else:
        features.extend([0.0] * _POSTFLOP_FEATURES)

    def after_button(seat: int) -> float:
        return ((seat - view.button) % players) / (MAX_PLAYERS - 1)

    def before_button(seat: int) -> float:
        """0 = button, 1/8 = cutoff...: late position means the same at any table size."""
        return ((view.button - seat) % players) / (MAX_PLAYERS - 1)

    me = view.seats[view.observer]
    to_call = max(0, view.current_bet - me.street_contribution)
    live_opponents = [seat for seat in view.seats if seat.seat != view.observer and not seat.folded]
    deepest = max((seat.stack + seat.street_contribution for seat in live_opponents), default=0)
    effective = min(me.stack, max(deepest - me.street_contribution, 0))
    features.extend(
        (
            me.stack / bb / _STACK_SCALE_BB,
            me.street_contribution / bb / _STACK_SCALE_BB,
            me.total_contribution / bb / _STACK_SCALE_BB,
            after_button(view.observer),
            before_button(view.observer),
            1.0 if view.observer == view.button else 0.0,
            1.0 if view.observer == view.small_blind_seat else 0.0,
            1.0 if view.observer == view.big_blind_seat else 0.0,
            to_call / bb / _STACK_SCALE_BB,
            effective / bb / _STACK_SCALE_BB,
        )
    )

    bounties = tournament.bounties if tournament is not None else ()
    average_bounty = sum(bounties) / len(bounties) if bounties else 0.0
    my_chips = me.stack + me.total_contribution
    for offset in range(1, OPPONENT_SLOTS + 1):
        if offset >= players:
            features.extend([0.0] * _OPPONENT_FEATURES)
            continue
        seat = view.seats[(view.observer + offset) % players]
        covered = my_chips >= seat.stack + seat.total_contribution
        features.extend(
            (
                1.0,
                seat.stack / bb / _STACK_SCALE_BB,
                seat.street_contribution / bb / _STACK_SCALE_BB,
                seat.total_contribution / bb / _STACK_SCALE_BB,
                1.0 if seat.folded else 0.0,
                1.0 if seat.all_in else 0.0,
                after_button(seat.seat),
                1.0 if seat.seat == view.button else 0.0,
                bounties[seat.seat] if seat.seat < len(bounties) else 0.0,
                # Only a player who covers an opponent can win that opponent's bounty.
                1.0 if covered else 0.0,
            )
        )

    pot = max(view.pot, 1)
    features.extend(
        (
            view.pot / bb / _STACK_SCALE_BB,
            view.current_bet / bb / _STACK_SCALE_BB,
            min(to_call / pot, _MAX_POT_RATIO) / _MAX_POT_RATIO,
            min(effective / pot, _MAX_POT_RATIO) / _MAX_POT_RATIO,
            1.0 if view.status is BettingStatus.ACTIVE else 0.0,
            (players - 2) / (MAX_PLAYERS - 2),
            len(live_opponents) / OPPONENT_SLOTS,
            sum(1 for seat in live_opponents if seat.all_in) / OPPONENT_SLOTS,
            1.0 if any(seat.all_in for seat in live_opponents) else 0.0,
        )
    )

    if tournament is None:
        features.extend([0.0] * (_STRUCTURE_FEATURES + MAX_PLAYERS))
    else:
        start = float(tournament.starting_stack)
        payout = list(tournament.payout[:MAX_PLAYERS])
        paid = sum(1 for share in payout if share > 0)
        bubble = payout[paid - 1] if paid else 0.0
        own_bounty = bounties[view.observer] if view.observer < len(bounties) else 0.0
        field_size = max(tournament.field_size, tournament.players_left, 1)
        features.extend(
            (
                sb / start,
                bb / start,
                math.log1p(start / bb) / math.log1p(_STACK_SCALE_BB * 2),
                tournament.ante / bb,
                min(tournament.level, 30) / 30,
                (
                    tournament.hands_to_next_level / tournament.hands_per_level
                    if tournament.hands_per_level > 0
                    else 0.0
                ),
                min(tournament.hands_per_level, 60) / 60,
                min(max(tournament.blind_growth - 1.0, 0.0), 1.0),
                tournament.players_left / MAX_PLAYERS,
                tournament.players_left / field_size,
                paid / MAX_PLAYERS,
                payout[0] if payout else 0.0,
                bubble,
                tournament.bounty_share,
                tournament.cash_share,
                own_bounty / average_bounty if average_bounty > 0 else 0.0,
            )
        )
        features.extend(payout + [0.0] * (MAX_PLAYERS - len(payout)))

    history = view.history[-HISTORY_LENGTH:]
    features.extend([0.0] * ((HISTORY_LENGTH - len(history)) * _HISTORY_FEATURES))
    for action in history:
        features.append(1.0)
        features.append(1.0 if action.actor == view.observer else 0.0)
        features.extend(1.0 if action.street is street else 0.0 for street in Street)
        features.extend(1.0 if action.action_type is kind else 0.0 for kind in v2._ACTION_TYPES)
        features.append(action.contribution / bb / _STACK_SCALE_BB)
        features.append(action.pot_after / bb / _STACK_SCALE_BB)
    if len(features) != FEATURE_SIZE:
        raise AssertionError(f"encoding size mismatch: {len(features)} != {FEATURE_SIZE}")
    return features
