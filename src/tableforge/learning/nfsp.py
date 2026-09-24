from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file, save_file
from torch import Tensor, nn

from tableforge.learning.actions import ACTION_COUNT, AbstractAction, ActionTranslator
from tableforge.learning.actions import SCHEMA as ACTION_SCHEMA
from tableforge.learning.buffers import (
    CircularBuffer,
    PolicySample,
    ReservoirBuffer,
    Transition,
)
from tableforge.learning.encoding import FEATURE_SIZE, encode
from tableforge.learning.encoding import SCHEMA as OBSERVATION_SCHEMA
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.observation import Observation

MASKED = -1e9


@dataclass(frozen=True, slots=True)
class NfspConfig:
    hidden_size: int = 128
    anticipatory: float = 0.1
    epsilon: float = 0.06
    rl_capacity: int = 50_000
    sl_capacity: int = 100_000
    batch_size: int = 128
    learning_rate: float = 1e-3
    gamma: float = 1.0
    target_update_steps: int = 200
    big_blind: int = 2
    seed: int = 0


def _network(hidden: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(FEATURE_SIZE, hidden),
        nn.LayerNorm(hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
        nn.ReLU(),
        nn.Linear(hidden, ACTION_COUNT),
    )


def _masked(logits: Tensor, mask: Tensor) -> Tensor:
    return logits.masked_fill(~mask, MASKED)


class NfspAgent:
    def __init__(self, config: NfspConfig) -> None:
        torch.manual_seed(config.seed)
        self.config = config
        self.rng = random.Random(config.seed)
        self.q_network = _network(config.hidden_size)
        self.target_network = _network(config.hidden_size)
        self.target_network.load_state_dict(self.q_network.state_dict())
        self.policy_network = _network(config.hidden_size)
        self.q_optimizer = torch.optim.Adam(self.q_network.parameters(), config.learning_rate)
        self.policy_optimizer = torch.optim.Adam(
            self.policy_network.parameters(), config.learning_rate
        )
        self.rl_buffer: CircularBuffer[Transition] = CircularBuffer(config.rl_capacity)
        self.sl_buffer: ReservoirBuffer[PolicySample] = ReservoirBuffer(config.sl_capacity)
        self.translator = ActionTranslator()
        self.training = True
        self.best_response_mode = False
        self.train_steps = 0
        self.last_choice: AbstractAction | None = None

    def start_hand(self) -> None:
        self.best_response_mode = self.training and self.rng.random() < self.config.anticipatory

    def act(self, observation: Observation, legal: LegalActions) -> Action:
        state = encode(observation, self.config.big_blind)
        mask = self.translator.legal_mask(legal)
        choice = self.choose(state, mask)
        self.last_choice = choice
        if self.training and self.best_response_mode:
            self.sl_buffer.add(PolicySample(tuple(state), mask, int(choice)), self.rng)
        me = observation.players[int(observation.observer)]
        return self.translator.to_action(
            choice,
            legal,
            observation.pot,
            observation.current_bet,
            me.street_contribution,
        )

    def choose(self, state: Sequence[float], mask: tuple[bool, ...]) -> AbstractAction:
        legal = [index for index, allowed in enumerate(mask) if allowed]
        if not legal:
            raise ValueError("no legal abstract actions")
        state_tensor = torch.tensor([state], dtype=torch.float32)
        mask_tensor = torch.tensor([mask], dtype=torch.bool)
        with torch.no_grad():
            if self.best_response_mode:
                if self.training and self.rng.random() < self.config.epsilon:
                    return AbstractAction(self.rng.choice(legal))
                values = _masked(self.q_network(state_tensor), mask_tensor)
                return AbstractAction(int(values.argmax(dim=1).item()))
            logits = _masked(self.policy_network(state_tensor), mask_tensor)
            probabilities = torch.softmax(logits, dim=1)[0].tolist()
        return AbstractAction(self.rng.choices(range(ACTION_COUNT), probabilities)[0])

    def train_step(self) -> dict[str, float]:
        metrics: dict[str, float] = {}
        if len(self.rl_buffer) >= self.config.batch_size:
            metrics["q_loss"] = self._train_q()
        if len(self.sl_buffer) >= self.config.batch_size:
            metrics["policy_loss"] = self._train_policy()
        self.train_steps += 1
        if self.train_steps % self.config.target_update_steps == 0:
            self.target_network.load_state_dict(self.q_network.state_dict())
        return metrics

    def _train_q(self) -> float:
        batch = self.rl_buffer.sample(self.config.batch_size, self.rng)
        states = torch.tensor([item.state for item in batch], dtype=torch.float32)
        actions = torch.tensor([item.action for item in batch], dtype=torch.int64)
        rewards = torch.tensor([item.reward for item in batch], dtype=torch.float32)
        done = torch.tensor([item.next_state is None for item in batch], dtype=torch.bool)
        next_states = torch.tensor(
            [item.next_state or (0.0,) * FEATURE_SIZE for item in batch], dtype=torch.float32
        )
        next_masks = torch.tensor(
            [item.next_mask or (True,) * ACTION_COUNT for item in batch], dtype=torch.bool
        )
        with torch.no_grad():
            next_values = _masked(self.target_network(next_states), next_masks).max(dim=1).values
            targets = rewards + self.config.gamma * next_values.masked_fill(done, 0.0)
        predicted = self.q_network(states).gather(1, actions.unsqueeze(1)).squeeze(1)
        loss = nn.functional.smooth_l1_loss(predicted, targets)
        self.q_optimizer.zero_grad()
        loss.backward()  # type: ignore[no-untyped-call]
        nn.utils.clip_grad_norm_(self.q_network.parameters(), 10.0)
        self.q_optimizer.step()
        return float(loss.item())

    def _train_policy(self) -> float:
        batch = self.sl_buffer.sample(self.config.batch_size, self.rng)
        states = torch.tensor([item.state for item in batch], dtype=torch.float32)
        masks = torch.tensor([item.mask for item in batch], dtype=torch.bool)
        actions = torch.tensor([item.action for item in batch], dtype=torch.int64)
        logits = _masked(self.policy_network(states), masks)
        loss = nn.functional.cross_entropy(logits, actions)
        self.policy_optimizer.zero_grad()
        loss.backward()  # type: ignore[no-untyped-call]
        self.policy_optimizer.step()
        return float(loss.item())

    def save(self, directory: Path) -> dict[str, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        inference = directory / "inference.safetensors"
        training = directory / "training.safetensors"
        manifest = directory / "manifest.json"
        save_file(_prefixed("policy", self.policy_network.state_dict()), str(inference))
        save_file(
            {
                **_prefixed("q", self.q_network.state_dict()),
                **_prefixed("target", self.target_network.state_dict()),
            },
            str(training),
        )
        manifest.write_text(
            json.dumps(
                {
                    "action_schema": ACTION_SCHEMA,
                    "algorithm": "nfsp",
                    "config": asdict(self.config),
                    "observation_schema": OBSERVATION_SCHEMA,
                    "schema_version": "1.0",
                    "train_steps": self.train_steps,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return {"inference": inference, "training": training, "manifest": manifest}

    @classmethod
    def load_inference(cls, directory: Path) -> NfspAgent:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["observation_schema"] != OBSERVATION_SCHEMA:
            raise ValueError("unsupported observation schema")
        if manifest["action_schema"] != ACTION_SCHEMA:
            raise ValueError("unsupported action schema")
        agent = cls(NfspConfig(**manifest["config"]))
        weights = load_file(str(directory / "inference.safetensors"))
        agent.policy_network.load_state_dict(_unprefixed("policy", weights))
        agent.train_steps = int(manifest["train_steps"])
        agent.training = False
        return agent


def _prefixed(prefix: str, state: dict[str, Any]) -> dict[str, Tensor]:
    return {f"{prefix}.{key}": value.detach().clone() for key, value in state.items()}


def _unprefixed(prefix: str, state: dict[str, Tensor]) -> dict[str, Tensor]:
    marker = f"{prefix}."
    return {
        key.removeprefix(marker): value
        for key, value in state.items()
        if key.startswith(marker)
    }
