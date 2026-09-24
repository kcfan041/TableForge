from __future__ import annotations

import argparse

from tableforge.poker.soak import run_soak


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands", type=int, default=1_000_000)
    parser.add_argument("--seed-start", type=int, default=0)
    args = parser.parse_args()
    result = run_soak(args.hands, args.seed_start)
    print(
        f"hands={result.hands} actions={result.actions} "
        f"showdowns={result.showdowns} folds={result.folds} "
        f"elapsed={result.elapsed_seconds:.3f}s "
        f"hands_per_second={result.hands_per_second:.1f}"
    )


if __name__ == "__main__":
    main()
