from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tableforge.agents.rule import RandomBot
from tableforge.learning.encoding_v2 import (
    FEATURE_SIZE,
    MAX_PLAYERS,
    TournamentContext,
    encode,
    feature_names,
)
from tableforge.poker.betting import Action, Seat
from tableforge.poker.cards import Card, full_deck
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.evaluator import HandCategory
from tableforge.poker.strength import chen_score, postflop_features, preflop_features
from tableforge.poker.table import TableHand

NAMES = feature_names()


def cards(text: str) -> tuple[Card, ...]:
    return tuple(Card.parse(item) for item in text.split())


def value(features: list[float], name: str) -> float:
    return features[NAMES.index(name)]


# --- hand strength -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hole", "score"),
    [("As Ah", 20), ("Ks Kd", 16), ("As Ks", 12), ("Js Ts", 9), ("7c 2d", -1), ("5h 5d", 5)],
)
def test_chen_score(hole: str, score: float) -> None:
    first, second = cards(hole)
    assert chen_score((first, second)) == score


def test_preflop_features() -> None:
    first, second = cards("Qs Js")
    features = preflop_features((first, second))

    assert (features.high_rank, features.low_rank, features.suited, features.pair) == (
        12,
        11,
        True,
        False,
    )


def test_postflop_made_hands_and_draws() -> None:
    top_pair = postflop_features(cards("Ah Kc")[:2], cards("Kd 7s 2c"))  # type: ignore[arg-type]
    assert top_pair.category is HandCategory.ONE_PAIR
    assert top_pair.top_pair and top_pair.pair_uses_hole and not top_pair.overpair

    overpair = postflop_features(cards("Qs Qh")[:2], cards("9d 7s 2c"))  # type: ignore[arg-type]
    assert overpair.overpair and not overpair.top_pair

    board_pair = postflop_features(cards("Ah Kc")[:2], cards("9d 9s 2c"))  # type: ignore[arg-type]
    assert board_pair.board_paired and not board_pair.pair_uses_hole

    flush_draw = postflop_features(cards("Ah Kh")[:2], cards("9h 7h 2c"))  # type: ignore[arg-type]
    assert flush_draw.flush_draw

    board_only_flush = postflop_features(cards("As Kc")[:2], cards("9h 7h 2h 4h"))  # type: ignore[arg-type]
    assert not board_only_flush.flush_draw and board_only_flush.board_four_suited

    open_ended = postflop_features(cards("9c 8d")[:2], cards("7h 6s 2c"))  # type: ignore[arg-type]
    assert open_ended.open_ended_draw and not open_ended.gutshot

    gutshot = postflop_features(cards("9c 8d")[:2], cards("6h 5s Kc"))  # type: ignore[arg-type]
    assert gutshot.gutshot and not gutshot.open_ended_draw

    river = postflop_features(cards("9c 8d")[:2], cards("7h 6s 2c Kd Qh"))  # type: ignore[arg-type]
    assert not river.open_ended_draw and not river.gutshot and not river.flush_draw


# --- v2 encoding -------------------------------------------------------------------------


def test_size_and_names_match() -> None:
    assert len(NAMES) == FEATURE_SIZE == len(set(NAMES))


def test_heads_up_and_six_max_share_the_layout() -> None:
    heads_up = HeadsUpHand((200, 200), Seat.PLAYER_0, 1, 2, seed=3)
    table = TableHand([200, 200], 0, 1, 2, seed=3)

    from_heads_up = encode(heads_up.observe(Seat.PLAYER_0), 2)
    from_table = encode(table.observe(0), 2)

    assert from_heads_up == from_table
    assert value(from_heads_up, "opp1_present") == 1.0
    assert value(from_heads_up, "opp2_present") == 0.0
    assert value(from_heads_up, "table_size") == 0.0


def test_six_max_fills_every_opponent_slot_in_seat_order() -> None:
    stacks = [100, 90, 80, 70, 60, 50]
    hand = TableHand(stacks, 0, 1, 2, seed=11)
    actor = hand.betting.current_actor
    assert actor == 3

    encoded = encode(hand.observe(3), 2)

    assert value(encoded, "table_size") == 1.0
    for slot in range(1, 6):
        seat = (3 + slot) % 6
        assert value(encoded, f"opp{slot}_present") == 1.0
        expected = hand.betting.seats[seat].stack / 2 / 100
        assert value(encoded, f"opp{slot}_stack") == pytest.approx(expected)
    assert value(encoded, "me_stack") == pytest.approx(70 / 2 / 100)


def test_amounts_are_in_big_blinds() -> None:
    small = TableHand([200, 200, 200], 0, 1, 2, seed=5)
    large = TableHand([2000, 2000, 2000], 0, 10, 20, seed=5)

    assert encode(small.observe(0), 2) == pytest.approx(encode(large.observe(0), 20))


def test_tournament_fields_are_zero_in_cash_and_filled_in_tournaments() -> None:
    hand = TableHand([30, 25, 20, 15, 10, 20], 1, 50, 100, seed=2)
    actor = hand.betting.current_actor
    assert actor is not None
    cash = encode(hand.observe(actor), 100)
    context = TournamentContext(
        starting_stack=1_000,
        level=3,
        hands_to_next_level=4,
        hands_per_level=10,
        players_left=6,
        ante=10,
        payout=(0.65, 0.35),
        bounties=(1.0, 1.0, 1.5, 1.0, 2.0, 1.0),
    )

    tournament = encode(hand.observe(actor), 100, small_blind=50, tournament=context)

    for name in ("sb_over_start", "bb_over_start", "players_left", "payout_1", "payout_2"):
        assert value(cash, name) == 0.0
    assert value(tournament, "bb_over_start") == pytest.approx(0.1)
    assert value(tournament, "sb_over_start") == pytest.approx(0.05)
    assert value(tournament, "ante_over_bb") == pytest.approx(0.1)
    assert value(tournament, "level_progress") == pytest.approx(0.4)
    assert [value(tournament, f"payout_{place}") for place in range(1, 7)] == [
        0.65,
        0.35,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    assert value(tournament, "players_left") == pytest.approx(1.0)
    assert value(tournament, "own_bounty") == context.bounties[actor]
    opponent_seat = (actor + 1) % 6
    assert value(tournament, "opp1_bounty") == context.bounties[opponent_seat]


@given(
    players=st.integers(2, 6),
    seed=st.integers(0, 2**32),
    stacks=st.lists(st.integers(1, 20_000), min_size=6, max_size=6),
)
@settings(max_examples=100, deadline=None)
def test_features_stay_in_a_small_range(players: int, seed: int, stacks: list[int]) -> None:
    hand = TableHand(stacks[:players], seed % players, 1, 2, seed=seed)
    actor = hand.betting.current_actor
    if actor is None:
        return
    context = TournamentContext(1_000, 3, 2, 10, players, 1, (0.65, 0.35), (1.0,) * players)

    encoded = encode(hand.observe(actor), 2, tournament=context)

    # Stacks are in units of 100 BB, so 20,000 chips at BB 2 is 100; nothing else is large.
    assert all(-1.0 <= item <= 101.0 for item in encoded)
    ratios = ("to_call_over_pot", "effective_over_pot")
    assert all(0.0 <= value(encoded, name) <= 1.0 for name in ratios)


def test_payout_longer_than_the_table_is_rejected() -> None:
    with pytest.raises(ValueError):
        TournamentContext(1_000, 1, 1, 10, 6, payout=(0.2,) * (MAX_PLAYERS + 1))


def test_postflop_features_appear_after_the_flop() -> None:
    hand = HeadsUpHand((200, 200), Seat.PLAYER_0, 1, 2, seed=8)
    preflop = encode(hand.observe(Seat.PLAYER_0), 2)
    hand.act(Seat.PLAYER_0, Action.call())
    hand.act(Seat.PLAYER_1, Action.check())
    flop = encode(hand.observe(Seat.PLAYER_1), 2)

    assert value(preflop, "post_present") == 0.0
    assert value(flop, "post_present") == 1.0
    assert sum(value(flop, f"post_{category.name.lower()}") for category in HandCategory) == 1.0


# --- information isolation ---------------------------------------------------------------


@given(
    players=st.integers(2, 6),
    seed=st.integers(0, 2**32),
    swap_seed=st.integers(0, 2**32),
    steps=st.integers(0, 12),
)
@settings(max_examples=150, deadline=None)
def test_other_players_cards_and_future_board_do_not_change_the_encoding(
    players: int, seed: int, swap_seed: int, steps: int
) -> None:
    hand = TableHand([150] * players, seed % players, 1, 2, seed=seed)
    bots = [RandomBot(seed * 10 + index) for index in range(players)]
    for _ in range(steps):
        actor = hand.betting.current_actor
        if actor is None or hand.complete:
            break
        observation = hand.observe(actor)
        assert observation.legal_actions is not None
        hand.act(actor, bots[actor].act(observation, observation.legal_actions))
    actor = hand.betting.current_actor
    if actor is None or hand.complete:
        return
    before = encode(hand.observe(actor), 2)

    visible = set(hand.hole_cards[actor]) | set(hand.board)
    hidden = [card for card in full_deck() if card not in visible]
    random.Random(swap_seed).shuffle(hidden)
    replacement = iter(hidden)
    hand.hole_cards = tuple(
        pair if index == actor else (next(replacement), next(replacement))
        for index, pair in enumerate(hand.hole_cards)
    )
    unseen_board = [card for card in hidden if card not in {c for p in hand.hole_cards for c in p}]
    dealt = len(hand.board)
    runout = [*hand._flop, hand._turn, hand._river]  # noqa: SLF001
    for index in range(dealt, 5):
        runout[index] = unseen_board.pop()
    hand._flop = (runout[0], runout[1], runout[2])  # noqa: SLF001
    hand._turn, hand._river = runout[3], runout[4]  # noqa: SLF001

    assert encode(hand.observe(actor), 2) == before
