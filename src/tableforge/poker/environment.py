from __future__ import annotations

from dataclasses import dataclass

from tableforge.poker.betting import (
    Action,
    BettingStatus,
    HeadsUpBetting,
    LegalActions,
    Seat,
    Street,
)
from tableforge.poker.cards import Card
from tableforge.poker.deck import Deck
from tableforge.poker.evaluator import HandRank, evaluate


@dataclass(frozen=True, slots=True)
class HandResult:
    payouts: tuple[int, int]
    final_stacks: tuple[int, int]
    winners: tuple[Seat, ...]
    ranks: tuple[HandRank | None, HandRank | None]
    ended_by_fold: bool


class HandCompleteError(RuntimeError):
    pass


class HeadsUpHand:
    def __init__(
        self,
        stacks: tuple[int, int],
        button: Seat,
        small_blind: int,
        big_blind: int,
        seed: int | str | bytes,
    ) -> None:
        self.initial_stacks = stacks
        self.button = button
        self._deck = Deck.shuffled(seed)
        self.hole_cards = self._deal_hole_cards()
        self._flop, self._turn, self._river = self._deal_board_runout()
        self.betting = HeadsUpBetting(stacks, button, small_blind, big_blind)
        self.result: HandResult | None = None
        self._settle_if_complete()

    @property
    def board(self) -> tuple[Card, ...]:
        if self.betting.status is BettingStatus.SHOWDOWN:
            return (*self._flop, self._turn, self._river)
        if self.betting.street is Street.PREFLOP:
            return ()
        if self.betting.street is Street.FLOP:
            return self._flop
        if self.betting.street is Street.TURN:
            return (*self._flop, self._turn)
        return (*self._flop, self._turn, self._river)

    @property
    def complete(self) -> bool:
        return self.result is not None

    def legal_actions(self, seat: Seat) -> LegalActions:
        if self.complete:
            raise HandCompleteError("hand is complete")
        return self.betting.legal_actions(seat)

    def act(self, seat: Seat, action: Action) -> None:
        if self.complete:
            raise HandCompleteError("hand is complete")
        self.betting.act(seat, action)
        self._settle_if_complete()

    def _deal_hole_cards(self) -> tuple[tuple[Card, Card], tuple[Card, Card]]:
        first_round = self._deck.deal(2)
        second_round = self._deck.deal(2)
        dealt = {
            self.button: (first_round[0], second_round[0]),
            self.button.other: (first_round[1], second_round[1]),
        }
        return dealt[Seat.PLAYER_0], dealt[Seat.PLAYER_1]

    def _deal_board_runout(self) -> tuple[tuple[Card, Card, Card], Card, Card]:
        self._deck.deal()
        flop_cards = self._deck.deal(3)
        self._deck.deal()
        turn = self._deck.deal()[0]
        self._deck.deal()
        river = self._deck.deal()[0]
        return (flop_cards[0], flop_cards[1], flop_cards[2]), turn, river

    def _settle_if_complete(self) -> None:
        if self.betting.status is BettingStatus.ACTIVE:
            return
        contributions = tuple(
            self.betting.players[seat].total_contribution for seat in Seat
        )
        payouts = [0, 0]
        ranks: list[HandRank | None] = [None, None]
        winners: tuple[Seat, ...]
        if self.betting.status is BettingStatus.FOLDED:
            if self.betting.winner is None:
                raise AssertionError("folded hand must have a winner")
            payouts[int(self.betting.winner)] = sum(contributions)
            winners = (self.betting.winner,)
            ended_by_fold = True
        else:
            matched = min(contributions)
            for seat in Seat:
                payouts[int(seat)] += contributions[int(seat)] - matched
                ranks[int(seat)] = evaluate((*self.hole_cards[int(seat)], *self.board))
            contested_pot = matched * 2
            if ranks[0] is None or ranks[1] is None:
                raise AssertionError("showdown ranks must be available")
            if ranks[0] > ranks[1]:
                payouts[0] += contested_pot
                winners = (Seat.PLAYER_0,)
            elif ranks[1] > ranks[0]:
                payouts[1] += contested_pot
                winners = (Seat.PLAYER_1,)
            else:
                share, odd_chip = divmod(contested_pot, 2)
                payouts[0] += share
                payouts[1] += share
                if odd_chip:
                    odd_chip_seat = self.button.other
                    payouts[int(odd_chip_seat)] += odd_chip
                winners = (Seat.PLAYER_0, Seat.PLAYER_1)
            ended_by_fold = False
        final_stacks = tuple(
            self.betting.players[seat].stack + payouts[int(seat)] for seat in Seat
        )
        if sum(final_stacks) != sum(self.initial_stacks):
            raise AssertionError("chip conservation violated")
        self.result = HandResult(
            payouts=(payouts[0], payouts[1]),
            final_stacks=(final_stacks[0], final_stacks[1]),
            winners=winners,
            ranks=(ranks[0], ranks[1]),
            ended_by_fold=ended_by_fold,
        )
