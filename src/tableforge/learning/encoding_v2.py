"""`poker-hunlhe-vector-v2`: observation encoding for 2-6 players, cash and tournament.

Differences from v1 (`encoding.py`, kept for old checkpoints):

* hand-strength features computed from the player's own cards and the visible board;
* the player and up to five opponents in fixed slots ordered by seat after the player,
  so the same layout serves heads-up and 6-max;
* chip amounts are in big blinds, and the blinds themselves are reported relative to the
  starting stack so blind increases stay visible;
* tournament fields (blind level, players left, payout vector, bounties) that are zero in
  cash games.

Only information the acting player can see is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tableforge.poker.betting import ActionType, BettingStatus, Street
from tableforge.poker.cards import Card, Rank, Suit
from tableforge.poker.evaluator import HandCategory
from tableforge.poker.observation import Observation
from tableforge.poker.strength import postflop_features, preflop_features
from tableforge.poker.table import TableObservation

SCHEMA = "poker-hunlhe-vector-v2"
MAX_PLAYERS = 6
OPPONENT_SLOTS = MAX_PLAYERS - 1
HISTORY_LENGTH = 16

_CARD_FEATURES = 52
_ACTION_TYPES = (ActionType.FOLD, ActionType.CHECK, ActionType.CALL, ActionType.RAISE_TO)
_PREFLOP_FEATURES = 7
_POSTFLOP_FEATURES = len(HandCategory) + 13
_SELF_FEATURES = 9
_OPPONENT_FEATURES = 9
_TABLE_FEATURES = 9
_TOURNAMENT_FEATURES = 8 + MAX_PLAYERS
_HISTORY_FEATURES = 1 + 1 + len(Street) + len(_ACTION_TYPES) + 2
FEATURE_SIZE = (
    _CARD_FEATURES * 2
    + len(Street)
    + _PREFLOP_FEATURES
    + _POSTFLOP_FEATURES
    + _SELF_FEATURES
    + OPPONENT_SLOTS * _OPPONENT_FEATURES
    + _TABLE_FEATURES
    + _TOURNAMENT_FEATURES
    + HISTORY_LENGTH * _HISTORY_FEATURES
)

_STACK_SCALE_BB = 100.0
# Stack-to-pot and call-to-pot ratios are capped so tiny pots do not produce huge inputs.
_MAX_POT_RATIO = 20.0


@dataclass(frozen=True, slots=True)
class TournamentContext:
    """Tournament state visible to every player; absent (all zeros) in cash games.

    ``bounties`` are indexed by seat, in buy-ins; ``payout`` holds the share of the
    prize pool for each finishing position (first entry is the winner).
    """

    starting_stack: int
    level: int
    hands_to_next_level: int
    hands_per_level: int
    players_left: int
    ante: int = 0
    payout: tuple[float, ...] = ()
    bounties: tuple[float, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.starting_stack <= 0:
            raise ValueError("starting_stack must be positive")
        if len(self.payout) > MAX_PLAYERS:
            raise ValueError(f"payout has more than {MAX_PLAYERS} positions")


@dataclass(frozen=True, slots=True)
class _Seat:
    seat: int
    stack: int
    street_contribution: int
    total_contribution: int
    folded: bool
    all_in: bool


@dataclass(frozen=True, slots=True)
class _Action:
    street: Street
    actor: int
    action_type: ActionType
    contribution: int
    pot_after: int


@dataclass(frozen=True, slots=True)
class _View:
    players_at_table: int
    observer: int
    button: int
    small_blind_seat: int
    big_blind_seat: int
    street: Street
    status: BettingStatus
    hole_cards: tuple[Card, Card]
    board: tuple[Card, ...]
    pot: int
    current_bet: int
    seats: tuple[_Seat, ...]
    history: tuple[_Action, ...]


def _view(observation: Observation | TableObservation) -> _View:
    if isinstance(observation, TableObservation):
        return _View(
            players_at_table=observation.players_at_table,
            observer=observation.observer,
            button=observation.button,
            small_blind_seat=observation.small_blind_seat,
            big_blind_seat=observation.big_blind_seat,
            street=observation.street,
            status=observation.status,
            hole_cards=observation.hole_cards,
            board=observation.board,
            pot=observation.pot,
            current_bet=observation.current_bet,
            seats=tuple(
                _Seat(
                    player.seat,
                    player.stack,
                    player.street_contribution,
                    player.total_contribution,
                    player.folded,
                    player.all_in,
                )
                for player in observation.players
            ),
            history=tuple(
                _Action(a.street, a.actor, a.action_type, a.contribution, a.pot_after)
                for a in observation.action_history
            ),
        )
    button = int(observation.button)
    return _View(
        players_at_table=2,
        observer=int(observation.observer),
        button=button,
        small_blind_seat=button,
        big_blind_seat=1 - button,
        street=observation.street,
        status=observation.status,
        hole_cards=observation.hole_cards,
        board=observation.board,
        pot=observation.pot,
        current_bet=observation.current_bet,
        seats=tuple(
            _Seat(
                int(player.seat),
                player.stack,
                player.street_contribution,
                player.total_contribution,
                player.folded,
                player.all_in,
            )
            for player in observation.players
        ),
        history=tuple(
            _Action(a.street, int(a.actor), a.action_type, a.contribution, a.pot_after)
            for a in observation.action_history
        ),
    )


def _card_index(card: Card) -> int:
    return list(Suit).index(card.suit) * 13 + (int(card.rank) - int(Rank.TWO))


def _rank(value: int) -> float:
    return (value - 2) / 12


def encode(
    observation: Observation | TableObservation,
    big_blind: int,
    small_blind: int | None = None,
    tournament: TournamentContext | None = None,
) -> list[float]:
    """Encode what ``observation.observer`` can see into ``FEATURE_SIZE`` floats."""
    if big_blind <= 0:
        raise ValueError("big_blind must be positive")
    view = _view(observation)
    bb = float(big_blind)
    sb = float(small_blind if small_blind is not None else big_blind / 2)
    players = view.players_at_table
    features: list[float] = []

    hole = [0.0] * _CARD_FEATURES
    for card in view.hole_cards:
        hole[_card_index(card)] = 1.0
    board = [0.0] * _CARD_FEATURES
    for card in view.board:
        board[_card_index(card)] = 1.0
    features.extend(hole)
    features.extend(board)
    features.extend(1.0 if view.street is street else 0.0 for street in Street)

    pre = preflop_features(view.hole_cards)
    features.extend(
        (
            _rank(pre.high_rank),
            _rank(pre.low_rank),
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
                _rank(post.primary_rank),
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

    def position(seat: int) -> float:
        """Seats after the button, 0 = button, normalized by table size."""
        return ((seat - view.button) % players) / (MAX_PLAYERS - 1)

    me = view.seats[view.observer]
    to_call = max(0, view.current_bet - me.street_contribution)
    live_opponents = [seat for seat in view.seats if seat.seat != view.observer and not seat.folded]
    # Chips at risk against the deepest live opponent (the most this player can win or lose).
    deepest = max((seat.stack + seat.street_contribution for seat in live_opponents), default=0)
    effective = min(me.stack, max(deepest - me.street_contribution, 0))
    features.extend(
        (
            me.stack / bb / _STACK_SCALE_BB,
            me.street_contribution / bb / _STACK_SCALE_BB,
            me.total_contribution / bb / _STACK_SCALE_BB,
            position(view.observer),
            1.0 if view.observer == view.button else 0.0,
            1.0 if view.observer == view.small_blind_seat else 0.0,
            1.0 if view.observer == view.big_blind_seat else 0.0,
            to_call / bb / _STACK_SCALE_BB,
            effective / bb / _STACK_SCALE_BB,
        )
    )

    bounties = tournament.bounties if tournament is not None else ()
    for offset in range(1, OPPONENT_SLOTS + 1):
        if offset >= players:
            features.extend([0.0] * _OPPONENT_FEATURES)
            continue
        seat = view.seats[(view.observer + offset) % players]
        features.extend(
            (
                1.0,
                seat.stack / bb / _STACK_SCALE_BB,
                seat.street_contribution / bb / _STACK_SCALE_BB,
                seat.total_contribution / bb / _STACK_SCALE_BB,
                1.0 if seat.folded else 0.0,
                1.0 if seat.all_in else 0.0,
                position(seat.seat),
                1.0 if seat.seat == view.button else 0.0,
                bounties[seat.seat] if seat.seat < len(bounties) else 0.0,
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
        features.extend([0.0] * _TOURNAMENT_FEATURES)
    else:
        start = float(tournament.starting_stack)
        payout = list(tournament.payout[:MAX_PLAYERS])
        payout.extend([0.0] * (MAX_PLAYERS - len(payout)))
        own_bounty = bounties[view.observer] if view.observer < len(bounties) else 0.0
        features.extend(
            (
                sb / start,
                bb / start,
                tournament.ante / start,
                tournament.ante / bb,
                min(tournament.level, 30) / 30,
                (
                    tournament.hands_to_next_level / tournament.hands_per_level
                    if tournament.hands_per_level > 0
                    else 0.0
                ),
                tournament.players_left / MAX_PLAYERS,
                own_bounty,
            )
        )
        features.extend(payout)
    history = view.history[-HISTORY_LENGTH:]
    features.extend([0.0] * ((HISTORY_LENGTH - len(history)) * _HISTORY_FEATURES))
    for action in history:
        features.append(1.0)
        features.append(1.0 if action.actor == view.observer else 0.0)
        features.extend(1.0 if action.street is street else 0.0 for street in Street)
        features.extend(1.0 if action.action_type is kind else 0.0 for kind in _ACTION_TYPES)
        features.append(action.contribution / bb / _STACK_SCALE_BB)
        features.append(action.pot_after / bb / _STACK_SCALE_BB)
    if len(features) != FEATURE_SIZE:
        raise AssertionError(f"encoding size mismatch: {len(features)} != {FEATURE_SIZE}")
    return features


def feature_names() -> list[str]:
    """Human-readable names, one per feature, in encoding order (for debugging)."""
    names: list[str] = []
    names += [f"hole_{index}" for index in range(_CARD_FEATURES)]
    names += [f"board_{index}" for index in range(_CARD_FEATURES)]
    names += [f"street_{street.name.lower()}" for street in Street]
    names += [
        "pre_high",
        "pre_low",
        "pre_suited",
        "pre_pair",
        "pre_gap",
        "pre_chen",
        "pre_connected",
    ]
    names += [f"post_{category.name.lower()}" for category in HandCategory]
    names += [
        "post_present",
        "post_primary_rank",
        "post_pair_uses_hole",
        "post_overpair",
        "post_top_pair",
        "post_flush_draw",
        "post_open_ended",
        "post_gutshot",
        "board_paired",
        "board_trips",
        "board_three_suited",
        "board_four_suited",
        "board_straight_possible",
    ]
    names += [
        "me_stack",
        "me_street",
        "me_total",
        "me_position",
        "me_button",
        "me_small_blind",
        "me_big_blind",
        "to_call",
        "effective_stack",
    ]
    for slot in range(1, OPPONENT_SLOTS + 1):
        names += [
            f"opp{slot}_{name}"
            for name in (
                "present",
                "stack",
                "street",
                "total",
                "folded",
                "all_in",
                "position",
                "button",
                "bounty",
            )
        ]
    names += [
        "pot",
        "current_bet",
        "to_call_over_pot",
        "effective_over_pot",
        "active",
        "table_size",
        "live_opponents",
        "all_in_opponents",
        "any_all_in",
    ]
    names += [
        "sb_over_start",
        "bb_over_start",
        "ante_over_start",
        "ante_over_bb",
        "level",
        "level_progress",
        "players_left",
        "own_bounty",
    ]
    names += [f"payout_{place}" for place in range(1, MAX_PLAYERS + 1)]
    for index in range(HISTORY_LENGTH):
        names += [
            f"hist{index}_{name}"
            for name in (
                "present",
                "mine",
                *(street.name.lower() for street in Street),
                *(kind.value for kind in _ACTION_TYPES),
                "contribution",
                "pot_after",
            )
        ]
    return names
