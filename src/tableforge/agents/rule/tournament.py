"""Rule-based opponents for tournaments.

`HeuristicPushFoldBot` is a simple push-or-fold baseline for short stacks. It is a rule
approximation, not a Nash equilibrium: shove ranges widen as the stack shrinks and the
position gets later; calling ranges are tighter than shoving ranges. It never makes a
non-all-in bet, so at 25-50 BB it is only a reference opponent, not a sensible strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tableforge.poker.betting import Action, ActionType, LegalActions, Street
from tableforge.poker.strength import chen_score
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo


def _players_left_to_act(observation: TableObservation) -> int:
    """Players who have not yet acted preflop and are behind this seat (incl. blinds)."""
    acted = {action.actor for action in observation.action_history}
    count = observation.players_at_table
    total = 0
    for offset in range(1, count):
        seat = (observation.observer + offset) % count
        player = observation.players[seat]
        if player.folded or player.all_in:
            continue
        if seat not in acted:
            total += 1
    return total


@dataclass(slots=True)
class HeuristicPushFoldBot:
    """Shoves or folds preflop by stack depth, position and Chen score.

    Thresholds (Chen score needed to shove) fall as the effective stack in big blinds
    falls and as fewer players are left to act. After the flop it checks or calls, which
    only happens when someone else's all-in was called.
    """

    shove_base: float = 7.0
    behind_step: float = 0.5
    call_margin: float = 2.0
    bounty_weight: float = 0.0
    """Chen points subtracted from the calling threshold per starting bounty at stake (PKO)."""
    info: TournamentHandInfo | None = field(default=None, repr=False)

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.info = info

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        if observation.street is not Street.PREFLOP:
            return Action.check() if legal.can_check else Action.call()
        me = observation.players[observation.observer]
        big_blind = self._big_blind(observation)
        stack_bb = (me.stack + me.street_contribution) / big_blind
        chen = chen_score(observation.hole_cards)
        raised = any(
            action.action_type is ActionType.RAISE_TO or action.contribution > big_blind
            for action in observation.action_history
        )
        if raised:
            threshold = (
                self._threshold(stack_bb, behind=0)
                + self.call_margin
                - self._bounty_discount(observation)
            )
            if chen >= threshold and legal.call_amount is not None:
                return Action.call()
            return Action.check() if legal.can_check else Action.fold()
        behind = _players_left_to_act(observation)
        if chen >= self._threshold(stack_bb, behind) and legal.max_raise_to is not None:
            return Action.raise_to(legal.max_raise_to)
        if legal.can_check:
            return Action.check()
        if legal.call_amount is not None and me.stack <= legal.call_amount:
            # Already committed (the call is all-in anyway): calling is free of decisions.
            return Action.call()
        return Action.fold()

    def _bounty_discount(self, observation: TableObservation) -> float:
        """How much looser to call an all-in when calling could win the shover's bounty.

        Only in PKO, only when this player covers an all-in opponent, and in proportion to
        the largest such bounty compared with the starting bounty. Zero for the plain bot.
        """
        info = self.info
        if self.bounty_weight <= 0 or info is None or not info.bounties:
            return 0.0
        me = observation.players[observation.observer]
        my_chips = me.stack + me.total_contribution
        best = 0.0
        for seat, player in enumerate(observation.players):
            if seat == observation.observer or not player.all_in or seat >= len(info.bounties):
                continue
            if my_chips >= player.total_contribution:
                best = max(best, info.bounties[seat])
        if best <= 0 or info.starting_bounty <= 0:
            return 0.0
        return self.bounty_weight * min(best / info.starting_bounty, 3.0)

    def _big_blind(self, observation: TableObservation) -> int:
        if self.info is not None:
            return self.info.big_blind
        blind = observation.players[observation.big_blind_seat]
        return max(blind.street_contribution, 1)

    def _threshold(self, stack_bb: float, behind: int) -> float:
        """Minimum Chen score to shove; lower with shorter stacks and fewer players behind."""
        depth = min(max(stack_bb, 1.0), 30.0)
        return self.shove_base + 0.35 * (depth - 10.0) + self.behind_step * (behind - 2)
