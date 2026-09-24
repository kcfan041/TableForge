from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Seat(IntEnum):
    PLAYER_0 = 0
    PLAYER_1 = 1

    @property
    def other(self) -> Seat:
        return Seat(1 - int(self))


class Street(IntEnum):
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3


class BettingStatus(StrEnum):
    ACTIVE = "active"
    SHOWDOWN = "showdown"
    FOLDED = "folded"


class ActionType(StrEnum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    RAISE_TO = "raise_to"


@dataclass(frozen=True, slots=True)
class Action:
    type: ActionType
    amount: int | None = None

    @classmethod
    def fold(cls) -> Action:
        return cls(ActionType.FOLD)

    @classmethod
    def check(cls) -> Action:
        return cls(ActionType.CHECK)

    @classmethod
    def call(cls) -> Action:
        return cls(ActionType.CALL)

    @classmethod
    def raise_to(cls, amount: int) -> Action:
        return cls(ActionType.RAISE_TO, amount)


@dataclass(frozen=True, slots=True)
class LegalActions:
    can_fold: bool
    can_check: bool
    call_amount: int | None
    min_raise_to: int | None
    max_raise_to: int | None
    all_in_only: bool


class IllegalActionError(ValueError):
    pass


@dataclass(slots=True)
class PlayerBetState:
    stack: int
    street_contribution: int = 0
    total_contribution: int = 0
    folded: bool = False

    @property
    def all_in(self) -> bool:
        return self.stack == 0 and not self.folded


class HeadsUpBetting:
    def __init__(
        self,
        stacks: tuple[int, int],
        button: Seat,
        small_blind: int,
        big_blind: int,
    ) -> None:
        if small_blind <= 0 or big_blind <= small_blind:
            raise ValueError("blinds must satisfy 0 < small_blind < big_blind")
        if any(stack <= 0 for stack in stacks):
            raise ValueError("starting stacks must be positive")
        self.button = button
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.street = Street.PREFLOP
        self.status = BettingStatus.ACTIVE
        self.winner: Seat | None = None
        self.players = {
            Seat.PLAYER_0: PlayerBetState(stacks[0]),
            Seat.PLAYER_1: PlayerBetState(stacks[1]),
        }
        self.current_bet = 0
        self.last_full_raise = big_blind
        self.pending: set[Seat] = set()
        self.raise_rights: set[Seat] = set()
        self.current_actor: Seat | None = None
        self._post_blind(button, small_blind)
        self._post_blind(button.other, big_blind)
        self.current_bet = max(player.street_contribution for player in self.players.values())
        self.pending = self._actionable_seats()
        self.raise_rights = set(self.pending)
        self.current_actor = button if button in self.pending else self._next_pending(button)
        self._settle_if_no_action()
        self._assert_invariants()

    @property
    def pot(self) -> int:
        return sum(player.total_contribution for player in self.players.values())

    def legal_actions(self, seat: Seat) -> LegalActions:
        if self.status is not BettingStatus.ACTIVE or seat != self.current_actor:
            raise IllegalActionError("seat is not the current actor")
        player = self.players[seat]
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

    def act(self, seat: Seat, action: Action) -> None:
        legal = self.legal_actions(seat)
        if action.type is ActionType.FOLD:
            if not legal.can_fold:
                raise IllegalActionError("fold is not legal")
            self.players[seat].folded = True
            self.pending.clear()
            self.raise_rights.clear()
            self.current_actor = None
            self.status = BettingStatus.FOLDED
            self.winner = seat.other
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

    def _raise_to(self, seat: Seat, amount: int | None, legal: LegalActions) -> None:
        if amount is None or legal.min_raise_to is None or legal.max_raise_to is None:
            raise IllegalActionError("raise is not legal")
        if amount < legal.min_raise_to or amount > legal.max_raise_to:
            raise IllegalActionError("raise_to amount is outside legal bounds")
        previous_bet = self.current_bet
        player = self.players[seat]
        self._contribute(seat, amount - player.street_contribution)
        self.current_bet = amount
        raise_size = amount - previous_bet
        full_raise = raise_size >= self.last_full_raise
        self.pending.discard(seat)
        self.raise_rights.discard(seat)
        opponent = seat.other
        if opponent in self._actionable_seats():
            self.pending.add(opponent)
        if full_raise:
            self.last_full_raise = raise_size
            self.raise_rights = self._actionable_seats()
            self.raise_rights.discard(seat)

    def _complete_action(self, seat: Seat) -> None:
        self.pending.discard(seat)
        self.raise_rights.discard(seat)

    def _advance_after_action(self, acting_seat: Seat) -> None:
        self.pending.intersection_update(self._actionable_seats())
        if self.pending:
            self.current_actor = self._next_pending(acting_seat)
            return
        self._finish_round()

    def _finish_round(self) -> None:
        if self.street is Street.RIVER:
            self.status = BettingStatus.SHOWDOWN
            self.current_actor = None
            return
        if len(self._actionable_seats()) < 2:
            self.status = BettingStatus.SHOWDOWN
            self.current_actor = None
            return
        self.street = Street(self.street + 1)
        for player in self.players.values():
            player.street_contribution = 0
        self.current_bet = 0
        self.last_full_raise = self.big_blind
        self.pending = self._actionable_seats()
        self.raise_rights = set(self.pending)
        first_postflop = self.button.other
        self.current_actor = (
            first_postflop if first_postflop in self.pending else self._next_pending(first_postflop)
        )

    def _post_blind(self, seat: Seat, amount: int) -> None:
        self._contribute(seat, min(amount, self.players[seat].stack))

    def _contribute(self, seat: Seat, amount: int) -> None:
        player = self.players[seat]
        if amount < 0 or amount > player.stack:
            raise IllegalActionError("invalid contribution")
        player.stack -= amount
        player.street_contribution += amount
        player.total_contribution += amount

    def _actionable_seats(self) -> set[Seat]:
        return {
            seat
            for seat, player in self.players.items()
            if not player.folded and not player.all_in
        }

    def _next_pending(self, after: Seat) -> Seat | None:
        candidate = after.other
        if candidate in self.pending:
            return candidate
        if after in self.pending:
            return after
        return None

    def _settle_if_no_action(self) -> None:
        if not self.pending or len(self._actionable_seats()) < 2:
            self.status = BettingStatus.SHOWDOWN
            self.current_actor = None

    def _assert_invariants(self) -> None:
        if any(player.stack < 0 for player in self.players.values()):
            raise AssertionError("negative stack")
        if any(player.street_contribution < 0 for player in self.players.values()):
            raise AssertionError("negative street contribution")
        if any(
            player.street_contribution > player.total_contribution
            for player in self.players.values()
        ):
            raise AssertionError("street contribution exceeds total contribution")
        if self.status is BettingStatus.ACTIVE and self.current_actor not in self.pending:
            raise AssertionError("current actor must be pending")
