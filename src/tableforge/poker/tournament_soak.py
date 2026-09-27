"""Tournament soak: play many complete tournaments and check every invariant.

uv run python -m tableforge.poker.tournament_soak --tournaments 100000 --workers 16
uv run python -m tableforge.poker.tournament_soak --tournaments 100000 --workers 16 --pko
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
from tableforge.poker.tournament import PAYOUTS, BountyConfig, preset_config, run_tournament

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
    knockouts: int = 0
    split_knockouts: int = 0

    def __add__(self, other: Totals) -> Totals:
        return Totals(
            self.tournaments + other.tournaments,
            self.hands + other.hands,
            max(self.max_hands, other.max_hands),
            self.simultaneous_busts + other.simultaneous_busts,
            self.ties + other.ties,
            self.knockouts + other.knockouts,
            self.split_knockouts + other.split_knockouts,
        )


_BOUNTIES = (BountyConfig(), BountyConfig(0.5, 1.0), BountyConfig(0.25, 0.0, False))


def _chunk(arguments: tuple[int, int, bool]) -> Totals:
    start, count, pko = arguments
    totals = Totals()
    for seed in range(start, start + count):
        bounty = _BOUNTIES[(seed // 6) % len(_BOUNTIES)] if pko else None
        config = preset_config(_STACKS[seed % 3], _PAYOUT_KEYS[(seed // 3) % 2], bounty=bounty)
        result = run_tournament(config, seed, _lineup(seed))
        if not math.isclose(sum(result.prizes), config.prize_pool):
            raise AssertionError(f"prize pool mismatch in tournament {seed}")
        # Money left on the table: only the winner's own bounty when they do not keep it.
        left = 0.0
        if bounty is not None and not bounty.winner_keeps_own:
            left = config.starting_bounty * config.players - sum(result.bounties)
            if left < -1e-9:
                raise AssertionError(f"more bounty paid than paid in, tournament {seed}")
        elif not math.isclose(
            sum(result.bounties), config.starting_bounty * config.players, abs_tol=1e-9
        ):
            raise AssertionError(f"bounties not conserved in tournament {seed}")
        if not math.isclose(sum(result.results), -left, abs_tol=1e-9):
            raise AssertionError(f"results do not balance in tournament {seed}")
        if bounty is not None:
            eliminated = {knockout.eliminated for knockout in result.knockouts}
            if eliminated != set(range(config.players)) - {result.places.index(1)}:
                raise AssertionError(f"a knockout is missing in tournament {seed}")
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
            len({knockout.eliminated for knockout in result.knockouts}),
            len(result.knockouts) - len({knockout.eliminated for knockout in result.knockouts}),
        )
    return totals


def run(
    tournaments: int, workers: int, seed_start: int = 0, pko: bool = False
) -> tuple[Totals, float]:
    size = max(1, -(-tournaments // (workers * 8)))
    chunks = [
        (start, min(size, seed_start + tournaments - start), pko)
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
    parser.add_argument("--pko", action="store_true", help="progressive knockout rules")
    args = parser.parse_args()
    totals, elapsed = run(args.tournaments, args.workers, args.seed_start, args.pko)
    print(
        json.dumps(
            {
                "tournaments": totals.tournaments,
                "hands": totals.hands,
                "average_hands": round(totals.hands / max(totals.tournaments, 1), 2),
                "max_hands": totals.max_hands,
                "simultaneous_busts": totals.simultaneous_busts,
                "tied_places": totals.ties,
                "knockouts": totals.knockouts,
                "split_knockouts": totals.split_knockouts,
                "elapsed_s": round(elapsed, 1),
                "tournaments_per_second": round(totals.tournaments / elapsed, 1),
            }
        )
    )


if __name__ == "__main__":
    main()
