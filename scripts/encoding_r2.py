"""Can an input encoding express hand strength? Supervised equity regression, v1 vs v2.

Random heads-up spots (hole cards and 0, 3, 4 or 5 board cards) are labelled with the
Monte Carlo equity of the hole cards against a random hand. A network with the same
shape as the NFSP networks learns to predict it from each encoding; the test R² shows
how much hand strength the encoding makes learnable.

    uv run python scripts/encoding_r2.py [--samples 20000] [--epochs 30] [--trials 200]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

import torch
from torch import nn

from tableforge.learning import encoding as v1
from tableforge.learning import encoding_v2 as v2
from tableforge.poker.betting import Action, Seat
from tableforge.poker.cards import Card, full_deck
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.evaluator import evaluate


def equity(
    hole: tuple[Card, Card], board: tuple[Card, ...], trials: int, rng: random.Random
) -> float:
    used = set(hole) | set(board)
    deck = [card for card in full_deck() if card not in used]
    total = 0.0
    for _ in range(trials):
        drawn = rng.sample(deck, 2 + 5 - len(board))
        villain = (drawn[0], drawn[1])
        runout = (*board, *drawn[2:])
        mine = evaluate((*hole, *runout))
        theirs = evaluate((*villain, *runout))
        total += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
    return total / trials


def spot(seed: int, board_cards: int) -> HeadsUpHand:
    hand = HeadsUpHand((200, 200), Seat.PLAYER_0, 1, 2, seed=seed)
    streets = {0: 0, 3: 1, 4: 2, 5: 3}[board_cards]
    for _ in range(streets):
        # Check/call down so the board is dealt without changing stack features much.
        for _ in range(2):
            actor = hand.betting.current_actor
            if actor is None:
                break
            legal = hand.legal_actions(actor)
            hand.act(actor, Action.check() if legal.can_check else Action.call())
    return hand


Rows = tuple[list[list[float]], list[list[float]], list[float]]


def _chunk(arguments: tuple[int, int, int, int]) -> Rows:
    start, count, trials, seed = arguments
    rng = random.Random(seed * 7_919 + start)
    first: list[list[float]] = []
    second: list[list[float]] = []
    targets: list[float] = []
    for index in range(start, start + count):
        hand = spot(seed * 1_000_003 + index, rng.choice((0, 3, 4, 5)))
        observation = hand.observe(Seat.PLAYER_0)
        first.append(v1.encode(observation, 2))
        second.append(v2.encode(observation, 2))
        targets.append(equity(hand.hole_cards[0], observation.board, trials, rng))
    return first, second, targets


def dataset(samples: int, trials: int, seed: int, workers: int) -> Rows:
    size = -(-samples // workers)
    chunks = [
        (start, min(size, samples - start), trials, seed) for start in range(0, samples, size)
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(_chunk, chunks))
    first = [row for part in parts for row in part[0]]
    second = [row for part in parts for row in part[1]]
    targets = [value for part in parts for value in part[2]]
    return first, second, targets


def r_squared(features: list[list[float]], targets: list[float], epochs: int, seed: int) -> float:
    torch.manual_seed(seed)
    x = torch.tensor(features, dtype=torch.float32)
    y = torch.tensor(targets, dtype=torch.float32).unsqueeze(1)
    split = int(len(x) * 0.8)
    model = nn.Sequential(
        nn.Linear(x.shape[1], 128),
        nn.LayerNorm(128),
        nn.ReLU(),
        nn.Linear(128, 128),
        nn.ReLU(),
        nn.Linear(128, 1),
    )
    optimizer = torch.optim.Adam(model.parameters(), 1e-3)
    for _ in range(epochs):
        order = torch.randperm(split)
        for start in range(0, split, 128):
            batch = order[start : start + 128]
            loss = nn.functional.mse_loss(model(x[batch]), y[batch])
            optimizer.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
    with torch.no_grad():
        predicted = model(x[split:])
        actual = y[split:]
        residual = ((predicted - actual) ** 2).sum()
        total = ((actual - actual.mean()) ** 2).sum()
    return float(1 - residual / total)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20_000)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 1))
    args = parser.parse_args()
    started = time.perf_counter()
    first, second, targets = dataset(args.samples, args.trials, args.seed, args.workers)
    built = time.perf_counter() - started
    print(
        json.dumps(
            {
                "samples": args.samples,
                "epochs": args.epochs,
                "equity_trials": args.trials,
                "dataset_seconds": round(built, 1),
                "r2_v1": round(r_squared(first, targets, args.epochs, args.seed), 3),
                "r2_v2": round(r_squared(second, targets, args.epochs, args.seed), 3),
                "features_v1": len(first[0]),
                "features_v2": len(second[0]),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
