"""T3 probe: NFSP on 6-max PKO tournaments, benchmarked against a mixed rule-bot field.

uv run python scripts/tournament_probe.py --tournaments 20000 --checkpoints 4 --seeds 300

Prints one JSON line per checkpoint with the tournament benchmark of the average policy
(ROI, Prize EV, Bounty EV, ITM with 95% intervals). Baselines on the same benchmark:
`HeuristicPushFoldBot` ROI -10.5%, `hunter` -7.3% (10 BB PKO, 65/35).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

from tableforge.learning.nfsp import NfspAgent, NfspConfig
from tableforge.learning.tournament_training import (
    FrozenTournamentPlayer,
    GreedyTournamentPlayer,
    tournament_self_play_train,
)
from tableforge.poker.tournament import BountyConfig, preset_config
from tableforge.poker.tournament_benchmark import evaluate_tournament
from tableforge.worker.tournament import field_factory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tournaments", type=int, default=20_000)
    parser.add_argument("--checkpoints", type=int, default=4)
    parser.add_argument("--seeds", type=int, default=300, help="benchmark seeds (x6 seats)")
    parser.add_argument("--stack-bb", type=float, default=10)
    parser.add_argument("--no-pko", action="store_true")
    parser.add_argument(
        "--tournament", default="", help="tournament spec JSON (overrides --stack-bb/--no-pko)"
    )
    parser.add_argument(
        "--pool-models", default="", help="library model ids to train against (or 'library')"
    )
    parser.add_argument(
        "--pool-rules", default="random,call,tight,aggro,pushfold,hunter", help="rule bots"
    )
    parser.add_argument("--pfsp", default="uniform", choices=("uniform", "hard", "variance"))
    parser.add_argument("--history-every", type=int, default=0, help="snapshot every N")
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument("--pool-eval-seeds", type=int, default=0, help="also vs model pool")
    parser.add_argument(
        "--exploit-target",
        default="",
        help="S4: train only against this library model (or rule:<bot>) and report the ROI "
        "against a field of it (average policy and best response); implies --self-play 0",
    )
    parser.add_argument("--library", default="", help="model library folder")
    parser.add_argument(
        "--init-model", default="", help="continue training this library model (full state)"
    )
    parser.add_argument("--init-checkpoint", default="", help="continue from a checkpoint file")
    parser.add_argument("--seed-offset", type=int, default=0, help="first training seed index")
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--train-every", type=int, default=1, help="hands per train step")
    parser.add_argument("--anticipatory", type=float, default=0.1)
    parser.add_argument("--epsilon", type=float, default=0.06)
    parser.add_argument("--target-update", type=int, default=200)
    parser.add_argument("--reward-scale", type=float, default=1.0)
    parser.add_argument("--self-play", type=float, default=0.5)
    parser.add_argument("--double-q", action="store_true")
    parser.add_argument("--rl-capacity", type=int, default=50_000)
    parser.add_argument("--eval-seed-start", type=int, default=900_000_000)
    parser.add_argument("--greedy", action="store_true", help="also benchmark the Q network")
    parser.add_argument("--save", default="", help="write the final checkpoint here")
    parser.add_argument("--sl-capacity", type=int, default=100_000)
    parser.add_argument("--hand-horizon", action="store_true")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--v3", action="store_true", help="v3 encoder (2-9 players) and v2 bet sizes"
    )
    parser.add_argument(
        "--sampler",
        default="",
        help="train on tournaments drawn from this TournamentSampler JSON (needs --v3); "
        "the benchmark stays --tournament/--stack-bb",
    )
    args = parser.parse_args()
    if args.threads:
        torch.set_num_threads(args.threads)
    if args.tournament:
        from tableforge.poker.tournament_spec import TournamentSpec

        config = TournamentSpec.from_dict(json.loads(args.tournament)).to_config()
    else:
        config = preset_config(args.stack_bb, bounty=None if args.no_pko else BountyConfig())
    agent = NfspAgent(
        NfspConfig(
            hidden_size=args.hidden,
            batch_size=args.batch,
            learning_rate=args.lr,
            anticipatory=args.anticipatory,
            epsilon=args.epsilon,
            target_update_steps=args.target_update,
            reward_scale=args.reward_scale,
            double_q=args.double_q,
            rl_capacity=args.rl_capacity,
            sl_capacity=args.sl_capacity,
            seed=args.seed,
            observation_schema="poker-nlhe-vector-v3" if args.v3 else "poker-hunlhe-vector-v2",
            action_schema="nlhe-discrete-v2" if args.v3 else "nlhe-discrete-v1",
        )
    )
    training_config: object = config
    if args.sampler:
        from tableforge.poker.tournament_sampler import TournamentSampler

        training_config = TournamentSampler.from_json(args.sampler)
    if args.init_model:
        from pathlib import Path as _InitPath

        from tableforge.models import ModelLibrary as _InitLibrary
        from tableforge.web.cluster import Layout as _InitLayout
        from tableforge.web.cluster import default_home as _init_home

        init_library = _InitLibrary(
            _InitPath(args.library) if args.library else _InitLayout(_init_home()).models
        )
        agent = NfspAgent.from_bundle(init_library.checkpoint(args.init_model))
        agent.rng.seed(args.seed)
    if args.init_checkpoint:
        from pathlib import Path as _CheckpointPath

        agent = NfspAgent.from_bundle(_CheckpointPath(args.init_checkpoint).read_bytes())
        agent.rng.seed(args.seed + 1)
    opponents = None
    pool_members: list = []  # type: ignore[type-arg]
    target_members: list = []  # type: ignore[type-arg]
    if args.exploit_target:
        from pathlib import Path as _TargetPath

        from tableforge.learning.opponents import OpponentPool, model_member, rule_members
        from tableforge.models import ModelLibrary as _TargetLibrary
        from tableforge.web.cluster import Layout as _TargetLayout
        from tableforge.web.cluster import default_home as _target_home

        if args.exploit_target.startswith("rule:"):
            target_members = rule_members([args.exploit_target[5:]])
        else:
            target_library = _TargetLibrary(
                _TargetPath(args.library) if args.library else _TargetLayout(_target_home()).models
            )
            target_members = [
                model_member(
                    f"model:{args.exploit_target}",
                    target_library.checkpoint(args.exploit_target),
                )
            ]
        opponents = OpponentPool(list(target_members))
        args.self_play = 0.0
    if args.pool_models or args.pool_eval_seeds:
        from pathlib import Path as _Path

        from tableforge.learning.opponents import OpponentPool, rule_members
        from tableforge.learning.pool_benchmark import library_pool
        from tableforge.models import ModelLibrary
        from tableforge.web.cluster import Layout, default_home

        library = ModelLibrary(
            _Path(args.library) if args.library else Layout(default_home()).models
        )
        pool_members = library_pool(library, config)
        if args.pool_models and args.pool_models != "library":
            wanted = {f"model:{item}" for item in args.pool_models.split(",") if item}
            pool_members = [member for member in pool_members if member.name in wanted]
        if args.pool_models:
            opponents = OpponentPool(
                rule_members([item for item in args.pool_rules.split(",") if item])
                + list(pool_members),
                weighting=args.pfsp,
                history_limit=args.history_limit,
            )
    per_stage = args.tournaments // args.checkpoints
    trained = 0
    hands = 0
    started = time.perf_counter()
    for stage in range(args.checkpoints + 1):
        losses: dict[str, float] = {}
        if stage:
            report = tournament_self_play_train(
                agent,
                training_config,  # type: ignore[arg-type]
                per_stage,
                seed_start=args.seed * 10_000_000 + args.seed_offset + trained,
                train_every_hands=args.train_every,
                self_play_share=args.self_play,
                hand_horizon=args.hand_horizon,
                opponents=opponents,
                history_every=args.history_every,
            )
            trained += per_stage
            hands += report.hands
            losses = report.last_metrics
        bundle = agent.to_bundle()
        frozen = NfspAgent.from_bundle(bundle, inference_only=True)
        benchmark = evaluate_tournament(
            lambda frozen=frozen: FrozenTournamentPlayer(frozen),
            field_factory("mixed"),
            config,
            seeds=args.seeds,
            seed_start=args.eval_seed_start,
        ).summary()
        pool_result: dict[str, object] | None = None
        if args.pool_eval_seeds and pool_members:
            from tableforge.learning.pool_benchmark import pool_benchmark

            pool_result = pool_benchmark(
                lambda frozen=frozen: FrozenTournamentPlayer(frozen),
                pool_members,
                config,
                args.pool_eval_seeds,
                1_700_000_000,
            )["overall"]  # type: ignore[assignment]
        exploit: dict[str, object] | None = None
        if target_members:
            from tableforge.learning.pool_benchmark import rotating_field

            q_target = NfspAgent.from_actor_bundle(bundle)
            q_target.training = False
            exploit = {}
            for name, factory in (
                ("average", lambda frozen=frozen: FrozenTournamentPlayer(frozen)),
                ("best_response", lambda q=q_target: GreedyTournamentPlayer(q)),
            ):
                summary = evaluate_tournament(
                    factory,
                    rotating_field(target_members),
                    config,
                    seeds=args.pool_eval_seeds or args.seeds,
                    seed_start=1_800_000_000,
                ).summary()
                exploit[name] = {key: summary[key] for key in ("roi", "bounty_ev", "itm")}
        greedy: dict[str, object] = {}
        if args.greedy and stage:
            q_agent = NfspAgent.from_actor_bundle(bundle)
            q_agent.training = False
            greedy = evaluate_tournament(
                lambda q_agent=q_agent: GreedyTournamentPlayer(q_agent),
                field_factory("mixed"),
                config,
                seeds=args.seeds,
                seed_start=args.eval_seed_start,
            ).summary()
        print(
            json.dumps(
                {
                    "tag": args.tag,
                    "seed": args.seed,
                    "pool": pool_result,
                    "exploit": exploit,
                    "opponents": opponents.summary() if opponents is not None else None,
                    "tournaments": trained,
                    "hands": hands,
                    "elapsed_s": round(time.perf_counter() - started),
                    "train_steps": agent.train_steps,
                    "losses": {key: round(value, 5) for key, value in losses.items()},
                    "benchmark": {
                        key: benchmark[key]
                        for key in ("roi", "prize_ev", "bounty_ev", "itm", "average_place")
                    },
                    "greedy": {
                        key: greedy[key]
                        for key in ("roi", "prize_ev", "bounty_ev", "itm", "average_place")
                    }
                    if greedy
                    else None,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_bytes(agent.to_bundle())
    sys.exit(0)


if __name__ == "__main__":
    main()
