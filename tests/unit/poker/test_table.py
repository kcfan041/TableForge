from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.poker.betting import (
    Action,
    ActionType,
    BettingStatus,
    IllegalActionError,
    LegalActions,
    Seat,
    Street,
)
from tableforge.poker.cards import Card, Rank, Suit
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.observation import Observation
from tableforge.poker.runner import HandRunner
from tableforge.poker.table import (
    TableHand,
    TableObservation,
    TablePositions,
    TableRunner,
)


class Scripted:
    """Plays a fixed list of actions, then check/calls."""

    def __init__(self, actions: list[Action]) -> None:
        self.actions = list(actions)

    def act(self, observation: object, legal: LegalActions) -> Action:
        if self.actions:
            return self.actions.pop(0)
        return Action.check() if legal.can_check else Action.call()


def play(hand: TableHand, script: list[tuple[int, Action]]) -> None:
    for seat, action in script:
        assert hand.betting.current_actor == seat, (hand.betting.current_actor, seat)
        hand.act(seat, action)


# --- positions ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("players", "button", "sb", "bb", "first_pre", "first_post"),
    [
        (2, 0, 0, 1, 0, 1),
        (2, 1, 1, 0, 1, 0),
        (3, 0, 1, 2, 0, 1),
        (6, 0, 1, 2, 3, 1),
        (6, 4, 5, 0, 1, 5),
        (9, 0, 1, 2, 3, 1),
        (9, 7, 8, 0, 1, 8),
    ],
)
def test_positions(
    players: int, button: int, sb: int, bb: int, first_pre: int, first_post: int
) -> None:
    positions = TablePositions.for_table(players, button)

    assert (positions.small_blind, positions.big_blind) == (sb, bb)
    assert (positions.first_preflop, positions.first_postflop) == (first_pre, first_post)


@pytest.mark.parametrize("players", [1, 10])
def test_table_size_is_limited(players: int) -> None:
    with pytest.raises(ValueError):
        TableHand([100] * players, 0, 1, 2, seed=1)


def test_six_max_preflop_and_postflop_order() -> None:
    hand = TableHand([100] * 6, 0, 1, 2, seed=5)

    assert hand.betting.seats[1].total_contribution == 1
    assert hand.betting.seats[2].total_contribution == 2
    play(hand, [(3, Action.call()), (4, Action.call()), (5, Action.fold())])
    play(hand, [(0, Action.call()), (1, Action.call()), (2, Action.check())])

    assert hand.betting.street is Street.FLOP
    assert hand.betting.current_actor == 1
    play(hand, [(1, Action.check()), (2, Action.check()), (3, Action.check())])
    assert hand.betting.current_actor == 4


def test_big_blind_gets_option_after_limps() -> None:
    hand = TableHand([100] * 3, 0, 1, 2, seed=3)
    play(hand, [(0, Action.call()), (1, Action.call())])

    legal = hand.legal_actions(2)

    assert legal.can_check and legal.min_raise_to == 4


def test_folds_end_hand_with_last_player() -> None:
    hand = TableHand([100] * 4, 0, 1, 2, seed=8)
    play(hand, [(3, Action.fold()), (0, Action.fold()), (1, Action.fold())])

    assert hand.result is not None
    assert hand.result.ended_by_fold
    assert hand.result.winners == (2,)
    assert hand.result.final_stacks == (100, 99, 101, 100)


def test_short_all_in_does_not_reopen_raising() -> None:
    hand = TableHand([100, 100, 100, 25], 0, 1, 2, seed=9)
    play(hand, [(3, Action.raise_to(20)), (0, Action.call()), (1, Action.fold())])
    play(hand, [(2, Action.call())])
    play(hand, [(2, Action.check()), (3, Action.raise_to(5))])

    assert hand.betting.seats[3].all_in
    play(hand, [(0, Action.raise_to(10))])
    legal_2 = hand.legal_actions(2)
    assert legal_2.min_raise_to == 15
    play(hand, [(2, Action.call())])
    assert hand.betting.street is Street.TURN


def test_incomplete_all_in_raise_does_not_reopen_for_the_original_bettor() -> None:
    hand = TableHand([100, 100, 16], 0, 1, 2, seed=11)
    play(hand, [(0, Action.call()), (1, Action.call()), (2, Action.check())])
    play(hand, [(1, Action.raise_to(10)), (2, Action.raise_to(14))])

    assert hand.betting.seats[2].all_in
    legal_0 = hand.legal_actions(0)
    assert legal_0.min_raise_to == 24
    play(hand, [(0, Action.call())])
    legal_1 = hand.legal_actions(1)
    assert legal_1.min_raise_to is None
    assert legal_1.call_amount == 4
    play(hand, [(1, Action.call())])
    assert hand.betting.street is Street.TURN


def test_illegal_actions_are_rejected() -> None:
    hand = TableHand([100] * 3, 0, 1, 2, seed=1)

    with pytest.raises(IllegalActionError):
        hand.act(1, Action.call())
    with pytest.raises(IllegalActionError):
        hand.act(0, Action.check())
    with pytest.raises(IllegalActionError):
        hand.act(0, Action.raise_to(3))


# --- side pots ---------------------------------------------------------------------------


def rigged(players: int, stacks: list[int], hole: list[tuple[str, str]], board: str) -> TableHand:
    hand = TableHand(stacks, 0, 1, 2, seed=0)
    hand.hole_cards = tuple((card(a), card(b)) for a, b in hole)
    cards = [card(item) for item in board.split()]
    hand._flop = (cards[0], cards[1], cards[2])  # noqa: SLF001
    hand._turn, hand._river = cards[3], cards[4]  # noqa: SLF001
    return hand


def card(text: str) -> Card:
    ranks = {r.name[0] if r.name != "TEN" else "T": r for r in Rank}
    ranks.update({"T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING, "A": Rank.ACE})
    for r in Rank:
        if int(r) <= 9:
            ranks[str(int(r))] = r
    suits = {"s": Suit.SPADES, "h": Suit.HEARTS, "d": Suit.DIAMONDS, "c": Suit.CLUBS}
    return Card(ranks[text[0]], suits[text[1]])


def test_three_way_all_in_builds_main_and_side_pots() -> None:
    hand = rigged(
        3,
        [30, 60, 100],
        [("As", "Ah"), ("Ks", "Kh"), ("Qs", "Qh")],
        "2c 7d 9s Jc 3h",
    )
    play(hand, [(0, Action.raise_to(30)), (1, Action.raise_to(60)), (2, Action.call())])

    result = hand.result
    assert result is not None and not result.ended_by_fold
    assert [pot.amount for pot in result.pots] == [90, 60]
    assert result.pots[0].winners == (0,) and result.pots[1].winners == (1,)
    assert result.final_stacks == (90, 60, 40)
    assert result.winners == (0, 1)


def test_uncalled_excess_returns_to_bettor() -> None:
    hand = rigged(2, [50, 200], [("2s", "3h"), ("As", "Ah")], "Kc Kd 9s Jc 4h")
    play(hand, [(0, Action.call()), (1, Action.raise_to(200)), (0, Action.call())])

    result = hand.result
    assert result is not None
    assert result.final_stacks == (0, 250)
    assert result.pots[-1].amount == 150 and result.pots[-1].eligible == (1,)


def test_split_pot_gives_odd_chip_to_first_winner_left_of_button() -> None:
    hand = rigged(
        3,
        [100, 100, 100],
        [("2s", "3h"), ("4d", "5c"), ("6h", "7d")],
        "As Ks Qs Js Ts",
    )
    play(hand, [(0, Action.call()), (1, Action.fold()), (2, Action.check())])
    for _ in range(3):
        play(hand, [(2, Action.check()), (0, Action.check())])

    result = hand.result
    assert result is not None and not result.ended_by_fold
    assert result.pots[0].amount == 5
    assert result.pots[0].winners == (2, 0)
    assert result.final_stacks == (100, 99, 101)


# --- information isolation ---------------------------------------------------------------


def test_observation_hides_other_hole_cards() -> None:
    hand = TableHand([100] * 6, 2, 1, 2, seed=42)

    for seat in range(6):
        observation = hand.observe(seat)
        assert observation.hole_cards == hand.hole_cards[seat]
        others = {
            card for index, cards in enumerate(hand.hole_cards) if index != seat for card in cards
        }
        text = repr(observation)
        assert all(repr(card) not in text for card in others)
        assert observation.board == ()
        assert (observation.legal_actions is not None) == (seat == hand.betting.current_actor)


def test_hole_cards_are_distinct_and_board_is_hidden_until_dealt() -> None:
    hand = TableHand([100] * 6, 0, 1, 2, seed=7)
    cards = [c for pair in hand.hole_cards for c in pair]
    board = [*hand._flop, hand._turn, hand._river]  # noqa: SLF001

    assert len(set(cards + board)) == 17


# --- heads-up parity ---------------------------------------------------------------------


class HeadsUpView:
    """Runs a heads-up agent inside the table engine by converting observations."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        from tableforge.poker.observation import PlayerObservation, PublicAction

        converted = Observation(
            schema_version="poker-hunlhe-observation-v1",
            observer=Seat(observation.observer),
            button=Seat(observation.button),
            street=observation.street,
            status=observation.status,
            current_actor=None
            if observation.current_actor is None
            else Seat(observation.current_actor),
            hole_cards=observation.hole_cards,
            board=observation.board,
            pot=observation.pot,
            current_bet=observation.current_bet,
            players=tuple(
                PlayerObservation(
                    Seat(p.seat),
                    p.stack,
                    p.street_contribution,
                    p.total_contribution,
                    p.folded,
                    p.all_in,
                )
                for p in observation.players
            ),  # type: ignore[arg-type]
            action_history=tuple(
                PublicAction(
                    a.sequence,
                    a.street,
                    Seat(a.actor),
                    a.action_type,
                    a.raise_to,
                    a.contribution,
                    a.pot_after,
                )
                for a in observation.action_history
            ),
            legal_actions=observation.legal_actions,
        )
        action: Action = self.inner.act(converted, legal)  # type: ignore[attr-defined]
        return action


def agent_pair(kind: int, seed: int) -> Callable[[], tuple[object, object]]:
    def make() -> tuple[object, object]:
        if kind == 0:
            return RandomBot(seed * 2 + 1), RandomBot(seed * 2 + 2)
        if kind == 1:
            return TightBot(), AggroBot()
        return AggroBot(), CallBot()

    return make


@given(
    seed=st.integers(min_value=0, max_value=2**40),
    stack_0=st.integers(min_value=3, max_value=400),
    stack_1=st.integers(min_value=3, max_value=400),
    button=st.sampled_from((0, 1)),
    kind=st.sampled_from((0, 1, 2)),
)
@settings(max_examples=400, deadline=None)
def test_two_players_match_heads_up_engine(
    seed: int, stack_0: int, stack_1: int, button: int, kind: int
) -> None:
    make = agent_pair(kind, seed)
    heads_up = HeadsUpHand((stack_0, stack_1), Seat(button), 1, 2, seed=seed)
    expected = HandRunner().run(heads_up, make())  # type: ignore[arg-type]
    table = TableHand([stack_0, stack_1], button, 1, 2, seed=seed)
    left, right = make()
    actual = TableRunner().run(table, [HeadsUpView(left), HeadsUpView(right)])

    assert table.hole_cards == heads_up.hole_cards
    assert [(a.actor, a.action_type, a.raise_to, a.contribution) for a in table.action_history] == [
        (int(a.actor), a.action_type, a.raise_to, a.contribution) for a in heads_up.action_history
    ]
    assert actual.final_stacks == expected.final_stacks
    assert actual.ended_by_fold == expected.ended_by_fold


# --- properties --------------------------------------------------------------------------


@dataclass(frozen=True)
class TableCase:
    stacks: list[int]
    button: int
    seed: int
    agent_seeds: list[int]


@st.composite
def tables(draw: st.DrawFn) -> TableCase:
    players = draw(st.integers(min_value=2, max_value=9))
    return TableCase(
        stacks=draw(st.lists(st.integers(1, 400), min_size=players, max_size=players)),
        button=draw(st.integers(0, players - 1)),
        seed=draw(st.integers(0, 2**40)),
        agent_seeds=draw(st.lists(st.integers(0, 2**40), min_size=players, max_size=players)),
    )


@given(case=tables())
@settings(max_examples=500, deadline=None)
def test_random_tables_conserve_chips_and_pay_eligible_winners(case: TableCase) -> None:
    hand = TableHand(case.stacks, case.button, 1, 2, seed=case.seed)
    result = TableRunner().run(hand, [RandomBot(s) for s in case.agent_seeds])

    contributions = [state.total_contribution for state in hand.betting.seats]
    assert sum(result.final_stacks) == sum(case.stacks)
    assert all(stack >= 0 for stack in result.final_stacks)
    assert sum(result.payouts) == sum(contributions) == sum(p.amount for p in result.pots)
    for state, stack in zip(hand.betting.seats, case.stacks, strict=True):
        assert state.total_contribution <= stack
    for pot in result.pots:
        assert set(pot.winners) <= set(pot.eligible)
        assert not any(hand.betting.seats[index].folded for index in pot.winners)
    for index, state in enumerate(hand.betting.seats):
        if state.folded:
            assert result.payouts[index] == 0
    if not result.ended_by_fold:
        assert hand.betting.status is BettingStatus.SHOWDOWN
    assert all(a.action_type in ActionType for a in hand.action_history)


@given(case=tables())
@settings(max_examples=200, deadline=None)
def test_replay_is_deterministic(case: TableCase) -> None:
    first = TableHand(case.stacks, case.button, 1, 2, seed=case.seed)
    second = TableHand(case.stacks, case.button, 1, 2, seed=case.seed)

    a = TableRunner().run(first, [RandomBot(s) for s in case.agent_seeds])
    b = TableRunner().run(second, [RandomBot(s) for s in case.agent_seeds])

    assert first.action_history == second.action_history
    assert a == b
