from __future__ import annotations

from dataclasses import asdict

from tableforge.poker.betting import Action, Seat, Street
from tableforge.poker.environment import HeadsUpHand


def test_observation_contains_only_observers_hole_cards() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=101)

    observation = hand.observe(Seat.PLAYER_0)
    serialized = repr(asdict(observation))

    assert observation.hole_cards == hand.hole_cards[0]
    assert all(str(card) not in serialized for card in hand.hole_cards[1])


def test_preflop_observation_hides_future_board() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=102)

    observation = hand.observe(Seat.PLAYER_0)

    assert observation.board == ()
    assert "_flop" not in observation.__dataclass_fields__
    assert "_deck" not in observation.__dataclass_fields__


def test_legal_actions_are_only_exposed_to_current_actor() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=103)

    assert hand.observe(Seat.PLAYER_0).legal_actions is not None
    assert hand.observe(Seat.PLAYER_1).legal_actions is None


def test_public_action_history_is_identical_for_both_seats() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=104)
    hand.act(Seat.PLAYER_0, Action.call())
    hand.act(Seat.PLAYER_1, Action.check())

    first = hand.observe(Seat.PLAYER_0)
    second = hand.observe(Seat.PLAYER_1)

    assert first.action_history == second.action_history
    assert first.action_history[0].contribution == 1
    assert first.action_history[0].pot_after == 4
    assert first.action_history[1].contribution == 0
    assert first.street is Street.FLOP
    assert len(first.board) == 3


def test_observation_is_snapshot_not_live_state() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=105)
    before = hand.observe(Seat.PLAYER_0)

    hand.act(Seat.PLAYER_0, Action.call())

    assert before.pot == 3
    assert before.action_history == ()
    assert before.players[0].stack == 99


def test_folded_hand_does_not_reveal_hole_cards_or_future_board() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=106)
    opponent_cards = hand.hole_cards[1]
    hand.act(Seat.PLAYER_0, Action.fold())

    observation = hand.observe(Seat.PLAYER_0)
    serialized = repr(asdict(observation))

    assert observation.board == ()
    assert all(str(card) not in serialized for card in opponent_cards)
