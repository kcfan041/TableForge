from __future__ import annotations

import random
from dataclasses import dataclass, field

from tableforge.poker.betting import Action, LegalActions, Street
from tableforge.poker.observation import Observation


@dataclass(slots=True)
class RandomBot:
    seed: int | str | bytes
    _random: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._random = random.Random(self.seed)

    def act(self, observation: Observation, legal_actions: LegalActions) -> Action:
        actions = _legal_action_candidates(legal_actions)
        return self._random.choice(actions)


@dataclass(frozen=True, slots=True)
class CallBot:
    def act(self, observation: Observation, legal_actions: LegalActions) -> Action:
        if legal_actions.can_check:
            return Action.check()
        if legal_actions.call_amount is not None:
            return Action.call()
        return Action.fold()


@dataclass(frozen=True, slots=True)
class TightBot:
    def act(self, observation: Observation, legal_actions: LegalActions) -> Action:
        strength = _starting_hand_strength(observation)
        if legal_actions.can_check:
            return Action.check()
        if strength >= 2 and legal_actions.min_raise_to is not None:
            return Action.raise_to(legal_actions.min_raise_to)
        if strength >= 1 and legal_actions.call_amount is not None:
            return Action.call()
        return Action.fold()


@dataclass(frozen=True, slots=True)
class AggroBot:
    def act(self, observation: Observation, legal_actions: LegalActions) -> Action:
        can_aggress = (
            observation.street is Street.PREFLOP or observation.pot % 3 != 0
        )
        if legal_actions.min_raise_to is not None and can_aggress:
            return Action.raise_to(legal_actions.min_raise_to)
        if legal_actions.call_amount is not None:
            return Action.call()
        return Action.check()


def _legal_action_candidates(legal: LegalActions) -> list[Action]:
    actions: list[Action] = []
    if legal.can_fold:
        actions.append(Action.fold())
    if legal.can_check:
        actions.append(Action.check())
    if legal.call_amount is not None:
        actions.append(Action.call())
    if legal.min_raise_to is not None:
        actions.append(Action.raise_to(legal.min_raise_to))
        if legal.max_raise_to != legal.min_raise_to:
            if legal.max_raise_to is None:
                raise AssertionError("max_raise_to required when raising is legal")
            actions.append(Action.raise_to(legal.max_raise_to))
    if not actions:
        raise AssertionError("current actor must have a legal action")
    return actions


def _starting_hand_strength(observation: Observation) -> int:
    first, second = observation.hole_cards
    if observation.street is not Street.PREFLOP:
        return 1
    if first.rank == second.rank:
        return 2 if int(first.rank) >= 10 else 1
    high = max(int(first.rank), int(second.rank))
    low = min(int(first.rank), int(second.rank))
    suited = first.suit == second.suit
    if high >= 13 and low >= 10:
        return 2
    if high >= 11 and (suited or high - low <= 2):
        return 1
    return 0
