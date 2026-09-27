from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from safetensors.torch import load as st_load
from safetensors.torch import save as st_save
from torch import Tensor, nn

from tableforge.learning.actions import ACTION_COUNT, AbstractAction, ActionTranslator
from tableforge.learning.actions import SCHEMA as ACTION_SCHEMA
from tableforge.learning.buffers import (
    CircularBuffer,
    PolicySample,
    ReservoirBuffer,
    Transition,
)
from tableforge.learning.bundle import (
    array_bytes,
    array_from_bytes,
    json_bytes,
    pack_entries,
    unpack_entries,
)
from tableforge.learning.encoders import DEFAULT_SCHEMA, Encoder, encoder
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
    observation_schema: str = DEFAULT_SCHEMA
    reward_scale: float = 1.0
    """Rewards (in big blinds) are divided by this before Q learning.

    The Huber loss is linear for errors above 1, which makes Q estimate something close to
    the median return instead of the mean when rewards are tens of big blinds. Scaling by
    the stack depth (e.g. 100) keeps errors in the quadratic range. Actions are unchanged
    because every Q value is scaled the same way.
    """


def _network(inputs: int, hidden: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(inputs, hidden),
        nn.LayerNorm(hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
        nn.ReLU(),
        nn.Linear(hidden, ACTION_COUNT),
    )


def _masked(logits: Tensor, mask: Tensor) -> Tensor:
    return logits.masked_fill(~mask, MASKED)


class NfspAgent:
    def __init__(self, config: NfspConfig, device: str = "cpu") -> None:
        if config.reward_scale <= 0:
            raise ValueError("reward_scale must be positive")
        torch.manual_seed(config.seed)
        self.config = config
        self.device = torch.device(device)
        self.rng = random.Random(config.seed)
        self.encoder: Encoder = encoder(config.observation_schema)
        inputs = self.encoder.feature_size
        self.q_network = _network(inputs, config.hidden_size).to(self.device)
        self.target_network = _network(inputs, config.hidden_size).to(self.device)
        self.target_network.load_state_dict(self.q_network.state_dict())
        self.policy_network = _network(inputs, config.hidden_size).to(self.device)
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
        self.batch_size = config.batch_size
        self.last_choice: AbstractAction | None = None
        self.policy_sink: Callable[[PolicySample], None] | None = None

    def start_hand(self) -> None:
        self.best_response_mode = self.training and self.rng.random() < self.config.anticipatory

    def act(self, observation: Observation, legal: LegalActions) -> Action:
        state = self.encoder.encode(observation, self.config.big_blind)
        mask = self.translator.legal_mask(legal)
        choice = self.choose(state, mask)
        self.last_choice = choice
        if self.training and self.best_response_mode:
            sample = PolicySample(tuple(state), mask, int(choice))
            if self.policy_sink is not None:
                self.policy_sink(sample)
            else:
                self.sl_buffer.add(sample, self.rng)
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
        state_tensor = torch.tensor([state], dtype=torch.float32, device=self.device)
        mask_tensor = torch.tensor([mask], dtype=torch.bool, device=self.device)
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
        if len(self.rl_buffer) >= self.batch_size:
            metrics["q_loss"] = self._train_q()
        if len(self.sl_buffer) >= self.batch_size:
            metrics["policy_loss"] = self._train_policy()
        self.train_steps += 1
        if self.train_steps % self.config.target_update_steps == 0:
            self.target_network.load_state_dict(self.q_network.state_dict())
        return metrics

    def _train_q(self) -> float:
        batch = self.rl_buffer.sample(self.batch_size, self.rng)
        device = self.device
        states = torch.tensor([item.state for item in batch], dtype=torch.float32, device=device)
        actions = torch.tensor([item.action for item in batch], dtype=torch.int64, device=device)
        rewards = torch.tensor(
            [item.reward / self.config.reward_scale for item in batch],
            dtype=torch.float32,
            device=device,
        )
        done = torch.tensor(
            [item.next_state is None for item in batch], dtype=torch.bool, device=device
        )
        next_states = torch.tensor(
            [item.next_state or (0.0,) * self.encoder.feature_size for item in batch],
            dtype=torch.float32,
            device=device,
        )
        next_masks = torch.tensor(
            [item.next_mask or (True,) * ACTION_COUNT for item in batch],
            dtype=torch.bool,
            device=device,
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
        batch = self.sl_buffer.sample(self.batch_size, self.rng)
        device = self.device
        states = torch.tensor([item.state for item in batch], dtype=torch.float32, device=device)
        masks = torch.tensor([item.mask for item in batch], dtype=torch.bool, device=device)
        actions = torch.tensor([item.action for item in batch], dtype=torch.int64, device=device)
        logits = _masked(self.policy_network(states), masks)
        loss = nn.functional.cross_entropy(logits, actions)
        self.policy_optimizer.zero_grad()
        loss.backward()  # type: ignore[no-untyped-call]
        self.policy_optimizer.step()
        return float(loss.item())

    def to_bundle(self, metadata: dict[str, object] | None = None) -> bytes:
        entries = {
            MANIFEST: json_bytes(
                {
                    "action_schema": ACTION_SCHEMA,
                    "algorithm": "nfsp",
                    "config": asdict(self.config),
                    "metadata": metadata or {},
                    "observation_schema": self.encoder.schema,
                    "schema_version": BUNDLE_SCHEMA,
                    "train_steps": self.train_steps,
                }
            ),
            INFERENCE: st_save(_prefixed("policy", self.policy_network.state_dict())),
            ACTOR: st_save(
                {
                    **_prefixed("q", self.q_network.state_dict()),
                    **_prefixed("policy", self.policy_network.state_dict()),
                }
            ),
            TRAINING_WEIGHTS: st_save(
                {
                    **_prefixed("target", self.target_network.state_dict()),
                    **_optimizer_tensors("q_opt", self.q_optimizer),
                    **_optimizer_tensors("policy_opt", self.policy_optimizer),
                }
            ),
            TRAINING_STATE: json_bytes(
                {
                    "q_opt_groups": _optimizer_groups(self.q_optimizer),
                    "policy_opt_groups": _optimizer_groups(self.policy_optimizer),
                    "reservoir_seen": self.sl_buffer.seen,
                    "rng_state": _rng_state(self.rng),
                }
            ),
            RESERVOIR_STATES: array_bytes(
                np.asarray([item.state for item in self.sl_buffer.items], dtype=np.float32)
                .reshape(len(self.sl_buffer), self.encoder.feature_size)
            ),
            RESERVOIR_MASKS: array_bytes(
                np.asarray([item.mask for item in self.sl_buffer.items], dtype=np.bool_)
                .reshape(len(self.sl_buffer), ACTION_COUNT)
            ),
            RESERVOIR_ACTIONS: array_bytes(
                np.asarray([item.action for item in self.sl_buffer.items], dtype=np.int8)
            ),
        }
        return pack_entries(entries)

    @classmethod
    def from_bundle(
        cls, content: bytes, *, inference_only: bool = False, device: str = "cpu"
    ) -> NfspAgent:
        entries = unpack_entries(content, BUNDLE_ENTRIES)
        missing = REQUIRED_ENTRIES - set(entries)
        if missing:
            raise ValueError(f"checkpoint bundle is missing entries: {sorted(missing)}")
        manifest = json.loads(entries[MANIFEST])
        if manifest.get("schema_version") != BUNDLE_SCHEMA:
            raise ValueError("unsupported checkpoint bundle schema")
        if manifest["action_schema"] != ACTION_SCHEMA:
            raise ValueError("unsupported action schema")
        agent = cls(_config_from_manifest(manifest), device=device)
        agent.train_steps = int(manifest["train_steps"])
        if inference_only:
            weights = st_load(entries[INFERENCE])
            agent.policy_network.load_state_dict(_unprefixed("policy", weights))
            agent.training = False
            return agent
        actor = st_load(entries[ACTOR])
        agent.q_network.load_state_dict(_unprefixed("q", actor))
        agent.policy_network.load_state_dict(_unprefixed("policy", actor))
        training = st_load(entries[TRAINING_WEIGHTS])
        agent.target_network.load_state_dict(_unprefixed("target", training))
        state = json.loads(entries[TRAINING_STATE])
        _load_optimizer(agent.q_optimizer, "q_opt", training, state["q_opt_groups"])
        _load_optimizer(
            agent.policy_optimizer, "policy_opt", training, state["policy_opt_groups"]
        )
        states = array_from_bytes(entries[RESERVOIR_STATES])
        masks = array_from_bytes(entries[RESERVOIR_MASKS])
        actions = array_from_bytes(entries[RESERVOIR_ACTIONS])
        agent.sl_buffer.items = [
            PolicySample(
                tuple(float(value) for value in states[index]),
                tuple(bool(value) for value in masks[index]),
                int(actions[index]),
            )
            for index in range(len(actions))
        ]
        agent.sl_buffer.seen = int(state["reservoir_seen"])
        agent.rng.setstate(_restore_rng_state(state["rng_state"]))
        return agent

    @classmethod
    def from_actor_bundle(cls, content: bytes, device: str = "cpu") -> NfspAgent:
        entries = unpack_entries(content, BUNDLE_ENTRIES)
        manifest = json.loads(entries[MANIFEST])
        agent = cls(_config_from_manifest(manifest), device=device)
        actor = st_load(entries[ACTOR])
        agent.q_network.load_state_dict(_unprefixed("q", actor))
        agent.policy_network.load_state_dict(_unprefixed("policy", actor))
        agent.train_steps = int(manifest["train_steps"])
        return agent

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "checkpoint.zip"
        path.write_bytes(self.to_bundle())
        return path

    @classmethod
    def load_inference(cls, directory: Path) -> NfspAgent:
        return cls.from_bundle((directory / "checkpoint.zip").read_bytes(), inference_only=True)


BUNDLE_SCHEMA = "tableforge.nfsp-bundle.v2"
MANIFEST = "manifest.json"
INFERENCE = "inference.safetensors"
ACTOR = "actor.safetensors"
TRAINING_WEIGHTS = "training.safetensors"
TRAINING_STATE = "training.json"
RESERVOIR_STATES = "reservoir_states.npy"
RESERVOIR_MASKS = "reservoir_masks.npy"
RESERVOIR_ACTIONS = "reservoir_actions.npy"
BUNDLE_ENTRIES = frozenset(
    {
        MANIFEST,
        INFERENCE,
        ACTOR,
        TRAINING_WEIGHTS,
        TRAINING_STATE,
        RESERVOIR_STATES,
        RESERVOIR_MASKS,
        RESERVOIR_ACTIONS,
    }
)
REQUIRED_ENTRIES = BUNDLE_ENTRIES


def _config_from_manifest(manifest: dict[str, Any]) -> NfspConfig:
    """Rebuild the config; checkpoints from before v2 have no schema field and are v1."""
    config = dict(manifest["config"])
    config.setdefault("observation_schema", DEFAULT_SCHEMA)
    config.setdefault("reward_scale", 1.0)
    if manifest.get("observation_schema", DEFAULT_SCHEMA) != config["observation_schema"]:
        raise ValueError("checkpoint observation schema does not match its config")
    encoder(config["observation_schema"])
    return NfspConfig(**config)


def checkpoint_manifest(content: bytes) -> dict[str, Any]:
    entries = unpack_entries(content, BUNDLE_ENTRIES)
    manifest = json.loads(entries[MANIFEST])
    if not isinstance(manifest, dict):
        raise ValueError("checkpoint manifest must be an object")
    return manifest


def _optimizer_tensors(prefix: str, optimizer: torch.optim.Optimizer) -> dict[str, Tensor]:
    tensors: dict[str, Tensor] = {}
    for index, values in optimizer.state_dict()["state"].items():
        for name, value in values.items():
            if isinstance(value, Tensor):
                tensors[f"{prefix}.{index}.{name}"] = (
                    value.detach().to("cpu").clone().contiguous()
                )
    return tensors


def _optimizer_groups(optimizer: torch.optim.Optimizer) -> list[dict[str, Any]]:
    groups = []
    for group in optimizer.state_dict()["param_groups"]:
        groups.append(
            {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in group.items()
            }
        )
    return groups


def _load_optimizer(
    optimizer: torch.optim.Optimizer,
    prefix: str,
    tensors: dict[str, Tensor],
    groups: list[dict[str, Any]],
) -> None:
    state: dict[int, dict[str, Tensor]] = {}
    marker = f"{prefix}."
    device = next(
        (parameter.device for group in optimizer.param_groups for parameter in group["params"]),
        torch.device("cpu"),
    )
    for key, value in tensors.items():
        if not key.startswith(marker):
            continue
        index, name = key.removeprefix(marker).split(".", 1)
        state.setdefault(int(index), {})[name] = value if name == "step" else value.to(device)
    restored_groups = [
        {key: tuple(value) if key == "betas" else value for key, value in group.items()}
        for group in groups
    ]
    optimizer.load_state_dict({"state": state, "param_groups": restored_groups})


def _rng_state(rng: random.Random) -> list[Any]:
    version, internal, gauss = rng.getstate()
    return [version, list(internal), gauss]


def _restore_rng_state(value: list[Any]) -> tuple[Any, ...]:
    return (value[0], tuple(value[1]), value[2])


def _prefixed(prefix: str, state: dict[str, Any]) -> dict[str, Tensor]:
    return {
        f"{prefix}.{key}": value.detach().to("cpu").clone().contiguous()
        for key, value in state.items()
    }


def _unprefixed(prefix: str, state: dict[str, Tensor]) -> dict[str, Tensor]:
    marker = f"{prefix}."
    return {
        key.removeprefix(marker): value
        for key, value in state.items()
        if key.startswith(marker)
    }
