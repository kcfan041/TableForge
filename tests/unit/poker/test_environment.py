from __future__ import annotations

from tableforge.poker.betting import Action, BettingStatus, Seat, Street
from tableforge.poker.environment import HeadsUpHand


def check_down(hand: HeadsUpHand) -> None:
    hand.act(Seat.PLAYER_0, Action.call())
    hand.act(Seat.PLAYER_1, Action.check())
    while not hand.complete:
        actor = hand.betting.current_actor
        assert actor is not None
        hand.act(actor, Action.check())


def test_board_is_revealed_by_street() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=1)
    assert hand.board == ()

    hand.act(Seat.PLAYER_0, Action.call())
    hand.act(Seat.PLAYER_1, Action.check())
    assert hand.betting.street is Street.FLOP
    assert len(hand.board) == 3

    hand.act(Seat.PLAYER_1, Action.check())
    hand.act(Seat.PLAYER_0, Action.check())
    assert len(hand.board) == 4

    hand.act(Seat.PLAYER_1, Action.check())
    hand.act(Seat.PLAYER_0, Action.check())
    assert len(hand.board) == 5


def test_fold_settlement_conserves_chips() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=2)

    hand.act(Seat.PLAYER_0, Action.fold())

    assert hand.result is not None
    assert hand.result.ended_by_fold
    assert hand.result.final_stacks == (99, 101)
    assert hand.result.payouts == (0, 3)
    assert sum(hand.result.final_stacks) == 200


def test_checkdown_reaches_showdown_and_conserves_chips() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=3)

    check_down(hand)

    assert hand.betting.status is BettingStatus.SHOWDOWN
    assert hand.result is not None
    assert not hand.result.ended_by_fold
    assert all(rank is not None for rank in hand.result.ranks)
    assert sum(hand.result.final_stacks) == 200


def test_all_in_reveals_complete_board_and_settles() -> None:
    hand = HeadsUpHand((20, 20), Seat.PLAYER_0, 1, 2, seed=4)

    hand.act(Seat.PLAYER_0, Action.raise_to(20))
    hand.act(Seat.PLAYER_1, Action.call())

    assert hand.complete
    assert len(hand.board) == 5
    assert hand.result is not None
    assert sum(hand.result.final_stacks) == 40


def test_unmatched_all_in_contribution_is_refunded() -> None:
    hand = HeadsUpHand((100, 20), Seat.PLAYER_0, 1, 2, seed=5)

    hand.act(Seat.PLAYER_0, Action.raise_to(100))
    hand.act(Seat.PLAYER_1, Action.call())

    assert hand.result is not None
    assert sum(hand.result.payouts) == 120
    assert sum(hand.result.final_stacks) == 120
    assert hand.result.payouts[0] >= 80


def test_split_pot_is_found_and_divided_evenly() -> None:
    tied = None
    for seed in range(10_000):
        candidate = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=seed)
        check_down(candidate)
        assert candidate.result is not None
        if len(candidate.result.winners) == 2:
            tied = candidate
            break

    assert tied is not None
    assert tied.result is not None
    assert tied.result.payouts == (2, 2)
    assert tied.result.final_stacks == (100, 100)


def test_deterministic_hand_replay() -> None:
    first = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed="hand-42")
    second = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed="hand-42")

    check_down(first)
    check_down(second)

    assert first.hole_cards == second.hole_cards
    assert first.board == second.board
    assert first.result == second.result


def test_chip_conservation_over_many_showdowns() -> None:
    for seed in range(1_000):
        hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=seed)
        check_down(hand)
        assert hand.result is not None
        assert sum(hand.result.final_stacks) == 200
        assert sum(hand.result.payouts) == hand.betting.pot
