"""No-limit hold'em for 2 to 6 players.

This engine is independent of ``HeadsUpHand`` and does not change it. With two players
it follows the heads-up conventions (the button posts the small blind, acts first
preflop and last postflop) and produces the same hand as ``HeadsUpHand`` for the same
seed and actions, except where the heads-up engine skips a decision: when a blind poster
is all-in for less than the other blind, ``HeadsUpHand`` goes straight to showdown, while
this engine lets the remaining player call or fold.

Positions for three or more players: small blind is left of the button, big blind left
of the small blind, the first preflop actor is left of the big blind, and postflop action
starts left of the button. Cards are dealt one at a time starting with the small blind.

Side pots are built in layers of contribution. Each layer goes to the best eligible
(non-folded, contributed at least that much) hand; ties split evenly and odd chips go one
at a time to the tied winners in seat order starting left of the button. A layer only one
player reached is an uncalled bet and returns to that player.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tableforge.poker.betting import (
    Action,
    ActionType,
    BettingStatus,
    IllegalActionError,
    LegalActions,
    Street,
)
from tableforge.poker.cards import Card
from tableforge.poker.deck import Deck
from tableforge.poker.environment import HandCompleteError
from tableforge.poker.evaluator import HandRank, evaluate

MIN_PLAYERS = 2
MAX_PLAYERS = 6
OBSERVATION_SCHEMA = "poker-nlhe-table-observation-v1"


@dataclass(slots=True)
class _SeatState:
    stack: int
    street_contribution: int = 0
    total_contribution: int = 0
    folded: bool = False

    @property
    def all_in(self) -> bool:
        return self.stack == 0 and not self.folded


@dataclass(frozen=True, slots=True)
class TablePositions:
    players: int
    button: int
    small_blind: int
    big_blind: int
    first_preflop: int
    first_postflop: int
    first_dealt: int

    @classmethod
    def for_table(cls, players: int, button: int) -> TablePositions:
        if not MIN_PLAYERS <= players <= MAX_PLAYERS:
            raise ValueError(f"players must be between {MIN_PLAYERS} and {MAX_PLAYERS}")
        if not 0 <= button < players:
            raise ValueError("button must be a seat at the table")
        small_blind = button if players == 2 else (button + 1) % players
        big_blind = (small_blind + 1) % players
        return cls(
            players=players,
            button=button,
            small_blind=small_blind,
            big_blind=big_blind,
            first_preflop=(big_blind + 1) % players,
            first_postflop=(button + 1) % players,
            first_dealt=small_blind,
        )


class TableBetting:
    def __init__(
        self,
        stacks: Sequence[int],
        button: int,
        small_blind: int,
        big_blind: int,
        big_blind_ante: int = 0,
        extra_dead_money: int = 0,
    ) -> None:
        if small_blind <= 0 or big_blind <= small_blind:
            raise ValueError("blinds must satisfy 0 < small_blind < big_blind")
        if big_blind_ante < 0 or extra_dead_money < 0:
            raise ValueError("ante cannot be negative")
        if any(stack <= 0 for stack in stacks):
            raise ValueError("starting stacks must be positive")
        self.positions = TablePositions.for_table(len(stacks), button)
        self.players = len(stacks)
        self.button = button
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.street = Street.PREFLOP
        self.status = BettingStatus.ACTIVE
        self.winner: int | None = None
        self.seats = [_SeatState(stack) for stack in stacks]
        self.current_bet = 0
        self.last_full_raise = big_blind
        self.pending: set[int] = set()
        self.raise_rights: set[int] = set()
        self.current_actor: int | None = None
        self._contribute(
            self.positions.small_blind, min(small_blind, stacks[self.positions.small_blind])
        )
        self._contribute(self.positions.big_blind, min(big_blind, stacks[self.positions.big_blind]))
        # Big blind ante: posted by the big blind after the blind (the blind has priority
        # when short). It is dead money in the main pot: not a bet, not part of anyone's
        # contribution, and every player still in the hand can win it.
        big_blind_seat = self.seats[self.positions.big_blind]
        self.dead_money = min(big_blind_ante, big_blind_seat.stack)
        big_blind_seat.stack -= self.dead_money
        # Chips already in the pot from outside these stacks (antes every player posted,
        # when replaying a hand history): dead money like the big blind ante.
        self.dead_money += extra_dead_money
        self.current_bet = max(seat.street_contribution for seat in self.seats)
        self._open_round(self.positions.first_preflop)
        self._assert_invariants()

    @property
    def pot(self) -> int:
        return sum(seat.total_contribution for seat in self.seats) + self.dead_money

    def legal_actions(self, seat: int) -> LegalActions:
        if self.status is not BettingStatus.ACTIVE or seat != self.current_actor:
            raise IllegalActionError("seat is not the current actor")
        player = self.seats[seat]
        to_call = self.current_bet - player.street_contribution
        max_raise_to = player.street_contribution + player.stack
        can_raise = seat in self.raise_rights and max_raise_to > self.current_bet
        minimum = self.current_bet + self.last_full_raise
        all_in_only = can_raise and max_raise_to < minimum
        min_raise_to = max_raise_to if all_in_only else minimum if can_raise else None
        return LegalActions(
            can_fold=to_call > 0,
            can_check=to_call == 0,
            call_amount=min(to_call, player.stack) if to_call > 0 else None,
            min_raise_to=min_raise_to,
            max_raise_to=max_raise_to if can_raise else None,
            all_in_only=all_in_only,
        )

    def act(self, seat: int, action: Action) -> None:
        legal = self.legal_actions(seat)
        if action.type is ActionType.FOLD:
            if not legal.can_fold:
                raise IllegalActionError("fold is not legal")
            self.seats[seat].folded = True
            self.pending.discard(seat)
            self.raise_rights.discard(seat)
            remaining = [index for index, state in enumerate(self.seats) if not state.folded]
            if len(remaining) == 1:
                self.pending.clear()
                self.raise_rights.clear()
                self.current_actor = None
                self.status = BettingStatus.FOLDED
                self.winner = remaining[0]
        elif action.type is ActionType.CHECK:
            if not legal.can_check:
                raise IllegalActionError("check is not legal")
            self._complete_action(seat)
        elif action.type is ActionType.CALL:
            if legal.call_amount is None:
                raise IllegalActionError("call is not legal")
            self._contribute(seat, legal.call_amount)
            self._complete_action(seat)
        elif action.type is ActionType.RAISE_TO:
            self._raise_to(seat, action.amount, legal)
        else:
            raise IllegalActionError(f"unsupported action: {action.type}")
        if self.status is BettingStatus.ACTIVE:
            self._advance_after_action(seat)
        self._assert_invariants()

    def actionable(self) -> set[int]:
        return {
            index for index, state in enumerate(self.seats) if not state.folded and not state.all_in
        }

    def _raise_to(self, seat: int, amount: int | None, legal: LegalActions) -> None:
        if amount is None or legal.min_raise_to is None or legal.max_raise_to is None:
            raise IllegalActionError("raise is not legal")
        if amount < legal.min_raise_to or amount > legal.max_raise_to:
            raise IllegalActionError("raise_to amount is outside legal bounds")
        previous_bet = self.current_bet
        player = self.seats[seat]
        self._contribute(seat, amount - player.street_contribution)
        self.current_bet = amount
        raise_size = amount - previous_bet
        self.pending = self.actionable() - {seat}
        self.raise_rights.discard(seat)
        if raise_size >= self.last_full_raise:
            self.last_full_raise = raise_size
            self.raise_rights = self.actionable() - {seat}

    def _complete_action(self, seat: int) -> None:
        self.pending.discard(seat)
        self.raise_rights.discard(seat)

    def _advance_after_action(self, acting_seat: int) -> None:
        self.pending.intersection_update(self.actionable())
        if self.pending:
            self.current_actor = self._next_pending(acting_seat)
            return
        self._finish_round()

    def _finish_round(self) -> None:
        if self.street is Street.RIVER or len(self.actionable()) < 2:
            self._to_showdown()
            return
        self.street = Street(self.street + 1)
        for state in self.seats:
            state.street_contribution = 0
        self.current_bet = 0
        self.last_full_raise = self.big_blind
        self._open_round(self.positions.first_postflop)

    def _open_round(self, first: int) -> None:
        actionable = self.actionable()
        owes = any(self.seats[index].street_contribution < self.current_bet for index in actionable)
        if not actionable or (len(actionable) < 2 and not owes):
            self._to_showdown()
            return
        self.pending = set(actionable)
        self.raise_rights = set(actionable)
        self.current_actor = first if first in self.pending else self._next_pending(first)

    def _to_showdown(self) -> None:
        self.status = BettingStatus.SHOWDOWN
        self.pending.clear()
        self.raise_rights.clear()
        self.current_actor = None

    def _contribute(self, seat: int, amount: int) -> None:
        player = self.seats[seat]
        if amount < 0 or amount > player.stack:
            raise IllegalActionError("invalid contribution")
        player.stack -= amount
        player.street_contribution += amount
        player.total_contribution += amount

    def _next_pending(self, after: int) -> int | None:
        for offset in range(1, self.players + 1):
            candidate = (after + offset) % self.players
            if candidate in self.pending:
                return candidate
        return None

    def _assert_invariants(self) -> None:
        for state in self.seats:
            if state.stack < 0 or state.street_contribution < 0:
                raise AssertionError("negative stack or contribution")
            if state.street_contribution > state.total_contribution:
                raise AssertionError("street contribution exceeds total contribution")
        if self.status is BettingStatus.ACTIVE and self.current_actor not in self.pending:
            raise AssertionError("current actor must be pending")


@dataclass(frozen=True, slots=True)
class TablePublicAction:
    sequence: int
    street: Street
    actor: int
    action_type: ActionType
    raise_to: int | None
    contribution: int
    pot_after: int


@dataclass(frozen=True, slots=True)
class TablePlayerObservation:
    seat: int
    stack: int
    street_contribution: int
    total_contribution: int
    folded: bool
    all_in: bool


@dataclass(frozen=True, slots=True)
class TableObservation:
    """What one seat can see. Other players' hole cards are never included."""

    schema_version: str
    players_at_table: int
    observer: int
    button: int
    small_blind_seat: int
    big_blind_seat: int
    street: Street
    status: BettingStatus
    current_actor: int | None
    hole_cards: tuple[Card, Card]
    board: tuple[Card, ...]
    pot: int
    current_bet: int
    players: tuple[TablePlayerObservation, ...]
    action_history: tuple[TablePublicAction, ...]
    legal_actions: LegalActions | None


@dataclass(frozen=True, slots=True)
class PotAward:
    amount: int
    eligible: tuple[int, ...]
    winners: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TableResult:
    payouts: tuple[int, ...]
    final_stacks: tuple[int, ...]
    winners: tuple[int, ...]
    ranks: tuple[HandRank | None, ...]
    ended_by_fold: bool
    pots: tuple[PotAward, ...]


class TableHand:
    def __init__(
        self,
        stacks: Sequence[int],
        button: int,
        small_blind: int,
        big_blind: int,
        seed: int | str | bytes,
        big_blind_ante: int = 0,
        extra_dead_money: int = 0,
        cards: tuple[tuple[tuple[Card, Card], ...], tuple[Card, ...]] | None = None,
    ) -> None:
        """`extra_dead_money` and `cards` (hole cards per seat and five board cards) are for
        replaying recorded hands; normal play leaves them at their defaults."""
        self.initial_stacks = tuple(stacks)
        self.extra_dead_money = extra_dead_money
        self.button = button
        self.betting = TableBetting(
            stacks, button, small_blind, big_blind, big_blind_ante, extra_dead_money
        )
        self.players = len(stacks)
        self._deck = Deck.shuffled(seed)
        if cards is None:
            self.hole_cards = self._deal_hole_cards()
            self._flop, self._turn, self._river = self._deal_board_runout()
        else:
            holes, board = cards
            dealt = [card for pair in holes for card in pair] + list(board)
            if len(holes) != self.players or len(board) != 5 or len(set(dealt)) != len(dealt):
                raise ValueError("give two different cards per seat and five board cards")
            self.hole_cards = tuple(holes)
            self._flop = (board[0], board[1], board[2])
            self._turn, self._river = board[3], board[4]
        self.action_history: list[TablePublicAction] = []
        self.result: TableResult | None = None
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
    def runout(self) -> tuple[Card, ...]:
        """All five board cards, including those not dealt yet (for replaying a hand)."""
        return (*self._flop, self._turn, self._river)

    @property
    def complete(self) -> bool:
        return self.result is not None

    def legal_actions(self, seat: int) -> LegalActions:
        if self.complete:
            raise HandCompleteError("hand is complete")
        return self.betting.legal_actions(seat)

    def act(self, seat: int, action: Action) -> None:
        if self.complete:
            raise HandCompleteError("hand is complete")
        street = self.betting.street
        before = self.betting.seats[seat].total_contribution
        self.betting.act(seat, action)
        self.action_history.append(
            TablePublicAction(
                sequence=len(self.action_history),
                street=street,
                actor=seat,
                action_type=action.type,
                raise_to=action.amount,
                contribution=self.betting.seats[seat].total_contribution - before,
                pot_after=self.betting.pot,
            )
        )
        self._settle_if_complete()

    def observe(self, seat: int) -> TableObservation:
        if not 0 <= seat < self.players:
            raise ValueError("seat is not at this table")
        legal = None
        if not self.complete and self.betting.current_actor == seat:
            legal = self.betting.legal_actions(seat)
        positions = self.betting.positions
        return TableObservation(
            schema_version=OBSERVATION_SCHEMA,
            players_at_table=self.players,
            observer=seat,
            button=self.button,
            small_blind_seat=positions.small_blind,
            big_blind_seat=positions.big_blind,
            street=self.betting.street,
            status=self.betting.status,
            current_actor=self.betting.current_actor,
            hole_cards=self.hole_cards[seat],
            board=self.board,
            pot=self.betting.pot,
            current_bet=self.betting.current_bet,
            players=tuple(
                TablePlayerObservation(
                    seat=index,
                    stack=state.stack,
                    street_contribution=state.street_contribution,
                    total_contribution=state.total_contribution,
                    folded=state.folded,
                    all_in=state.all_in,
                )
                for index, state in enumerate(self.betting.seats)
            ),
            action_history=tuple(self.action_history),
            legal_actions=legal,
        )

    def _deal_hole_cards(self) -> tuple[tuple[Card, Card], ...]:
        order = [
            (self.betting.positions.first_dealt + offset) % self.players
            for offset in range(self.players)
        ]
        first_round = self._deck.deal(self.players)
        second_round = self._deck.deal(self.players)
        dealt = {
            seat: (first_round[index], second_round[index]) for index, seat in enumerate(order)
        }
        return tuple(dealt[seat] for seat in range(self.players))

    def _deal_board_runout(self) -> tuple[tuple[Card, Card, Card], Card, Card]:
        self._deck.deal()
        flop = self._deck.deal(3)
        self._deck.deal()
        turn = self._deck.deal()[0]
        self._deck.deal()
        river = self._deck.deal()[0]
        return (flop[0], flop[1], flop[2]), turn, river

    def _settle_if_complete(self) -> None:
        if self.betting.status is BettingStatus.ACTIVE:
            return
        seats = self.betting.seats
        contributions = [state.total_contribution for state in seats]
        dead_money = self.betting.dead_money
        payouts = [0] * self.players
        ranks: list[HandRank | None] = [None] * self.players
        pots: list[PotAward] = []
        showdown_winners: set[int] = set()
        if self.betting.status is BettingStatus.FOLDED:
            winner = self.betting.winner
            if winner is None:
                raise AssertionError("folded hand must have a winner")
            total = sum(contributions) + dead_money
            payouts[winner] = total
            pots.append(PotAward(total, (winner,), (winner,)))
            winners: tuple[int, ...] = (winner,)
            ended_by_fold = True
        else:
            board = (*self._flop, self._turn, self._river)
            live = [index for index, state in enumerate(seats) if not state.folded]
            for index in live:
                ranks[index] = evaluate((*self.hole_cards[index], *board))
            order = [(self.button + 1 + offset) % self.players for offset in range(self.players)]
            layers: list[tuple[int, tuple[int, ...]]] = []
            previous = 0
            for level in sorted({value for value in contributions if value > 0}):
                amount = sum(min(value, level) - min(value, previous) for value in contributions)
                eligible = [index for index in live if contributions[index] >= level]
                if not eligible:
                    top = max(contributions[index] for index in live)
                    eligible = [index for index in live if contributions[index] == top]
                key = tuple(sorted(eligible))
                if not layers:
                    amount += dead_money
                if layers and layers[-1][1] == key:
                    layers[-1] = (layers[-1][0] + amount, key)
                else:
                    layers.append((amount, key))
                previous = level
            for amount, eligible_seats in layers:
                best = max(rank for index in eligible_seats if (rank := ranks[index]) is not None)
                layer_winners = [
                    index for index in order if index in eligible_seats and ranks[index] == best
                ]
                share, remainder = divmod(amount, len(layer_winners))
                for position, index in enumerate(layer_winners):
                    payouts[index] += share + (1 if position < remainder else 0)
                if len(eligible_seats) > 1:
                    showdown_winners.update(layer_winners)
                pots.append(PotAward(amount, eligible_seats, tuple(layer_winners)))
            winners = tuple(sorted(showdown_winners))
            ended_by_fold = False
        final_stacks = tuple(state.stack + payouts[index] for index, state in enumerate(seats))
        if sum(final_stacks) != sum(self.initial_stacks) + self.extra_dead_money:
            raise AssertionError("chip conservation violated")
        if sum(payouts) != sum(contributions) + dead_money:
            raise AssertionError("payouts do not match the pot")
        self.result = TableResult(
            payouts=tuple(payouts),
            final_stacks=final_stacks,
            winners=winners,
            ranks=tuple(ranks),
            ended_by_fold=ended_by_fold,
            pots=tuple(pots),
        )


class TableAgent(Protocol):
    def act(self, observation: TableObservation, legal_actions: LegalActions) -> Action: ...


@dataclass(frozen=True, slots=True)
class TableRunner:
    max_actions: int = 2_000

    def run(self, hand: TableHand, agents: Sequence[TableAgent]) -> TableResult:
        if len(agents) != hand.players:
            raise ValueError("one agent is required per seat")
        actions = 0
        while not hand.complete:
            actor = hand.betting.current_actor
            if actor is None:
                raise AssertionError("active hand must have a current actor")
            observation = hand.observe(actor)
            legal = observation.legal_actions
            if legal is None:
                raise AssertionError("current actor must receive legal actions")
            hand.act(actor, agents[actor].act(observation, legal))
            actions += 1
            if actions > self.max_actions:
                raise RuntimeError("hand exceeded maximum action count")
        if hand.result is None:
            raise AssertionError("complete hand must have a result")
        return hand.result
