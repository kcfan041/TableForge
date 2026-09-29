"""Explaining decisions: one format for every algorithm.

Replays and strategy tables ask a model "what would you do here, and why?" through
`DecisionExplainer`, never through algorithm-specific code, so adding an algorithm only
means implementing this interface.

* `probabilities` (required): the chance of each canonical action. Every algorithm can
  report this: a stochastic policy gives its distribution, a greedy one puts 1.0 on its
  choice, a rule bot puts 1.0 on its action.
* `details` (optional): algorithm-specific numbers, each with a name, a unit and a value
  per action or a single value; the UI shows them in a generic table.
* `play_modes`: ways the model can play (for NFSP: average policy or best response). The UI
  lists them; the first is the default.

Canonical actions are the same for every algorithm: fold, check/call, raise, all-in. A
model with several raise sizes reports their total under "raise" and may add the split in
`raise_sizes`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from tableforge.poker.betting import Action, ActionType, LegalActions
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo

EXPLANATION_SCHEMA = "tableforge.decision-explanation.v1"
ACTIONS = ("fold", "check_call", "raise", "all_in")


@dataclass(frozen=True, slots=True)
class PlayMode:
    key: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Detail:
    """An algorithm-specific number (per action, or one value for the whole state)."""

    name: str
    unit: str
    per_action: dict[str, float] = field(default_factory=dict)
    value: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "unit": self.unit,
            "per_action": {key: round(item, 6) for key, item in self.per_action.items()},
            "value": None if self.value is None else round(self.value, 6),
        }


@dataclass(frozen=True, slots=True)
class DecisionExplanation:
    probabilities: dict[str, float]
    details: tuple[Detail, ...] = ()
    raise_sizes: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.probabilities) - set(ACTIONS)
        if unknown:
            raise ValueError(f"unknown canonical actions: {sorted(unknown)}")
        total = sum(self.probabilities.values())
        if not math.isclose(total, 1.0, abs_tol=1e-4):
            raise ValueError(f"probabilities must add up to 1, got {total}")

    def as_dict(self) -> dict[str, object]:
        return {
            "probabilities": {
                name: round(self.probabilities.get(name, 0.0), 6) for name in ACTIONS
            },
            "details": [detail.as_dict() for detail in self.details],
            "raise_sizes": {key: round(value, 6) for key, value in self.raise_sizes.items()},
        }


class DecisionExplainer(Protocol):
    """A model that can say what it would do in a table state.

    `begin_hand` receives the tournament state of the hand (blinds, bounties, payout) just
    like a playing agent. `explain` must not change the model and may be called for any
    state, including states that did not arise in play (strategy tables).
    """

    @property
    def play_modes(self) -> tuple[PlayMode, ...]: ...

    def begin_hand(self, info: TournamentHandInfo) -> None: ...

    def explain(
        self, observation: TableObservation, legal: LegalActions, mode: str
    ) -> DecisionExplanation: ...

    def act(self, observation: TableObservation, legal: LegalActions, mode: str) -> Action: ...


def canonical_action(action: Action, legal: LegalActions) -> str:
    """Canonical name of a concrete action."""
    if action.type is ActionType.FOLD:
        return "fold"
    if action.type in (ActionType.CHECK, ActionType.CALL):
        return "check_call"
    if legal.max_raise_to is not None and action.amount == legal.max_raise_to:
        return "all_in"
    return "raise"


def one_hot(name: str) -> dict[str, float]:
    return {action: 1.0 if action == name else 0.0 for action in ACTIONS}


class RuleBotExplainer:
    """A rule bot: whatever it does has probability 1 (sampled once for random bots)."""

    def __init__(self, bot: object) -> None:
        self.bot = bot

    @property
    def play_modes(self) -> tuple[PlayMode, ...]:
        return (PlayMode("rule", "Rule"),)

    def begin_hand(self, info: TournamentHandInfo) -> None:
        begin = getattr(self.bot, "begin_hand", None)
        if callable(begin):
            begin(info)

    def act(self, observation: TableObservation, legal: LegalActions, mode: str) -> Action:
        del mode
        return self.bot.act(observation, legal)  # type: ignore[attr-defined,no-any-return]

    def explain(
        self, observation: TableObservation, legal: LegalActions, mode: str
    ) -> DecisionExplanation:
        return DecisionExplanation(
            one_hot(canonical_action(self.act(observation, legal, mode), legal))
        )


def normalize(values: Sequence[float]) -> list[float]:
    total = sum(values)
    return [value / total for value in values] if total > 0 else [0.0 for _ in values]
