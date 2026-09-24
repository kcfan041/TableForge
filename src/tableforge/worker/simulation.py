from __future__ import annotations

import json
from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.poker.betting import Seat
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.runner import HandRunner


@dataclass(frozen=True, slots=True)
class SimulationExecutor:
    def execute(self, payload_bytes: bytes) -> bytes:
        payload = SimulationJobPayload.from_bytes(payload_bytes)
        runner = HandRunner()
        actions = 0
        folds = 0
        showdowns = 0
        winnings = [0, 0]
        for index in range(payload.hands):
            seed = payload.seed_start + index
            agents = _agents_for_hand(index, seed)
            button = Seat(index % 2)
            hand = HeadsUpHand(
                stacks=(payload.starting_stack, payload.starting_stack),
                button=button,
                small_blind=payload.small_blind,
                big_blind=payload.big_blind,
                seed=seed,
            )
            result = runner.run(hand, agents)
            expected_total = payload.starting_stack * 2
            if sum(result.final_stacks) != expected_total:
                raise AssertionError(f"chip conservation failed at hand {index}")
            actions += len(hand.action_history)
            folds += int(result.ended_by_fold)
            showdowns += int(not result.ended_by_fold)
            winnings[0] += result.final_stacks[0] - payload.starting_stack
            winnings[1] += result.final_stacks[1] - payload.starting_stack
        if sum(winnings) != 0:
            raise AssertionError("aggregate winnings do not sum to zero")
        return json.dumps(
            {
                "actions": actions,
                "folds": folds,
                "hands": payload.hands,
                "schema_version": "tableforge.simulation-summary.v1",
                "seed_end_exclusive": payload.seed_start + payload.hands,
                "seed_start": payload.seed_start,
                "showdowns": showdowns,
                "winnings": winnings,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def _agents_for_hand(index: int, seed: int) -> tuple[PokerAgent, PokerAgent]:
    matchup = index % 4
    if matchup == 0:
        return CallBot(), CallBot()
    if matchup == 1:
        return TightBot(), AggroBot()
    if matchup == 2:
        return AggroBot(), CallBot()
    return RandomBot(seed * 2 + 1), RandomBot(seed * 2 + 2)
