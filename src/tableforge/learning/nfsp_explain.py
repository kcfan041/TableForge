"""NFSP implementation of `DecisionExplainer`."""

from __future__ import annotations

import torch

from tableforge.learning.algorithms import NFSP_AVERAGE, NFSP_BEST_RESPONSE
from tableforge.learning.explain import ACTIONS, DecisionExplanation, Detail, PlayMode
from tableforge.learning.nfsp import MASKED, NfspAgent
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo

AVERAGE, BEST_RESPONSE = NFSP_AVERAGE, NFSP_BEST_RESPONSE


class NfspExplainer:
    """Explains an NFSP checkpoint; needs the actor weights (policy and Q networks)."""

    def __init__(self, bundle: bytes) -> None:
        self.agent = NfspAgent.from_actor_bundle(bundle)
        self.agent.training = False
        self.info: TournamentHandInfo | None = None

    @property
    def play_modes(self) -> tuple[PlayMode, ...]:
        return (AVERAGE, BEST_RESPONSE)

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.agent.begin_hand(info)
        self.info = info

    def _values(
        self, observation: TableObservation, legal: LegalActions
    ) -> tuple[list[bool], list[float], list[float]]:
        state = self.agent.encode(observation)
        mask = list(self.agent.legal_mask(observation, legal))
        device = self.agent.device
        state_tensor = torch.tensor([state], dtype=torch.float32, device=device)
        mask_tensor = torch.tensor([mask], dtype=torch.bool, device=device)
        with torch.no_grad():
            logits = self.agent.policy_network(state_tensor).masked_fill(~mask_tensor, MASKED)
            policy = torch.softmax(logits, dim=1)[0].tolist()
            q_values = self.agent.q_network(state_tensor)[0].tolist()
        return mask, policy, q_values

    def explain(
        self, observation: TableObservation, legal: LegalActions, mode: str
    ) -> DecisionExplanation:
        mask, policy, q_values = self._values(observation, legal)
        actions = self.agent.actions
        legal_actions = [index for index, allowed in enumerate(mask) if allowed]
        if mode == BEST_RESPONSE.key:
            best = max(legal_actions, key=lambda action: q_values[action])
            weights = {action: 1.0 if action == best else 0.0 for action in legal_actions}
        elif mode == AVERAGE.key:
            weights = {action: policy[action] for action in legal_actions}
        else:
            raise ValueError(f"unknown play mode: {mode}")
        probabilities = dict.fromkeys(ACTIONS, 0.0)
        raise_sizes: dict[str, float] = {}
        for action, weight in weights.items():
            probabilities[actions.canonical[action]] += weight
            if actions.size_label[action]:
                raise_sizes[actions.size_label[action]] = weight
        total = sum(probabilities.values())
        probabilities = {name: value / total for name, value in probabilities.items()}
        scale = self.agent.config.reward_scale
        unit = "buy-in" if self.info is not None else "big blind"
        q_detail: dict[str, float] = {}
        for action in legal_actions:
            size = actions.size_label[action]
            name = f"raise_{size}" if size else actions.canonical[action]
            q_detail[name] = q_values[action] * scale
        return DecisionExplanation(
            probabilities,
            details=(
                Detail(
                    "Q value (expected change this hand)"
                    if self.info is not None
                    else "Q value (expected result)",
                    unit,
                    per_action=q_detail,
                ),
            ),
            raise_sizes=raise_sizes,
        )

    def act(self, observation: TableObservation, legal: LegalActions, mode: str) -> Action:
        self.agent.best_response_mode = mode == BEST_RESPONSE.key
        if mode not in (AVERAGE.key, BEST_RESPONSE.key):
            raise ValueError(f"unknown play mode: {mode}")
        return self.agent.act(observation, legal)
