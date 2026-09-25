from __future__ import annotations

import argparse
import json
import sys
import time

import torch

from tableforge.learning.benchmark import run_benchmark
from tableforge.learning.nfsp import NfspAgent, NfspConfig
from tableforge.learning.training import self_play_train


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands", type=int, default=20_000)
    parser.add_argument("--checkpoints", type=int, default=4)
    parser.add_argument("--deals", type=int, default=2_000)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--train-every", type=int, default=4)
    parser.add_argument("--anticipatory", type=float, default=0.1)
    parser.add_argument("--epsilon", type=float, default=0.06)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    agent = NfspAgent(
        NfspConfig(
            hidden_size=args.hidden,
            batch_size=args.batch,
            learning_rate=args.lr,
            anticipatory=args.anticipatory,
            epsilon=args.epsilon,
            seed=args.seed,
        ),
        device=args.device,
    )
    per_stage = args.hands // args.checkpoints
    trained = 0
    started = time.perf_counter()
    for stage in range(args.checkpoints + 1):
        if stage:
            report = self_play_train(agent, per_stage, trained, args.train_every)
            trained += per_stage
            losses = report.last_metrics
        else:
            losses = {}
        snapshot = agent.to_bundle()
        benchmark = run_benchmark(
            lambda: NfspAgent.from_bundle(snapshot, inference_only=True), args.deals
        )
        print(
            json.dumps(
                {
                    "hands": trained,
                    "elapsed_s": round(time.perf_counter() - started),
                    "train_steps": agent.train_steps,
                    "rl": len(agent.rl_buffer),
                    "sl": len(agent.sl_buffer),
                    "losses": {key: round(value, 4) for key, value in losses.items()},
                    "benchmark": benchmark.summary(),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
