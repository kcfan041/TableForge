from __future__ import annotations

import argparse

from tableforge.poker.soak import run_soak
from tableforge.poker.table_play import simulate_table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands", type=int, default=1_000_000)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument(
        "--players",
        type=int,
        default=0,
        help="2-6 runs the multi-player engine; omit for the heads-up engine",
    )
    args = parser.parse_args()
    if args.players:
        summary = simulate_table(args.players, args.hands, args.seed_start, varied_stacks=True)
        print(
            f"players={summary.players} hands={summary.hands} actions={summary.actions} "
            f"showdowns={summary.showdowns} folds={summary.folds} "
            f"side_pots={summary.side_pots} "
            f"elapsed={summary.elapsed_seconds:.3f}s "
            f"hands_per_second={summary.hands_per_second:.1f}"
        )
        return
    result = run_soak(args.hands, args.seed_start)
    print(
        f"hands={result.hands} actions={result.actions} "
        f"showdowns={result.showdowns} folds={result.folds} "
        f"elapsed={result.elapsed_seconds:.3f}s "
        f"hands_per_second={result.hands_per_second:.1f}"
    )


if __name__ == "__main__":
    main()
