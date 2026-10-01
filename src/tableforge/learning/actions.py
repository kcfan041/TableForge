"""Abstract actions: the discrete choices a learned agent picks from.

An action set maps a few abstract actions to concrete no-limit actions. Checkpoints and
experience record which set they use (``action_schema``), so models trained with one set
keep working when another is added.

* ``nlhe-discrete-v1`` (5 actions): fold, check/call, raise half pot, raise pot, all in.
  Raises are sized from the pot on every street.
* ``nlhe-discrete-v2`` (10 actions, S2): fold, check/call, all in, three preflop sizes
  measured in big blinds over the current bet (open 2x, 2.5x, 3x the big blind; facing a
  raise the same multiples of the raise, i.e. a 3-bet to 2x/2.5x/3x), and four postflop
  sizes as a fraction of the pot after calling (33%, 66%, 100%, 150%). Preflop sizes are
  only legal preflop and postflop sizes only after the flop, so the network never has to
  learn that half of its outputs mean nothing on a street.

Every size is clamped to the legal raise range; a size that would equal the all-in is still
a raise of that size (the engine treats it as all in).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from tableforge.poker.betting import Action, LegalActions, Street


class AbstractAction(IntEnum):
    FOLD = 0
    CHECK_CALL = 1
    RAISE_HALF_POT = 2
    RAISE_POT = 3
    ALL_IN = 4


ACTION_COUNT = len(AbstractAction)
SCHEMA = "nlhe-discrete-v1"
SCHEMA_V2 = "nlhe-discrete-v2"
MAX_ACTIONS = 10
"""The largest action set (buffers and shards are sized per set; this bounds them)."""


@dataclass(frozen=True, slots=True)
class ActionTranslator:
    """The v1 action set (kept as is: models and tests use it directly)."""

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


@dataclass(frozen=True, slots=True)
class Spot:
    """What sizing needs to know about a decision."""

    street: Street
    pot: int
    current_bet: int
    own_contribution: int
    big_blind: int


@dataclass(frozen=True, slots=True)
class Size:
    name: str
    """Label in explanations (``2x``, ``pot_33``...)."""
    preflop: bool
    """True: legal only preflop; False: only after the flop."""
    target: Callable[[Spot], int]
    """Raise-to amount before clamping to the legal range."""


def _preflop(multiple: float) -> Callable[[Spot], int]:
    def target(spot: Spot) -> int:
        # Unopened (the current bet is the big blind): open to multiple x BB. Facing a
        # raise: raise to multiple x the current bet.
        return round(max(spot.current_bet, spot.big_blind) * multiple)

    return target


def _postflop(fraction: float) -> Callable[[Spot], int]:
    def target(spot: Spot) -> int:
        pot_after_call = spot.pot + spot.current_bet - spot.own_contribution
        return spot.current_bet + int(pot_after_call * fraction)

    return target


@dataclass(frozen=True, slots=True)
class ActionSet:
    """Fold, check/call, all in, and named raise sizes (see the module docstring)."""

    schema: str
    sizes: tuple[Size, ...]
    canonical: tuple[str, ...] = field(init=False)

    FOLD = 0
    CHECK_CALL = 1
    ALL_IN = 2

    def __post_init__(self) -> None:
        names = ["fold", "check_call", "all_in", *("raise" for _ in self.sizes)]
        object.__setattr__(self, "canonical", tuple(names))

    @property
    def count(self) -> int:
        return 3 + len(self.sizes)

    def size_names(self) -> tuple[str, ...]:
        return tuple(size.name for size in self.sizes)

    def legal_mask(self, legal: LegalActions, street: Street) -> tuple[bool, ...]:
        mask = [False] * self.count
        mask[self.FOLD] = legal.can_fold
        mask[self.CHECK_CALL] = legal.can_check or legal.call_amount is not None
        if legal.min_raise_to is not None and legal.max_raise_to is not None:
            mask[self.ALL_IN] = True
            if not legal.all_in_only:
                preflop = street is Street.PREFLOP
                for index, size in enumerate(self.sizes):
                    mask[3 + index] = size.preflop == preflop
        return tuple(mask)

    def to_action(self, index: int, legal: LegalActions, spot: Spot) -> Action:
        if not self.legal_mask(legal, spot.street)[index]:
            raise ValueError(f"abstract action {index} is not legal")
        if index == self.FOLD:
            return Action.fold()
        if index == self.CHECK_CALL:
            return Action.check() if legal.can_check else Action.call()
        assert legal.min_raise_to is not None and legal.max_raise_to is not None
        if index == self.ALL_IN:
            return Action.raise_to(legal.max_raise_to)
        target = self.sizes[index - 3].target(spot)
        return Action.raise_to(min(max(target, legal.min_raise_to), legal.max_raise_to))


V2 = ActionSet(
    SCHEMA_V2,
    (
        Size("2x", True, _preflop(2.0)),
        Size("2.5x", True, _preflop(2.5)),
        Size("3x", True, _preflop(3.0)),
        Size("pot_33", False, _postflop(0.33)),
        Size("pot_66", False, _postflop(0.66)),
        Size("pot_100", False, _postflop(1.0)),
        Size("pot_150", False, _postflop(1.5)),
    ),
)

ACTION_SCHEMAS = (SCHEMA, SCHEMA_V2)


class Actions:
    """One interface over both action sets, for agents and explainers.

    `canonical[i]` is the canonical action (fold, check_call, raise, all_in) of abstract
    action i and `size_label[i]` its raise size (empty for the others).
    """

    def __init__(self, schema: str) -> None:
        if schema not in ACTION_SCHEMAS:
            raise ValueError(f"unsupported action schema: {schema}")
        self.schema = schema
        if schema == SCHEMA:
            self.count = ACTION_COUNT
            self.canonical: tuple[str, ...] = ("fold", "check_call", "raise", "raise", "all_in")
            self.size_label: tuple[str, ...] = ("", "", "half_pot", "pot", "")
            self._v1 = ActionTranslator()
        else:
            self.count = V2.count
            self.canonical = V2.canonical
            self.size_label = ("", "", "", *V2.size_names())

    def legal_mask(self, legal: LegalActions, street: Street) -> tuple[bool, ...]:
        if self.schema == SCHEMA:
            return self._v1.legal_mask(legal)
        return V2.legal_mask(legal, street)

    def to_action(self, index: int, legal: LegalActions, spot: Spot) -> Action:
        if self.schema == SCHEMA:
            return self._v1.to_action(
                AbstractAction(index), legal, spot.pot, spot.current_bet, spot.own_contribution
            )
        return V2.to_action(index, legal, spot)


def action_count(schema: str) -> int:
    return Actions(schema).count


def raise_size_amounts(legal: LegalActions, spot: Spot) -> dict[str, int]:
    """Raise-to amount of every named raise size of every action set at this spot.

    Keys are the size labels used in explanations (``half_pot``, ``pot``, ``2x``,
    ``pot_33``...); sizes not legal on this street are left out. Used to match a person's
    raise to the coach's nearest size.
    """
    if legal.min_raise_to is None or legal.max_raise_to is None:
        return {}
    low, high = legal.min_raise_to, legal.max_raise_to
    pot_after_call = spot.pot + spot.current_bet - spot.own_contribution
    amounts = {
        "half_pot": spot.current_bet + int(pot_after_call * 0.5),
        "pot": spot.current_bet + int(pot_after_call),
    }
    preflop = spot.street is Street.PREFLOP
    for size in V2.sizes:
        if size.preflop == preflop:
            amounts[size.name] = size.target(spot)
    return {name: min(max(value, low), high) for name, value in amounts.items()}
