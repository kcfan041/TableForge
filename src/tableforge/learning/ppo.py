"""PPO self-play (A1): a policy network and a value network trained on whole tournaments.

PPO reuses NFSP's pieces: observation encoders (v2, v3), action sets (v1, v2), the
tournament reward (the change in tournament equity, see `tournament_training`), opponent
pools and the round pipeline (actors, learner, evaluation). The learning rule differs:

* actors play with the round's checkpoint (on-policy) and record each learning seat's
  decisions in order: state, legal mask, action, reward, next state (none at the end of an
  episode: the seat's tournament, or the hand with `hand_horizon`);
* the learner recomputes the behaviour log-probabilities and values with that same
  checkpoint (the pipeline checks the experience came from it), computes advantages with
  GAE, and runs a few epochs of clipped PPO updates on shuffled minibatches.

Play modes: ``sample`` (draw from the policy, the default) and ``greedy`` (always the most
likely action). Explanations give the policy's probabilities and the state value V(s).
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import torch
from numpy.typing import NDArray
from safetensors.torch import load as st_load
from safetensors.torch import save as st_save
from torch import Tensor, nn

from tableforge.learning.actions import ACTION_SCHEMAS, Actions, Spot
from tableforge.learning.actions import SCHEMA as V1_ACTIONS
from tableforge.learning.buffers import Transition
from tableforge.learning.bundle import json_bytes, pack_entries, unpack_entries
from tableforge.learning.encoders import Encoder, encoder
from tableforge.learning.nfsp import (
    MASKED,
    _load_optimizer,
    _optimizer_groups,
    _optimizer_tensors,
    _prefixed,
    _restore_rng_state,
    _rng_state,
    _unprefixed,
    hand_context,
)
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo

BUNDLE_SCHEMA = "tableforge.ppo-bundle.v1"
CHECKPOINT_SCHEMA = "tableforge.ppo-checkpoint.v1"
MANIFEST = "manifest.json"
WEIGHTS = "weights.safetensors"
TRAINING_WEIGHTS = "training.safetensors"
TRAINING_STATE = "training.json"
BUNDLE_ENTRIES = frozenset({MANIFEST, WEIGHTS, TRAINING_WEIGHTS, TRAINING_STATE})


@dataclass(frozen=True, slots=True)
class PpoConfig:
    hidden_size: int = 128
    learning_rate: float = 3e-4
    gamma: float = 1.0
    gae_lambda: float = 0.95
    clip: float = 0.2
    epochs: int = 4
    minibatch_size: int = 1024
    value_coef: float = 0.5
    entropy_coef: float = 0.01
    max_grad_norm: float = 0.5
    reward_scale: float = 0.3
    """Rewards (buy-ins) are divided by this before learning (as in NFSP)."""
    observation_schema: str = "poker-hunlhe-vector-v2"
    action_schema: str = V1_ACTIONS
    seed: int = 0
    magnet_coef: float = 0.0
    """MMD (magnetic mirror descent): weight of KL(policy || magnet) in the loss; 0 is
    plain PPO. The penalty keeps self-play from cycling between strategies."""
    magnet_every: int = 10
    """MMD: the magnet becomes the current policy after this many updates (learn calls)."""
    value_mode: str = "shared"
    """``shared``: one optimizer and gradient limit for both networks, Huber value loss (as
    first implemented). ``separate``: the value network has its own optimizer
    (`value_learning_rate`, `value_max_grad_norm`), a squared-error loss (it learns the
    mean return, not a median) and `value_epochs` passes per update. The A1 diagnostics
    found the shared value net explaining only 5-15% of returns."""
    value_learning_rate: float = 1e-3
    value_max_grad_norm: float = 5.0
    value_epochs: int = 8

    def __post_init__(self) -> None:
        if self.hidden_size < 1 or self.minibatch_size < 1 or self.epochs < 1:
            raise ValueError("hidden_size, minibatch_size and epochs must be positive")
        if self.value_mode not in VALUE_MODES:
            raise ValueError(f"value_mode must be one of {', '.join(VALUE_MODES)}")
        if not 0 < self.value_learning_rate <= 1 or self.value_max_grad_norm <= 0:
            raise ValueError("invalid value network settings")
        if self.value_epochs < 1:
            raise ValueError("value_epochs must be positive")
        if self.magnet_coef < 0 or self.magnet_every < 1:
            raise ValueError("magnet_coef cannot be negative and magnet_every must be positive")
        if not 0 < self.learning_rate <= 1 or self.reward_scale <= 0:
            raise ValueError("learning_rate and reward_scale must be positive")
        if not 0 < self.clip < 1 or not 0 <= self.gae_lambda <= 1 or not 0 <= self.gamma <= 1:
            raise ValueError("clip, gae_lambda or gamma is out of range")
        if self.entropy_coef < 0 or self.value_coef < 0 or self.max_grad_norm <= 0:
            raise ValueError("coefficients cannot be negative")
        if self.action_schema not in ACTION_SCHEMAS:
            raise ValueError(f"unsupported action schema: {self.action_schema}")
        encoder(self.observation_schema)


VALUE_MODES = ("shared", "separate")
_OPTIONAL_DEFAULTS: dict[str, object] = {
    "magnet_coef": 0.0,
    "magnet_every": 10,
    "value_mode": "shared",
    "value_learning_rate": 1e-3,
    "value_max_grad_norm": 5.0,
    "value_epochs": 8,
}


def _config_dict(config: PpoConfig) -> dict[str, Any]:
    """The config as stored in checkpoints; settings added after the first PPO release are
    written only when they are not the defaults, so older checkpoints keep their bytes."""
    data = asdict(config)
    for key, default in _OPTIONAL_DEFAULTS.items():
        if data[key] == default:
            del data[key]
    return data


def _network(inputs: int, hidden: int, outputs: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(inputs, hidden),
        nn.LayerNorm(hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
        nn.ReLU(),
        nn.Linear(hidden, outputs),
    )


@dataclass(frozen=True, slots=True)
class Rollout:
    """Decisions of complete episodes, each episode's decisions consecutive and in order."""

    states: NDArray[np.float32]
    masks: NDArray[np.bool_]
    actions: NDArray[np.int64]
    rewards: NDArray[np.float64]
    next_states: NDArray[np.float32]
    done: NDArray[np.bool_]

    def __post_init__(self) -> None:
        count = len(self.states)
        if count == 0:
            raise ValueError("no experience")
        lengths = {len(item) for item in (self.masks, self.actions, self.rewards, self.done)}
        if lengths != {count} or len(self.next_states) != count:
            raise ValueError("rollout arrays have different lengths")
        if not bool(self.done[-1]):
            raise ValueError("the last episode of a rollout is not finished")

    @classmethod
    def of(cls, transitions: Sequence[Transition], features: int, actions: int) -> Rollout:
        zero = np.zeros(features, dtype=np.float32)
        return cls(
            states=np.asarray([item.state for item in transitions], dtype=np.float32).reshape(
                len(transitions), features
            ),
            masks=np.asarray([item.mask for item in transitions], dtype=np.bool_).reshape(
                len(transitions), actions
            ),
            actions=np.asarray([item.action for item in transitions], dtype=np.int64),
            rewards=np.asarray([item.reward for item in transitions], dtype=np.float64),
            next_states=np.asarray(
                [zero if item.next_state is None else item.next_state for item in transitions],
                dtype=np.float32,
            ).reshape(len(transitions), features),
            done=np.asarray([item.next_state is None for item in transitions], dtype=np.bool_),
        )

    @classmethod
    def concat(cls, parts: Sequence[Rollout]) -> Rollout:
        return cls(
            states=np.concatenate([part.states for part in parts]),
            masks=np.concatenate([part.masks for part in parts]),
            actions=np.concatenate([part.actions for part in parts]),
            rewards=np.concatenate([part.rewards for part in parts]),
            next_states=np.concatenate([part.next_states for part in parts]),
            done=np.concatenate([part.done for part in parts]),
        )


def gae(
    rewards: NDArray[np.float64],
    values: NDArray[np.float64],
    next_values: NDArray[np.float64],
    done: NDArray[np.bool_],
    gamma: float,
    lam: float,
) -> NDArray[np.float64]:
    """Generalised advantage estimates of consecutive decisions of complete episodes.

    ``next_values[i]`` is V of decision i's next state; ``done[i]`` ends the episode after
    decision i (no bootstrap, and the next decision belongs to another episode).
    """
    advantages = np.zeros(len(rewards), dtype=np.float64)
    running = 0.0
    for index in range(len(rewards) - 1, -1, -1):
        if done[index]:
            delta = rewards[index] - values[index]
            running = delta
        else:
            delta = rewards[index] + gamma * next_values[index] - values[index]
            running = delta + gamma * lam * running
        advantages[index] = running
    return advantages


def clipped_loss(
    log_probs: Tensor, old_log_probs: Tensor, advantages: Tensor, clip: float
) -> Tensor:
    """PPO's clipped surrogate objective, as a loss to minimise."""
    ratio = torch.exp(log_probs - old_log_probs)
    clipped = torch.clamp(ratio, 1 - clip, 1 + clip)
    return -torch.min(ratio * advantages, clipped * advantages).mean()


def _grad_norm(network: nn.Module) -> float:
    total = 0.0
    for parameter in network.parameters():
        if parameter.grad is not None:
            total += float(parameter.grad.detach().pow(2).sum().item())
    return float(total**0.5)


def magnet_kl(log_probs: Tensor, magnet_log_probs: Tensor, legal: Tensor) -> Tensor:
    """Mean KL(policy || magnet) over the legal actions of each state."""
    terms = log_probs.exp() * (log_probs - magnet_log_probs)
    return terms.masked_fill(~legal, 0.0).sum(dim=1).mean()


class PpoAgent:
    """Policy and value networks; plays through the same seats as `NfspAgent`."""

    def __init__(self, config: PpoConfig, device: str = "cpu") -> None:
        torch.manual_seed(config.seed)
        self.config = config
        self.device = torch.device(device)
        self.rng = random.Random(config.seed)
        self.encoder: Encoder = encoder(config.observation_schema)
        self.actions = Actions(config.action_schema)
        inputs = self.encoder.feature_size
        self.policy_network = _network(inputs, config.hidden_size, self.actions.count).to(
            self.device
        )
        self.value_network = _network(inputs, config.hidden_size, 1).to(self.device)
        self.magnet_network: nn.Sequential | None = None
        self.updates = 0
        """PPO updates (`learn` calls) so far; the magnet moves every `magnet_every`."""
        self.last_diagnostics: dict[str, float] = {}
        """Read-only numbers about the last update (`learn`): approx_kl, clip_fraction,
        explained_variance(_after), advantage_std, return_mean, gradient norms."""
        if config.magnet_coef > 0:
            self.magnet_network = _network(inputs, config.hidden_size, self.actions.count).to(
                self.device
            )
            self.magnet_network.load_state_dict(self.policy_network.state_dict())
        self.value_optimizer: torch.optim.Optimizer | None = None
        if config.value_mode == "separate":
            self.optimizer = torch.optim.Adam(
                self.policy_network.parameters(), config.learning_rate
            )
            self.value_optimizer = torch.optim.Adam(
                self.value_network.parameters(), config.value_learning_rate
            )
        else:
            self.optimizer = torch.optim.Adam(
                [*self.policy_network.parameters(), *self.value_network.parameters()],
                config.learning_rate,
            )
        self.training = True
        self.greedy = False
        self.train_steps = 0
        self.hand_info: TournamentHandInfo | None = None
        self.last_choice: int | None = None
        self.last_state: tuple[tuple[float, ...], tuple[bool, ...]] | None = None

    # --- acting -----------------------------------------------------------------------

    def start_hand(self) -> None:
        """PPO explores by sampling its own policy; nothing changes between hands."""

    def begin_hand(self, info: TournamentHandInfo) -> None:
        if not self.encoder.supports_tables:
            raise ValueError(f"{self.encoder.schema} cannot play tournaments")
        if len(info.seat_players) > self.encoder.max_players:
            raise ValueError(
                f"this model ({self.encoder.schema}) has seats for "
                f"{self.encoder.max_players} players, not {len(info.seat_players)}"
            )
        self.hand_info = info

    def encode(self, observation: TableObservation) -> list[float]:
        info = self.hand_info
        if info is None:
            raise ValueError("PPO models play tournaments; begin_hand was not called")
        context = hand_context(self.encoder.schema, info)
        return self.encoder.encode(observation, info.big_blind, info.small_blind, context)

    def legal_mask(self, observation: TableObservation, legal: LegalActions) -> tuple[bool, ...]:
        return self.actions.legal_mask(legal, observation.street)

    def evaluate_state(
        self, state: Sequence[float], mask: Sequence[bool]
    ) -> tuple[list[float], float]:
        """Policy probabilities of every abstract action, and the state value V(s)."""
        state_tensor = torch.from_numpy(np.asarray([state], dtype=np.float32)).to(self.device)
        mask_tensor = torch.from_numpy(np.asarray([mask], dtype=np.bool_)).to(self.device)
        with torch.no_grad():
            logits = self.policy_network(state_tensor).masked_fill(~mask_tensor, MASKED)
            probabilities = torch.softmax(logits, dim=1)[0].tolist()
            value = float(self.value_network(state_tensor)[0, 0].item())
        return probabilities, value

    def choose(self, state: Sequence[float], mask: tuple[bool, ...]) -> int:
        if not any(mask):
            raise ValueError("no legal abstract actions")
        probabilities, _ = self.evaluate_state(state, mask)
        if self.greedy:
            return max(
                (index for index, allowed in enumerate(mask) if allowed),
                key=lambda index: probabilities[index],
            )
        return self.rng.choices(range(self.actions.count), probabilities)[0]

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        state = self.encode(observation)
        mask = self.legal_mask(observation, legal)
        choice = self.choose(state, mask)
        self.last_choice = choice
        self.last_state = (tuple(state), mask)
        info = self.hand_info
        assert info is not None
        me = observation.players[int(observation.observer)]
        spot = Spot(
            observation.street,
            observation.pot,
            observation.current_bet,
            me.street_contribution,
            info.big_blind,
        )
        return self.actions.to_action(choice, legal, spot)

    # --- learning ---------------------------------------------------------------------

    def _forward(self, states: Tensor, masks: Tensor) -> tuple[Tensor, Tensor]:
        logits = self.policy_network(states).masked_fill(~masks, MASKED)
        return torch.log_softmax(logits, dim=1), self.value_network(states).squeeze(1)

    def learn(
        self,
        rollout: Rollout,
        epochs: int | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> dict[str, float]:
        """Clipped PPO on one batch of on-policy experience (collected with these weights)."""
        config = self.config
        device = self.device
        states = torch.from_numpy(rollout.states).to(device)
        masks = torch.from_numpy(rollout.masks).to(device)
        actions = torch.from_numpy(rollout.actions).to(device)
        with torch.no_grad():
            log_probs, values = self._forward(states, masks)
            log_probs_before = log_probs
            old_log_probs = log_probs.gather(1, actions.unsqueeze(1)).squeeze(1)
            next_values = self.value_network(torch.from_numpy(rollout.next_states).to(device))
        value_array = values.cpu().numpy().astype(np.float64)
        advantages_array = gae(
            rollout.rewards / config.reward_scale,
            value_array,
            next_values.squeeze(1).cpu().numpy().astype(np.float64),
            rollout.done,
            config.gamma,
            config.gae_lambda,
        )
        returns = torch.from_numpy((advantages_array + value_array).astype(np.float32)).to(device)
        advantages = torch.from_numpy(advantages_array.astype(np.float32)).to(device)
        if len(advantages) > 1:
            advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        magnet_log_probs: Tensor | None = None
        if self.magnet_network is not None:
            with torch.no_grad():
                magnet_log_probs = torch.log_softmax(
                    self.magnet_network(states).masked_fill(~masks, MASKED), dim=1
                )
        parameters = [*self.policy_network.parameters(), *self.value_network.parameters()]
        generator = torch.Generator().manual_seed(self.rng.randrange(2**31))
        count = len(actions)
        metrics: dict[str, float] = {}
        # Diagnostics only (read-only, no effect on training): how far each update moves the
        # policy, how often the clip is active, and how well the value net explains returns.
        returns_array = advantages_array + value_array
        variance = float(np.var(returns_array))
        diagnostics: dict[str, float] = {
            "advantage_std": float(np.std(advantages_array)),
            "return_mean": float(np.mean(returns_array)),
            "explained_variance": (
                float(1.0 - np.var(returns_array - value_array) / variance)
                if variance > 1e-12
                else 0.0
            ),
            "decisions": float(count),
        }
        clipped_sum = 0.0
        batches = 0
        policy_grad = value_grad = 0.0
        separate = self.value_optimizer is not None
        if separate:
            parameters = list(self.policy_network.parameters())
        for epoch in range(epochs or config.epochs):
            order = torch.randperm(count, generator=generator).to(device)
            for start in range(0, count, config.minibatch_size):
                index = order[start : start + config.minibatch_size]
                log_probs, values = self._forward(states[index], masks[index])
                chosen = log_probs.gather(1, actions[index].unsqueeze(1)).squeeze(1)
                policy_loss = clipped_loss(
                    chosen, old_log_probs[index], advantages[index], config.clip
                )
                legal = masks[index]
                entropy = -(log_probs.exp() * log_probs).masked_fill(~legal, 0.0).sum(dim=1).mean()
                if separate:
                    value_loss = torch.zeros(())
                    loss = policy_loss - config.entropy_coef * entropy
                else:
                    value_loss = nn.functional.smooth_l1_loss(values, returns[index])
                    loss = (
                        policy_loss + config.value_coef * value_loss - config.entropy_coef * entropy
                    )
                kl: Tensor | None = None
                if magnet_log_probs is not None:
                    kl = magnet_kl(log_probs, magnet_log_probs[index], legal)
                    loss = loss + config.magnet_coef * kl
                self.optimizer.zero_grad()
                loss.backward()  # type: ignore[no-untyped-call]
                with torch.no_grad():
                    ratio = torch.exp(chosen - old_log_probs[index])
                    clipped_sum += float(((ratio - 1).abs() > config.clip).float().mean().item())
                    policy_grad += _grad_norm(self.policy_network)
                    value_grad += _grad_norm(self.value_network)
                    batches += 1
                nn.utils.clip_grad_norm_(parameters, config.max_grad_norm)
                self.optimizer.step()
                self.train_steps += 1
                metrics = {
                    "policy_loss": float(policy_loss.item()),
                    "value_loss": float(value_loss.item()),
                    "entropy": float(entropy.item()),
                }
                if kl is not None:
                    metrics["magnet_kl"] = float(kl.item())
            if on_progress is not None:
                on_progress(epoch + 1)
        if self.value_optimizer is not None:
            # The value network on its own: squared error (the mean return), its own
            # learning rate, gradient limit and number of passes.
            value_losses = []
            for _ in range(config.value_epochs):
                order = torch.randperm(count, generator=generator).to(device)
                for start in range(0, count, config.minibatch_size):
                    index = order[start : start + config.minibatch_size]
                    predicted = self.value_network(states[index]).squeeze(1)
                    value_loss = nn.functional.mse_loss(predicted, returns[index])
                    self.value_optimizer.zero_grad()
                    value_loss.backward()  # type: ignore[no-untyped-call]
                    value_grad += _grad_norm(self.value_network) / config.value_epochs
                    nn.utils.clip_grad_norm_(
                        self.value_network.parameters(), config.value_max_grad_norm
                    )
                    self.value_optimizer.step()
                    value_losses.append(float(value_loss.item()))
            metrics["value_loss"] = value_losses[-1]
        with torch.no_grad():
            new_log_probs, new_values = self._forward(states, masks)
            # KL(old || new) over legal actions, averaged over states.
            old_all = log_probs_before
            terms = old_all.exp() * (old_all - new_log_probs)
            diagnostics["approx_kl"] = float(
                terms.masked_fill(~masks, 0.0).sum(dim=1).mean().item()
            )
            new_value_array = new_values.cpu().numpy().astype(np.float64)
        diagnostics["explained_variance_after"] = (
            float(1.0 - np.var(returns_array - new_value_array) / variance)
            if variance > 1e-12
            else 0.0
        )
        diagnostics["clip_fraction"] = clipped_sum / max(batches, 1)
        diagnostics["policy_grad_norm"] = policy_grad / max(batches, 1)
        diagnostics["value_grad_norm"] = value_grad / max(batches, 1)
        self.last_diagnostics = diagnostics
        self.updates += 1
        if self.magnet_network is not None and self.updates % config.magnet_every == 0:
            self.magnet_network.load_state_dict(self.policy_network.state_dict())
        return metrics

    # --- checkpoints ------------------------------------------------------------------

    def to_bundle(self, metadata: dict[str, object] | None = None) -> bytes:
        return pack_entries(
            {
                MANIFEST: json_bytes(
                    {
                        "action_schema": self.actions.schema,
                        "algorithm": "ppo",
                        "config": _config_dict(self.config),
                        "metadata": metadata or {},
                        "observation_schema": self.encoder.schema,
                        "schema_version": BUNDLE_SCHEMA,
                        "train_steps": self.train_steps,
                    }
                ),
                WEIGHTS: st_save(
                    {
                        **_prefixed("policy", self.policy_network.state_dict()),
                        **_prefixed("value", self.value_network.state_dict()),
                    }
                ),
                TRAINING_WEIGHTS: st_save(
                    {
                        **_optimizer_tensors("opt", self.optimizer),
                        **(
                            _prefixed("magnet", self.magnet_network.state_dict())
                            if self.magnet_network is not None
                            else {}
                        ),
                        **(
                            _optimizer_tensors("value_opt", self.value_optimizer)
                            if self.value_optimizer is not None
                            else {}
                        ),
                    }
                    or {"empty": torch.zeros(1)}
                ),
                TRAINING_STATE: json_bytes(
                    {
                        "opt_groups": _optimizer_groups(self.optimizer),
                        "rng_state": _rng_state(self.rng),
                        **({"updates": self.updates} if self.magnet_network is not None else {}),
                        **(
                            {"value_opt_groups": _optimizer_groups(self.value_optimizer)}
                            if self.value_optimizer is not None
                            else {}
                        ),
                    }
                ),
            }
        )

    @classmethod
    def from_bundle(cls, content: bytes, device: str = "cpu") -> PpoAgent:
        entries = unpack_entries(content, BUNDLE_ENTRIES)
        if set(entries) != BUNDLE_ENTRIES:
            raise ValueError("PPO checkpoint is incomplete")
        manifest = json.loads(entries[MANIFEST])
        if manifest.get("schema_version") != BUNDLE_SCHEMA:
            raise ValueError("unsupported PPO checkpoint schema")
        agent = cls(PpoConfig(**manifest["config"]), device=device)
        agent.train_steps = int(manifest["train_steps"])
        weights = st_load(entries[WEIGHTS])
        agent.policy_network.load_state_dict(_unprefixed("policy", weights))
        agent.value_network.load_state_dict(_unprefixed("value", weights))
        state = json.loads(entries[TRAINING_STATE])
        tensors = {
            key: value
            for key, value in st_load(entries[TRAINING_WEIGHTS]).items()
            if key != "empty"
        }
        _load_optimizer(agent.optimizer, "opt", tensors, state["opt_groups"])
        if agent.value_optimizer is not None:
            _load_optimizer(agent.value_optimizer, "value_opt", tensors, state["value_opt_groups"])
        agent.rng.setstate(_restore_rng_state(state["rng_state"]))
        if agent.magnet_network is not None:
            magnet = _unprefixed("magnet", tensors)
            # Without stored magnet weights the magnet starts as the current policy.
            agent.magnet_network.load_state_dict(
                magnet if magnet else agent.policy_network.state_dict()
            )
            agent.updates = int(state.get("updates", 0))
        return agent


def checkpoint_manifest(content: bytes) -> dict[str, Any]:
    manifest = json.loads(unpack_entries(content, BUNDLE_ENTRIES)[MANIFEST])
    if not isinstance(manifest, dict) or manifest.get("schema_version") != BUNDLE_SCHEMA:
        raise ValueError("not a PPO checkpoint")
    return manifest


@dataclass
class PpoTablePlayer:
    """A PPO model at a table: samples its policy, or plays the most likely action."""

    agent: PpoAgent
    greedy: bool = False

    def __post_init__(self) -> None:
        self.agent.training = False

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.agent.begin_hand(info)

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        self.agent.greedy = self.greedy
        return self.agent.act(observation, legal)
