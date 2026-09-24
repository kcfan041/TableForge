from __future__ import annotations

from typing import Protocol

from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.observation import Observation


class PokerAgent(Protocol):
    def act(self, observation: Observation, legal_actions: LegalActions) -> Action: ...
