from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from tableforge.agents.protocol import PokerAgent
from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.poker.betting import Seat
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.runner import HandRunner


@dataclass(frozen=True, slots=True)
class SoakResult:
    hands: int
    actions: int
    showdowns: int
    folds: int
    elapsed_seconds: float

    @property
    def hands_per_second(self) -> float:
        return self.hands / self.elapsed_seconds if self.elapsed_seconds else float("inf")


def run_soak(hands: int, seed_start: int = 0) -> SoakResult:
    if hands < 0:
        raise ValueError("hands cannot be negative")
    runner = HandRunner()
    actions = 0
    showdowns = 0
    folds = 0
    started = perf_counter()
    for index in range(hands):
        seed = seed_start + index
        agents = _agents_for_hand(index, seed)
        hand = HeadsUpHand((100, 100), Seat(index % 2), 1, 2, seed=seed)
        result = runner.run(hand, agents)
        if sum(result.final_stacks) != 200:
            raise AssertionError(f"chip conservation failed at hand {index}")
        if sum(result.payouts) != hand.betting.pot:
            raise AssertionError(f"payout conservation failed at hand {index}")
        actions += len(hand.action_history)
        if result.ended_by_fold:
            folds += 1
        else:
            showdowns += 1
    return SoakResult(
        hands=hands,
        actions=actions,
        showdowns=showdowns,
        folds=folds,
        elapsed_seconds=perf_counter() - started,
    )


def _agents_for_hand(index: int, seed: int) -> tuple[PokerAgent, PokerAgent]:
    matchup = index % 4
    if matchup == 0:
        return CallBot(), CallBot()
    if matchup == 1:
        return TightBot(), AggroBot()
    if matchup == 2:
        return AggroBot(), CallBot()
    return RandomBot(seed * 2 + 1), RandomBot(seed * 2 + 2)
