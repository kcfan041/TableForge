"""PPO implementation of `DecisionExplainer`: policy probabilities and the state value."""

from __future__ import annotations

from tableforge.learning.algorithms import PPO_GREEDY, PPO_SAMPLE
from tableforge.learning.explain import ACTIONS, DecisionExplanation, Detail, PlayMode
from tableforge.learning.ppo import PpoAgent
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo


class PpoExplainer:
    def __init__(self, bundle: bytes) -> None:
        self.agent = PpoAgent.from_bundle(bundle)
        self.agent.training = False
        self.info: TournamentHandInfo | None = None

    @property
    def play_modes(self) -> tuple[PlayMode, ...]:
        return (PPO_SAMPLE, PPO_GREEDY)

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.agent.begin_hand(info)
        self.info = info

    def explain(
        self, observation: TableObservation, legal: LegalActions, mode: str
    ) -> DecisionExplanation:
        if mode not in (PPO_SAMPLE.key, PPO_GREEDY.key):
            raise ValueError(f"unknown play mode: {mode}")
        state = self.agent.encode(observation)
        mask = self.agent.legal_mask(observation, legal)
        policy, value = self.agent.evaluate_state(state, mask)
        actions = self.agent.actions
        legal_actions = [index for index, allowed in enumerate(mask) if allowed]
        if mode == PPO_GREEDY.key:
            best = max(legal_actions, key=lambda index: policy[index])
            weights = {index: 1.0 if index == best else 0.0 for index in legal_actions}
        else:
            weights = {index: policy[index] for index in legal_actions}
        probabilities = dict.fromkeys(ACTIONS, 0.0)
        raise_sizes: dict[str, float] = {}
        for index, weight in weights.items():
            probabilities[actions.canonical[index]] += weight
            if actions.size_label[index]:
                raise_sizes[actions.size_label[index]] = weight
        total = sum(probabilities.values())
        probabilities = {name: item / total for name, item in probabilities.items()}
        return DecisionExplanation(
            probabilities,
            details=(
                Detail(
                    "State value (expected equity change)",
                    "buy-in",
                    value=value * self.agent.config.reward_scale,
                ),
            ),
            raise_sizes=raise_sizes,
        )

    def act(self, observation: TableObservation, legal: LegalActions, mode: str) -> Action:
        if mode not in (PPO_SAMPLE.key, PPO_GREEDY.key):
            raise ValueError(f"unknown play mode: {mode}")
        self.agent.greedy = mode == PPO_GREEDY.key
        return self.agent.act(observation, legal)
