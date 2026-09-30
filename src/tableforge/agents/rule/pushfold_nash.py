"""`PushFoldNashBot`: plays the ICM Push/Fold Nash baseline at the table.

This is a reference opponent, not a full-game Nash strategy. Preflop, when nobody has
raised yet it shoves or folds; facing a single all-in it calls or folds; both according
to `tableforge.learning.pushfold_nash.solve` for the actual stacks, blinds, ante, remaining
prizes and bounties of the hand. Anything else (a raise that is not all in, several
all-ins) folds unless checking is free; after the flop it checks or calls. Solutions are
cached per spot (stacks rounded to a quarter of a big blind).

Needs NumPy (the `learning` extra).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tableforge.poker.betting import Action, ActionType, LegalActions, Street
from tableforge.poker.cards import Card
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo

if TYPE_CHECKING:
    from tableforge.learning.pushfold_nash import PushFoldSolution

CACHE_SIZE = 4096
ITERATIONS = 400
"""Most fictitious-play iterations per spot; solving stops once the gap is below TOLERANCE."""
TOLERANCE = 0.0005
"""Buy-ins a player could still gain by deviating when solving stops."""
RANKS = "AKQJT98765432"
_CACHE: OrderedDict[tuple[object, ...], PushFoldSolution] = OrderedDict()
"""Solved spots, shared by every bot in the process (spots repeat across seats)."""


def hand_class(cards: tuple[Card, Card]) -> str:
    """Starting-hand class of two cards, e.g. ``AKs``, ``T9o``, ``77``."""
    first, second = cards
    high, low = sorted((int(first.rank), int(second.rank)), reverse=True)
    name = RANKS[14 - high] + RANKS[14 - low]
    if high == low:
        return name
    return name + ("s" if first.suit == second.suit else "o")


@dataclass(slots=True)
class PushFoldNashBot:
    """Push/Fold Nash baseline player; `seed` only makes mixed actions reproducible."""

    seed: int = 0
    iterations: int = ITERATIONS
    info: TournamentHandInfo | None = field(default=None, repr=False)
    _draws: int = 0

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.info = info

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        passive = Action.check() if legal.can_check else Action.fold()
        if observation.street is not Street.PREFLOP:
            return Action.check() if legal.can_check else Action.call()
        info = self.info
        if info is None:
            return passive
        raises = [
            action
            for action in observation.action_history
            if action.action_type is ActionType.RAISE_TO
        ]
        if not raises:
            if legal.max_raise_to is None:
                return passive
            chance = self.probability(observation, info, None)
            if chance is not None and self._draw(chance):
                return Action.raise_to(legal.max_raise_to)
            return passive
        shover = raises[0].actor
        if len(raises) != 1 or not observation.players[shover].all_in:
            return passive
        if legal.call_amount is None:
            return passive
        chance = self.probability(observation, info, shover)
        if chance is not None and self._draw(chance):
            return Action.call()
        return passive

    def probability(
        self, observation: TableObservation, info: TournamentHandInfo, shover: int | None
    ) -> float | None:
        """Chance to shove (`shover` None) or to call `shover`'s all-in with our hand."""
        from tableforge.poker.pushfold_ranges import HAND_CLASSES

        solution = self._solution(observation, info)
        if solution is None:
            return None
        players = observation.players_at_table
        relative = [(observation.button + offset) % players for offset in range(players)]
        me = relative.index(observation.observer)
        index = HAND_CLASSES.index(hand_class(observation.hole_cards))
        if shover is None:
            push = solution.push.get(me)
            return None if push is None else push[index]
        call = solution.call.get((relative.index(shover), me))
        return None if call is None else call[index]

    def _solution(
        self, observation: TableObservation, info: TournamentHandInfo
    ) -> PushFoldSolution | None:
        from tableforge.learning.pushfold_nash import PushFoldGame, solve

        players = observation.players_at_table
        relative = [(observation.button + offset) % players for offset in range(players)]
        # Stacks at the start of the hand: chips behind plus chips put in (the big blind's
        # ante is dead money and not part of its contribution).
        stacks = []
        for seat in relative:
            player = observation.players[seat]
            chips = player.stack + player.total_contribution
            if seat == observation.big_blind_seat:
                chips += info.ante
            stacks.append(chips)
        quantum = max(1, info.big_blind // 4)
        rounded = tuple(max(quantum, round(stack / quantum) * quantum) for stack in stacks)
        prizes = info.prizes or (1.0,)
        heads: tuple[float, ...] = ()
        if info.bounties and len(info.bounties) == players:
            heads = tuple(round(info.bounties[seat], 4) for seat in relative)
        key = (rounded, info.small_blind, info.big_blind, info.ante, prizes, heads)
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
        try:
            game = PushFoldGame(
                rounded,
                info.small_blind,
                info.big_blind,
                info.ante,
                prizes,
                heads=heads,
                cash_share=info.bounty_cash_share,
                winner_keeps_own=info.winner_keeps_own,
            )
        except ValueError:
            return None
        solution = solve(game, self.iterations, TOLERANCE)
        _CACHE[key] = solution
        if len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
        return solution

    def _draw(self, probability: float) -> bool:
        if probability >= 0.999:
            return True
        if probability <= 0.001:
            return False
        self._draws += 1
        mixed = (self.seed * 2_654_435_761 + self._draws * 40_503) % 1_000_003
        return mixed / 1_000_003 < probability
