"""Measure NFSP throughput: self-play training and frozen-policy benchmark hands per second.

    uv run python scripts/nfsp_speed.py [--device cpu|cuda] [--hands 2000] [--deals 500]
"""

from __future__ import annotations

import argparse
import json
import time

import torch

from tableforge.learning.benchmark import run_benchmark
from tableforge.learning.nfsp import NfspAgent, NfspConfig
from tableforge.learning.training import self_play_train


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--hands", type=int, default=2_000)
    parser.add_argument("--deals", type=int, default=500)
    parser.add_argument("--hidden", type=int, default=128)
    args = parser.parse_args()

    agent = NfspAgent(NfspConfig(hidden_size=args.hidden), device=args.device)
    started = time.perf_counter()
    self_play_train(agent, args.hands, 0, train_every=4)
    training_seconds = time.perf_counter() - started
    snapshot = agent.to_bundle()

    def frozen() -> NfspAgent:
        return NfspAgent.from_bundle(snapshot, inference_only=True, device=args.device)

    started = time.perf_counter()
    run_benchmark(frozen, args.deals, opponents=("tight",))
    benchmark_seconds = time.perf_counter() - started
    benchmark_hands = args.deals * 2
    print(
        json.dumps(
            {
                "device": args.device,
                "hidden": args.hidden,
                "self_play_hands_per_second": round(args.hands / training_seconds, 1),
                "benchmark_hands_per_second": round(benchmark_hands / benchmark_seconds, 1),
                "full_benchmark_minutes_4x4000": round(
                    4 * 4_000 / (benchmark_hands / benchmark_seconds) / 60, 1
                ),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
