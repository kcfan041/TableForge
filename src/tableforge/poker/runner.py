from __future__ import annotations

from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.poker.betting import Seat
from tableforge.poker.environment import HandResult, HeadsUpHand


@dataclass(frozen=True, slots=True)
class HandRunner:
    max_actions: int = 1_000

    def run(
        self,
        hand: HeadsUpHand,
        agents: tuple[PokerAgent, PokerAgent],
    ) -> HandResult:
        action_count = 0
        while not hand.complete:
            actor = hand.betting.current_actor
            if actor is None:
                raise AssertionError("active hand must have a current actor")
            observation = hand.observe(actor)
            legal_actions = observation.legal_actions
            if legal_actions is None:
                raise AssertionError("current actor must receive legal actions")
            action = agents[int(actor)].act(observation, legal_actions)
            hand.act(Seat(actor), action)
            action_count += 1
            if action_count > self.max_actions:
                raise RuntimeError("hand exceeded maximum action count")
        if hand.result is None:
            raise AssertionError("complete hand must have a result")
        return hand.result
