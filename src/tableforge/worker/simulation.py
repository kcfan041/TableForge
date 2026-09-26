from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.poker.betting import Seat
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.runner import HandRunner
from tableforge.poker.table_play import simulate_table


@dataclass(frozen=True, slots=True)
class SimulationExecutor:
    progress_interval_hands: int = 100

    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        payload = SimulationJobPayload.from_bytes(payload_bytes)
        if payload.players != 2:
            return self._execute_table(payload, on_progress)
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
            completed = index + 1
            if (
                on_progress is not None
                and self.progress_interval_hands > 0
                and completed % self.progress_interval_hands == 0
            ):
                on_progress(completed)
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

    def _execute_table(
        self,
        payload: SimulationJobPayload,
        on_progress: Callable[[int], None] | None,
    ) -> bytes:
        summary = simulate_table(
            players=payload.players,
            hands=payload.hands,
            seed_start=payload.seed_start,
            starting_stack=payload.starting_stack,
            small_blind=payload.small_blind,
            big_blind=payload.big_blind,
            on_progress=on_progress,
            progress_interval=self.progress_interval_hands,
        )
        return json.dumps(
            {
                "actions": summary.actions,
                "folds": summary.folds,
                "hands": summary.hands,
                "players": summary.players,
                "schema_version": "tableforge.simulation-summary.v1",
                "seed_end_exclusive": payload.seed_start + payload.hands,
                "seed_start": payload.seed_start,
                "showdowns": summary.showdowns,
                "side_pots": summary.side_pots,
                "winnings": list(summary.winnings),
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
