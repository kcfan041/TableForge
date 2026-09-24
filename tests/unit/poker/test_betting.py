from __future__ import annotations

import pytest

from tableforge.poker.betting import (
    Action,
    BettingStatus,
    HeadsUpBetting,
    IllegalActionError,
    Seat,
    Street,
)


def game(stacks: tuple[int, int] = (100, 100)) -> HeadsUpBetting:
    return HeadsUpBetting(stacks, button=Seat.PLAYER_0, small_blind=1, big_blind=2)


def test_heads_up_blinds_and_preflop_order() -> None:
    betting = game()

    assert betting.players[Seat.PLAYER_0].street_contribution == 1
    assert betting.players[Seat.PLAYER_1].street_contribution == 2
    assert betting.current_actor is Seat.PLAYER_0
    assert betting.pot == 3


def test_call_then_check_advances_to_flop_with_big_blind_first() -> None:
    betting = game()

    betting.act(Seat.PLAYER_0, Action.call())
    betting.act(Seat.PLAYER_1, Action.check())

    assert betting.street is Street.FLOP
    assert betting.current_actor is Seat.PLAYER_1
    assert betting.current_bet == 0
    assert all(player.street_contribution == 0 for player in betting.players.values())


def test_check_check_advances_each_postflop_street() -> None:
    betting = game()
    betting.act(Seat.PLAYER_0, Action.call())
    betting.act(Seat.PLAYER_1, Action.check())

    for expected in (Street.TURN, Street.RIVER):
        betting.act(Seat.PLAYER_1, Action.check())
        betting.act(Seat.PLAYER_0, Action.check())
        assert betting.street is expected

    betting.act(Seat.PLAYER_1, Action.check())
    betting.act(Seat.PLAYER_0, Action.check())
    assert betting.status is BettingStatus.SHOWDOWN
    assert betting.current_actor is None


def test_full_raise_sets_new_minimum_and_reopens_opponent() -> None:
    betting = game()

    betting.act(Seat.PLAYER_0, Action.raise_to(6))
    legal = betting.legal_actions(Seat.PLAYER_1)

    assert legal.call_amount == 4
    assert legal.min_raise_to == 10
    assert legal.max_raise_to == 100

    betting.act(Seat.PLAYER_1, Action.raise_to(10))
    reopened = betting.legal_actions(Seat.PLAYER_0)
    assert reopened.call_amount == 4
    assert reopened.min_raise_to == 14


def test_short_all_in_raise_does_not_reopen_raise_right() -> None:
    betting = HeadsUpBetting((100, 13), button=Seat.PLAYER_0, small_blind=1, big_blind=2)
    betting.act(Seat.PLAYER_0, Action.raise_to(10))

    legal = betting.legal_actions(Seat.PLAYER_1)
    assert legal.min_raise_to == 13
    assert legal.max_raise_to == 13
    assert legal.all_in_only
    betting.act(Seat.PLAYER_1, Action.raise_to(13))

    response = betting.legal_actions(Seat.PLAYER_0)
    assert response.call_amount == 3
    assert response.min_raise_to is None
    assert response.max_raise_to is None


def test_calling_all_in_moves_to_showdown() -> None:
    betting = HeadsUpBetting((20, 20), button=Seat.PLAYER_0, small_blind=1, big_blind=2)
    betting.act(Seat.PLAYER_0, Action.raise_to(20))
    betting.act(Seat.PLAYER_1, Action.call())

    assert betting.status is BettingStatus.SHOWDOWN
    assert betting.pot == 40
    assert all(player.stack == 0 for player in betting.players.values())


def test_fold_awards_uncontested_winner() -> None:
    betting = game()

    betting.act(Seat.PLAYER_0, Action.fold())

    assert betting.status is BettingStatus.FOLDED
    assert betting.winner is Seat.PLAYER_1


def test_illegal_check_does_not_change_state() -> None:
    betting = game()
    before = (
        betting.pot,
        betting.current_actor,
        betting.players[Seat.PLAYER_0].stack,
    )

    with pytest.raises(IllegalActionError):
        betting.act(Seat.PLAYER_0, Action.check())

    assert (
        betting.pot,
        betting.current_actor,
        betting.players[Seat.PLAYER_0].stack,
    ) == before


def test_below_minimum_non_all_in_raise_is_rejected() -> None:
    betting = game()

    with pytest.raises(IllegalActionError):
        betting.act(Seat.PLAYER_0, Action.raise_to(3))


def test_short_stack_blind_posts_only_available_chips() -> None:
    betting = HeadsUpBetting((1, 100), button=Seat.PLAYER_0, small_blind=1, big_blind=2)

    assert betting.players[Seat.PLAYER_0].all_in
    assert betting.players[Seat.PLAYER_0].total_contribution == 1
    assert betting.status is BettingStatus.SHOWDOWN
