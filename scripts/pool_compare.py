"""Evaluate candidates against the pool of trained models (C).

For each candidate (a model from the library, or a rule bot) this reports:

* ROI against the model pool (the other models trained for the same tournament, the
  candidate itself left out), with the pool models rotating through the other seats;
* optionally one matchup per pool member (all other seats that member);
* optionally the paired ROI difference against a reference model on the mixed rule-bot
  benchmark (same tournaments), to check that nothing regressed there.

    uv run python scripts/pool_compare.py --config-from-model pko-10bb-platform \\
        --models pko-10bb-platform,pko-10bb-nfsp-s11 --rules tight,hunter \\
        --seeds 500 --matchup-seeds 150 --reference pko-10bb-platform --out pool.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config-from-model", required=True)
    parser.add_argument("--models", default="", help="comma-separated model ids (candidates)")
    parser.add_argument("--checkpoints", default="", help="comma-separated checkpoint files")
    parser.add_argument("--rules", default="", help="comma-separated rule bots (candidates)")
    parser.add_argument("--mode", default="average", choices=("average", "best_response"))
    parser.add_argument("--seeds", type=int, default=500)
    parser.add_argument("--matchup-seeds", type=int, default=0)
    parser.add_argument("--seed-start", type=int, default=1_700_000_000)
    parser.add_argument("--reference", default="", help="model for the paired rule-bot check")
    parser.add_argument("--rule-seeds", type=int, default=1000)
    parser.add_argument("--library", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    import torch

    from tableforge.coordinator.analysis import tournament_json
    from tableforge.learning.nfsp import NfspAgent
    from tableforge.learning.pool_benchmark import library_pool, paired_difference, pool_benchmark
    from tableforge.learning.tournament_training import (
        FrozenTournamentPlayer,
        GreedyTournamentPlayer,
    )
    from tableforge.models import ModelLibrary
    from tableforge.poker.tournament import TournamentConfig
    from tableforge.poker.tournament_benchmark import collect_samples
    from tableforge.web.cluster import Layout, default_home
    from tableforge.worker.tournament import field_factory, rule_bot

    torch.set_num_threads(1)
    library = ModelLibrary(args.library or Layout(default_home()).models)
    config = TournamentConfig.from_json(tournament_json(library.info(args.config_from_model)))

    def player(bundle: bytes) -> object:
        if args.mode == "best_response":
            return GreedyTournamentPlayer(NfspAgent.from_actor_bundle(bundle))
        return FrozenTournamentPlayer(NfspAgent.from_bundle(bundle, inference_only=True))

    candidates: list[tuple[str, object, str]] = []
    for model_id in [item for item in args.models.split(",") if item]:
        candidates.append((model_id, player(library.checkpoint(model_id)), model_id))
    for path in [item for item in args.checkpoints.split(",") if item]:
        candidates.append((Path(path).stem, player(Path(path).read_bytes()), ""))
    for name in [item for item in args.rules.split(",") if item]:
        candidates.append((f"rule:{name}", rule_bot(name, 0), ""))

    reference_samples = None
    if args.reference:
        reference = player(library.checkpoint(args.reference))
        reference_samples = collect_samples(
            lambda: reference, field_factory("mixed"), config, args.rule_seeds, 1_500_000_000
        )

    results: dict[str, object] = {}
    for label, agent, exclude in candidates:
        started = time.perf_counter()
        members = library_pool(library, config, exclude=(exclude,) if exclude else ())
        result = pool_benchmark(
            lambda agent=agent: agent,  # type: ignore[misc]
            members,
            config,
            args.seeds,
            args.seed_start,
            args.matchup_seeds,
        )
        if reference_samples is not None:
            samples = collect_samples(
                lambda agent=agent: agent,  # type: ignore[misc]
                field_factory("mixed"),
                config,
                args.rule_seeds,
                1_500_000_000,
            )
            difference = paired_difference(samples, reference_samples)
            result["rule_bots_vs_reference"] = {
                "reference": args.reference,
                "roi_difference": difference.as_dict(),
            }
        result["seconds"] = round(time.perf_counter() - started)
        results[label] = result
        print(json.dumps({"candidate": label, **result}), flush=True)

    print()
    for label, result in results.items():
        overall = result["overall"]["roi"]  # type: ignore[index]
        mark = "*" if overall["low"] > 0 else ("-" if overall["high"] < 0 else " ")
        line = (
            f"{label:<28} pool ROI {overall['mean'] * 100:+6.1f}%{mark} "
            f"[{overall['low'] * 100:+6.1f},{overall['high'] * 100:+6.1f}] "
            f"n={len(result['members'])}"  # type: ignore[arg-type]
        )
        check = result.get("rule_bots_vs_reference")  # type: ignore[union-attr]
        if check:
            diff = check["roi_difference"]
            line += (
                f"  vs {check['reference']} on rule bots {diff['mean'] * 100:+5.1f} "
                f"[{diff['low'] * 100:+5.1f},{diff['high'] * 100:+5.1f}]"
            )
        print(line)
        for name, matchup in result["matchups"].items():  # type: ignore[union-attr]
            roi = matchup["roi"]
            print(
                f"    vs {name:<32} {roi['mean'] * 100:+6.1f}% "
                f"[{roi['low'] * 100:+6.1f},{roi['high'] * 100:+6.1f}]"
            )
    if args.out:
        args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
