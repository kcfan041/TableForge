"""Head-to-head matrix of checkpoint files (and rule bots), one process per row.

Every competitor sits in one seat against a field made only of each other competitor, over
the same seeds with the seat rotated (`learning.comparison`, the protocol of the Compare
page and P2). Rows run in parallel processes; the merged result is the matrix and each
competitor's average ROI over its opponents.

    uv run python scripts/model_matrix.py run out.json --tournament '{"starting_stack_bb":25}' \\
        --model M1=path/M-s1.zip --model S1=path/S-s1.zip --rule pushfold --seeds 200 --workers 8
    uv run python scripts/model_matrix.py field out.json --tournament '{"starting_stack_bb":10}' \\
        --model M1=path/M-s1.zip --library-model pko-10bb-nfsp-c1 --seeds 300

`field` benchmarks each competitor against the mixed rule-bot field instead (T3 protocol).
Model files are NFSP checkpoints unless given as NAME=ppo:PATH.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SEED_START = 1_700_000_000


def _members(models: list[str], library_models: list[str], rules: list[str]) -> list:  # type: ignore[type-arg]
    from tableforge.learning.opponents import model_member, rule_members

    members = []
    for item in models:
        name, _, path = item.partition("=")
        algorithm = "nfsp"
        if path.startswith("ppo:"):
            algorithm, path = "ppo", path[4:]
        members.append(model_member(name, Path(path).read_bytes(), algorithm=algorithm))
    if library_models:
        from tableforge.models import ModelLibrary
        from tableforge.web.cluster import Layout, default_home

        library = ModelLibrary(Layout(default_home()).models)
        for model_id in library_models:
            info = library.info(model_id)
            members.append(
                model_member(model_id, library.checkpoint(model_id), algorithm=info.algorithm)
            )
    for rule in rules:
        member = rule_members([rule])[0]
        members.append(member)
    return members


def _row(task: tuple[list[str], list[str], list[str], str, int, int]) -> dict[str, object]:
    import torch

    from tableforge.learning.comparison import play_row
    from tableforge.poker.tournament_spec import TournamentSpec

    torch.set_num_threads(1)
    models, library_models, rules, tournament, seeds, row = task
    config = TournamentSpec.from_dict(json.loads(tournament)).to_config()
    members = _members(models, library_models, rules)
    started = time.perf_counter()
    result = play_row(members, row, config, seeds, SEED_START)
    print(
        f"row {row} ({members[row].name}) done in {time.perf_counter() - started:.0f}s", flush=True
    )
    return result


def _field(task: tuple[list[str], list[str], list[str], str, int, int]) -> dict[str, object]:
    import torch

    from tableforge.poker.tournament_benchmark import evaluate_tournament
    from tableforge.poker.tournament_spec import TournamentSpec
    from tableforge.worker.tournament import field_factory

    torch.set_num_threads(1)
    models, library_models, rules, tournament, seeds, row = task
    config = TournamentSpec.from_dict(json.loads(tournament)).to_config()
    member = _members(models, library_models, rules)[row]
    summary = evaluate_tournament(
        lambda: member.build(0), field_factory("mixed"), config, seeds, 900_000_000
    ).summary()
    print(f"{member.name}: ROI {summary['roi']}", flush=True)
    return {"name": member.name, **{key: summary[key] for key in ("roi", "itm", "bounty_ev")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=("run", "field"))
    parser.add_argument("output")
    parser.add_argument("--tournament", default="{}", help="tournament spec as JSON")
    parser.add_argument("--model", action="append", default=[], help="NAME=checkpoint path")
    parser.add_argument("--library-model", action="append", default=[])
    parser.add_argument("--rule", action="append", default=[])
    parser.add_argument("--seeds", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--stack-bb", type=float, default=0, help="shortcut for the spec")
    args = parser.parse_args()
    if args.stack_bb:
        args.tournament = json.dumps({"starting_stack_bb": args.stack_bb})
    count = len(args.model) + len(args.library_model) + len(args.rule)
    tasks = [
        (args.model, args.library_model, args.rule, args.tournament, args.seeds, row)
        for row in range(count)
    ]
    started = time.perf_counter()
    with ProcessPoolExecutor(min(args.workers, count)) as pool:
        results = list(pool.map(_row if args.mode == "run" else _field, tasks))
    if args.mode == "run":
        from tableforge.learning.comparison import summarize
        from tableforge.poker.tournament_spec import TournamentSpec

        config = TournamentSpec.from_dict(json.loads(args.tournament)).to_config()
        names = [item.partition("=")[0] for item in args.model] + args.library_model + args.rule
        output: object = summarize(names, config, args.seeds, SEED_START, results)
    else:
        output = {
            "tournament": json.loads(args.tournament),
            "seeds": args.seeds,
            "results": results,
        }
    Path(args.output).write_text(json.dumps(output, indent=1), encoding="utf-8")
    print(f"done in {time.perf_counter() - started:.0f}s -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
