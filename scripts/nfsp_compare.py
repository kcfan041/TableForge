"""Run nfsp_probe.py for several schemas and seeds in parallel and print a comparison.

uv run python scripts/nfsp_compare.py --hands 20000 --deals 1000 --timeout 1500
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCHEMAS = ("poker-hunlhe-vector-v1", "poker-hunlhe-vector-v2")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands", type=int, default=20_000)
    parser.add_argument("--deals", type=int, default=1_000)
    parser.add_argument("--checkpoints", type=int, default=2)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--timeout", type=float, default=1_500)
    args = parser.parse_args()
    script = Path(__file__).with_name("nfsp_probe.py")
    runs: dict[tuple[str, int], subprocess.Popen[str]] = {}
    for schema in SCHEMAS:
        for seed in args.seeds:
            runs[(schema, seed)] = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    str(script),
                    "--hands",
                    str(args.hands),
                    "--checkpoints",
                    str(args.checkpoints),
                    "--deals",
                    str(args.deals),
                    "--device",
                    "cpu",
                    "--seed",
                    str(seed),
                    "--schema",
                    schema,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
    deadline = time.monotonic() + args.timeout
    results: dict[tuple[str, int], list[dict[str, object]]] = {}
    for key, process in runs.items():
        remaining = max(1.0, deadline - time.monotonic())
        try:
            output, _ = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            for other in runs.values():
                other.kill()
            print(json.dumps({"error": "timeout", "run": list(key)}), flush=True)
            return 2
        results[key] = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    for (schema, seed), lines in results.items():
        for line in lines:
            benchmark = line["benchmark"]
            assert isinstance(benchmark, dict)
            print(
                json.dumps(
                    {
                        "schema": schema.rsplit("-", 1)[-1],
                        "seed": seed,
                        "hands": line["hands"],
                        "elapsed_s": line["elapsed_s"],
                        **{name: _cell(values) for name, values in benchmark.items()},
                    }
                ),
                flush=True,
            )
    return 0


def _cell(values: dict[str, float]) -> str:
    return f"{values['bb_per_100']:+.0f} [{values['ci_low']:+.0f},{values['ci_high']:+.0f}]"


if __name__ == "__main__":
    sys.exit(main())
