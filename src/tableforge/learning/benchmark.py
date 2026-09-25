from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.learning.executors import opponent_factory
from tableforge.learning.training import EvaluationReport, evaluate

BENCHMARK_OPPONENTS: tuple[str, ...] = ("random", "call", "tight", "aggro")
BENCHMARK_SEED = 900_000_000


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    opponent: str
    report: EvaluationReport

    @property
    def significantly_winning(self) -> bool:
        return self.report.confidence_low > 0

    @property
    def significantly_losing(self) -> bool:
        return self.report.confidence_high < 0


@dataclass(frozen=True, slots=True)
class Benchmark:
    """A fixed, reproducible measurement of an agent against every rule bot.

    Each deal is played twice with seats swapped (duplicate poker), and the deal seeds
    come from a range reserved for benchmarking so they never overlap training seeds.
    """

    results: tuple[BenchmarkResult, ...]

    @property
    def beats_all(self) -> bool:
        return all(result.significantly_winning for result in self.results)

    def by_opponent(self) -> dict[str, BenchmarkResult]:
        return {result.opponent: result for result in self.results}

    def summary(self) -> dict[str, dict[str, float]]:
        return {
            result.opponent: {
                "bb_per_100": round(result.report.bb_per_100, 1),
                "ci_low": round(result.report.confidence_low, 1),
                "ci_high": round(result.report.confidence_high, 1),
                "hands": result.report.hands,
            }
            for result in self.results
        }


def run_benchmark(
    agent_factory: Callable[[], PokerAgent],
    deals: int,
    opponents: tuple[str, ...] = BENCHMARK_OPPONENTS,
    seed_start: int = BENCHMARK_SEED,
) -> Benchmark:
    if deals < 2:
        raise ValueError("benchmark needs at least 2 deals per opponent")
    results = []
    for index, name in enumerate(opponents):
        seed = seed_start + index * 10_000_000
        report = evaluate(agent_factory(), opponent_factory(name, seed), deals, seed)
        results.append(BenchmarkResult(name, report))
    return Benchmark(tuple(results))
