"""Multi-player simulation, soak, and seat-rotated evaluation for ``TableHand``."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter

from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.poker.table import TableAgent, TableHand, TableRunner

RULE_BOTS: tuple[str, ...] = ("random", "call", "tight", "aggro")


def rule_bot(name: str, seed: int) -> TableAgent:
    if name == "random":
        return RandomBot(seed)
    if name == "call":
        return CallBot()
    if name == "tight":
        return TightBot()
    if name == "aggro":
        return AggroBot()
    raise ValueError(f"unknown rule bot: {name}")


def mixed_lineup(players: int, hand_index: int, seed: int) -> list[TableAgent]:
    """A deterministic, seed-derived mix of rule bots, one per seat."""
    return [
        rule_bot(
            RULE_BOTS[((seed * 2_654_435_761 + seat * 40_503) >> 7) % len(RULE_BOTS)],
            seed * 8 + seat,
        )
        for seat in range(players)
    ]


@dataclass(frozen=True, slots=True)
class TableSummary:
    players: int
    hands: int
    actions: int
    showdowns: int
    folds: int
    side_pots: int
    winnings: tuple[int, ...]
    elapsed_seconds: float

    @property
    def hands_per_second(self) -> float:
        return self.hands / self.elapsed_seconds if self.elapsed_seconds else float("inf")


def simulate_table(
    players: int,
    hands: int,
    seed_start: int,
    starting_stack: int = 100,
    small_blind: int = 1,
    big_blind: int = 2,
    lineup: Callable[[int, int, int], Sequence[TableAgent]] = mixed_lineup,
    on_progress: Callable[[int], None] | None = None,
    progress_interval: int = 0,
    varied_stacks: bool = False,
) -> TableSummary:
    """Plays ``hands`` independent hands; the button moves one seat every hand.

    With ``varied_stacks`` each seat starts with a seed-derived stack between one big
    blind and ``starting_stack``, so unequal all-ins and side pots occur regularly.
    """
    if hands < 0:
        raise ValueError("hands cannot be negative")
    runner = TableRunner()
    actions = showdowns = folds = side_pots = 0
    winnings = [0] * players
    started = perf_counter()
    for index in range(hands):
        seed = seed_start + index
        stacks = (
            _stacks(players, starting_stack, big_blind, seed)
            if varied_stacks
            else [starting_stack] * players
        )
        hand = TableHand(stacks, index % players, small_blind, big_blind, seed)
        result = runner.run(hand, lineup(players, index, seed))
        if sum(result.final_stacks) != sum(stacks):
            raise AssertionError(f"chip conservation failed at hand {index}")
        if sum(result.payouts) != hand.betting.pot:
            raise AssertionError(f"payout conservation failed at hand {index}")
        actions += len(hand.action_history)
        folds += int(result.ended_by_fold)
        showdowns += int(not result.ended_by_fold)
        side_pots += max(0, sum(1 for pot in result.pots if len(pot.eligible) > 1) - 1)
        for seat, stack in enumerate(result.final_stacks):
            winnings[seat] += stack - stacks[seat]
        completed = index + 1
        if on_progress is not None and progress_interval > 0 and completed % progress_interval == 0:
            on_progress(completed)
    if sum(winnings) != 0:
        raise AssertionError("aggregate winnings do not sum to zero")
    return TableSummary(
        players=players,
        hands=hands,
        actions=actions,
        showdowns=showdowns,
        folds=folds,
        side_pots=side_pots,
        winnings=tuple(winnings),
        elapsed_seconds=perf_counter() - started,
    )


def _stacks(players: int, maximum: int, big_blind: int, seed: int) -> list[int]:
    span = max(1, maximum - big_blind + 1)
    return [big_blind + (seed * 7_919 + seat * 104_729) % span for seat in range(players)]


@dataclass(frozen=True, slots=True)
class TableEvaluation:
    """Candidate result in big blinds per 100 hands with a 95% confidence interval.

    Every deal is replayed once per seat with the candidate in that seat and the button
    fixed, so each deal's cards are seen from every position (duplicate-style variance
    reduction). ``per_seat`` reports the same measure for each seat separately.
    """

    players: int
    deals: int
    hands: int
    bb_per_100: float
    confidence_low: float
    confidence_high: float
    per_seat: tuple[tuple[float, float, float], ...]

    @property
    def significantly_winning(self) -> bool:
        return self.confidence_low > 0


def _interval(values: Sequence[float]) -> tuple[float, float, float]:
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean * 100, -math.inf, math.inf
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    error = math.sqrt(variance / len(values))
    return mean * 100, (mean - 1.96 * error) * 100, (mean + 1.96 * error) * 100


def evaluate_table(
    candidate: Callable[[], TableAgent],
    opponents: Callable[[int, int], TableAgent],
    players: int,
    deals: int,
    seed_start: int,
    starting_stack: int = 100,
    small_blind: int = 1,
    big_blind: int = 2,
) -> TableEvaluation:
    """``opponents(seat, seed)`` builds the opponent for a seat; it is rebuilt per hand."""
    if deals < 2:
        raise ValueError("evaluation needs at least 2 deals")
    runner = TableRunner()
    per_deal: list[float] = []
    per_seat: list[list[float]] = [[] for _ in range(players)]
    for deal in range(deals):
        seed = seed_start + deal
        button = deal % players
        total = 0.0
        for seat in range(players):
            agents = [
                candidate() if index == seat else opponents(index, seed * 8 + index)
                for index in range(players)
            ]
            hand = TableHand([starting_stack] * players, button, small_blind, big_blind, seed)
            result = runner.run(hand, agents)
            won = (result.final_stacks[seat] - starting_stack) / big_blind
            per_seat[seat].append(won)
            total += won
        per_deal.append(total / players)
    mean, low, high = _interval(per_deal)
    return TableEvaluation(
        players=players,
        deals=deals,
        hands=deals * players,
        bb_per_100=mean,
        confidence_low=low,
        confidence_high=high,
        per_seat=tuple(_interval(values) for values in per_seat),
    )


def evaluate_against_rule_bots(
    candidate: Callable[[], TableAgent],
    players: int,
    deals: int,
    seed_start: int = 900_000_000,
) -> dict[str, TableEvaluation]:
    """Candidate against a full table of each rule bot in turn."""
    reports = {}
    for index, name in enumerate(RULE_BOTS):
        rng_base = seed_start + index * 10_000_000

        def opponent(seat: int, seed: int, name: str = name) -> TableAgent:
            return rule_bot(name, seed)

        reports[name] = evaluate_table(candidate, opponent, players, deals, rng_base)
    return reports
