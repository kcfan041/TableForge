from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from tableforge.poker.betting import Action, LegalActions


class AbstractAction(IntEnum):
    FOLD = 0
    CHECK_CALL = 1
    RAISE_HALF_POT = 2
    RAISE_POT = 3
    ALL_IN = 4


ACTION_COUNT = len(AbstractAction)
SCHEMA = "nlhe-discrete-v1"


@dataclass(frozen=True, slots=True)
class ActionTranslator:
    def legal_mask(self, legal: LegalActions) -> tuple[bool, ...]:
        mask = [False] * ACTION_COUNT
        mask[AbstractAction.FOLD] = legal.can_fold
        mask[AbstractAction.CHECK_CALL] = legal.can_check or legal.call_amount is not None
        if legal.min_raise_to is not None and legal.max_raise_to is not None:
            mask[AbstractAction.ALL_IN] = True
            if not legal.all_in_only:
                mask[AbstractAction.RAISE_HALF_POT] = True
                mask[AbstractAction.RAISE_POT] = True
        return tuple(mask)

    def to_action(
        self,
        abstract: AbstractAction,
        legal: LegalActions,
        pot: int,
        current_bet: int,
        own_contribution: int,
    ) -> Action:
        if not self.legal_mask(legal)[abstract]:
            raise ValueError(f"abstract action {abstract.name} is not legal")
        if abstract is AbstractAction.FOLD:
            return Action.fold()
        if abstract is AbstractAction.CHECK_CALL:
            return Action.check() if legal.can_check else Action.call()
        assert legal.min_raise_to is not None and legal.max_raise_to is not None
        if abstract is AbstractAction.ALL_IN:
            return Action.raise_to(legal.max_raise_to)
        to_call = current_bet - own_contribution
        pot_after_call = pot + to_call
        fraction = 0.5 if abstract is AbstractAction.RAISE_HALF_POT else 1.0
        target = current_bet + int(pot_after_call * fraction)
        return Action.raise_to(min(max(target, legal.min_raise_to), legal.max_raise_to))
