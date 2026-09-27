"""Tournament soak: play many complete tournaments and check every invariant.

uv run python -m tableforge.poker.tournament_soak --tournaments 100000 --workers 16
"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from tableforge.agents.rule import AggroBot, CallBot, HeuristicPushFoldBot, RandomBot, TightBot
from tableforge.poker.table import TableAgent
from tableforge.poker.tournament import PAYOUTS, preset_config, run_tournament

_STACKS = (10, 25, 50)
_PAYOUT_KEYS = ("65-35", "50-30-20")


def _lineup(seed: int) -> list[TableAgent]:
    factories = (TightBot, CallBot, AggroBot, HeuristicPushFoldBot)
    agents: list[TableAgent] = []
    for player in range(6):
        choice = (seed * 7 + player * 3) % 5
        agents.append(RandomBot(seed * 10 + player) if choice == 4 else factories[choice]())
    return agents


@dataclass(frozen=True, slots=True)
class Totals:
    tournaments: int = 0
    hands: int = 0
    max_hands: int = 0
    simultaneous_busts: int = 0
    ties: int = 0

    def __add__(self, other: Totals) -> Totals:
        return Totals(
            self.tournaments + other.tournaments,
            self.hands + other.hands,
            max(self.max_hands, other.max_hands),
            self.simultaneous_busts + other.simultaneous_busts,
            self.ties + other.ties,
        )


def _chunk(arguments: tuple[int, int]) -> Totals:
    start, count = arguments
    totals = Totals()
    for seed in range(start, start + count):
        config = preset_config(_STACKS[seed % 3], _PAYOUT_KEYS[(seed // 3) % 2])
        result = run_tournament(config, seed, _lineup(seed))
        if not math.isclose(sum(result.prizes), config.prize_pool):
            raise AssertionError(f"prize pool mismatch in tournament {seed}")
        if not math.isclose(sum(result.results), 0.0, abs_tol=1e-9):
            raise AssertionError(f"results do not sum to zero in tournament {seed}")
        if result.places.count(1) != 1:
            raise AssertionError(f"not exactly one winner in tournament {seed}")
        order = sorted(player for group in result.elimination_order for player in group)
        if order != list(range(config.players)):
            raise AssertionError(f"elimination order incomplete in tournament {seed}")
        paid = [share for share in PAYOUTS[_PAYOUT_KEYS[(seed // 3) % 2]] if share > 0]
        if len(paid) > config.players:
            raise AssertionError("payout longer than the field")
        totals = totals + Totals(
            1,
            result.hands,
            result.hands,
            sum(1 for group in result.elimination_order if len(group) > 1),
            len(result.places) - len(set(result.places)),
        )
    return totals


def run(tournaments: int, workers: int, seed_start: int = 0) -> tuple[Totals, float]:
    size = max(1, -(-tournaments // (workers * 8)))
    chunks = [
        (start, min(size, seed_start + tournaments - start))
        for start in range(seed_start, seed_start + tournaments, size)
    ]
    started = time.perf_counter()
    total = Totals()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(_chunk, chunks):
            total = total + part
    return total, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tournaments", type=int, default=100_000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--seed-start", type=int, default=0)
    args = parser.parse_args()
    totals, elapsed = run(args.tournaments, args.workers, args.seed_start)
    print(
        json.dumps(
            {
                "tournaments": totals.tournaments,
                "hands": totals.hands,
                "average_hands": round(totals.hands / max(totals.tournaments, 1), 2),
                "max_hands": totals.max_hands,
                "simultaneous_busts": totals.simultaneous_busts,
                "tied_places": totals.ties,
                "elapsed_s": round(elapsed, 1),
                "tournaments_per_second": round(totals.tournaments / elapsed, 1),
            }
        )
    )


if __name__ == "__main__":
    main()
