"""Compare models and rule-bot baselines under one tournament evaluation protocol.

Everyone is evaluated with the same tournament config, opponent field, seeds and seat
rotation (`EvaluationProtocol`), so the ROIs are directly comparable.

    uv run python scripts/tournament_compare.py --tournament '{"starting_stack_bb": 25}' \\
        --models pko-25bb-s1 --baselines pushfold,hunter,tight,call,aggro,random --seeds 500

`--tournament` is a tournament spec object (see `tableforge.poker.tournament_spec`), or
`--config-from-model` uses the tournament a model was trained on. Models come from the
cluster's model library (`--library`, default: this computer's). Prints one JSON line per
player and a summary table.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tableforge.poker.tournament_benchmark import EvaluationProtocol, evaluate_with  # noqa: E402
from tableforge.poker.tournament_spec import TournamentSpec  # noqa: E402
from tableforge.worker.tournament import field_factory, rule_bot  # noqa: E402


def default_library() -> Path:
    from tableforge.web.cluster import Layout, default_home

    return Layout(default_home()).models


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tournament", default="{}", help="tournament spec as JSON")
    parser.add_argument("--config-from-model", default="", help="use this model's tournament")
    parser.add_argument("--models", default="", help="comma-separated model ids")
    parser.add_argument("--modes", default="average", help="average,best_response")
    parser.add_argument("--baselines", default="pushfold,hunter")
    parser.add_argument("--field", default="mixed")
    parser.add_argument("--seeds", type=int, default=500)
    parser.add_argument("--seed-start", type=int, default=1_500_000_000)
    parser.add_argument("--library", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None, help="write all results as JSON")
    args = parser.parse_args()

    library_root = args.library or default_library()
    if args.config_from_model:
        from tableforge.coordinator.analysis import tournament_json
        from tableforge.models import ModelLibrary

        config_json = tournament_json(ModelLibrary(library_root).info(args.config_from_model))
    else:
        config_json = TournamentSpec.from_dict(json.loads(args.tournament)).to_config().to_json()
    protocol = EvaluationProtocol(
        config_json, field=args.field, seeds=args.seeds, seed_start=args.seed_start
    )
    print(json.dumps({"protocol": protocol.describe()}), flush=True)

    candidates: list[tuple[str, object]] = []
    for name in [item for item in args.baselines.split(",") if item]:
        candidates.append((f"rule:{name}", lambda name=name: rule_bot(name, 0)))
    model_ids = [item for item in args.models.split(",") if item]
    if model_ids:
        import torch

        from tableforge.learning.nfsp import NfspAgent
        from tableforge.learning.tournament_training import (
            FrozenTournamentPlayer,
            GreedyTournamentPlayer,
        )
        from tableforge.models import ModelLibrary

        torch.set_num_threads(args.threads)
        library = ModelLibrary(library_root)
        for model_id in model_ids:
            bundle = library.checkpoint(model_id)
            for mode in [item for item in args.modes.split(",") if item]:
                if mode == "average":
                    frozen = NfspAgent.from_bundle(bundle, inference_only=True)
                    agent = FrozenTournamentPlayer(frozen)
                else:
                    agent = GreedyTournamentPlayer(NfspAgent.from_bundle(bundle))
                candidates.append((f"{model_id}:{mode}", lambda agent=agent: agent))

    results: dict[str, dict[str, object]] = {}
    for label, factory in candidates:
        started = time.perf_counter()
        summary = evaluate_with(protocol, factory, field_factory).summary()  # type: ignore[arg-type]
        results[label] = {
            key: summary[key]
            for key in ("roi", "prize_ev", "bounty_ev", "itm", "average_place", "average_hands")
        }
        results[label]["seconds"] = round(time.perf_counter() - started, 1)
        print(json.dumps({"player": label, **results[label]}), flush=True)

    print()
    print(f"{'player':<36} {'ROI':>8} {'95% CI':>18} {'bounty':>7} {'ITM':>6}")
    for label, item in results.items():
        roi = item["roi"]
        assert isinstance(roi, dict)
        mark = "*" if roi["low"] > 0 else ("-" if roi["high"] < 0 else " ")
        print(
            f"{label:<36} {roi['mean'] * 100:+7.1f}%{mark} "
            f"[{roi['low'] * 100:+6.1f},{roi['high'] * 100:+6.1f}] "
            f"{item['bounty_ev']['mean']:7.3f} {item['itm']['mean'] * 100:5.1f}%"  # type: ignore[index]
        )
    if args.out:
        args.out.write_text(
            json.dumps({"protocol": protocol.describe(), "results": results}, indent=1),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
