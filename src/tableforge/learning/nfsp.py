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

from tableforge.learning.actions import (
    ACTION_COUNT,
    ACTION_SCHEMAS,
    AbstractAction,
    Actions,
    ActionTranslator,
    Spot,
)
from tableforge.learning.actions import SCHEMA as ACTION_SCHEMA
from tableforge.learning.buffers import PolicySample
from tableforge.learning.bundle import (
    array_bytes,
    array_from_bytes,
    json_bytes,
    pack_entries,
    unpack_entries,
)
from tableforge.learning.encoders import DEFAULT_SCHEMA, Encoder, encoder
from tableforge.learning.encoding_v2 import TournamentContext
from tableforge.learning.encoding_v3 import SCHEMA as STRUCTURE_SCHEMA
from tableforge.learning.encoding_v3 import StructureContext
from tableforge.learning.encoding_v4 import SCHEMA as STAGE_SCHEMA
from tableforge.learning.encoding_v4 import FieldContext, StageContext
from tableforge.learning.memory import PolicyReservoir, TransitionMemory
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.observation import Observation
from tableforge.poker.table import TableObservation
from tableforge.poker.tournament import TournamentHandInfo


def tournament_context(info: TournamentHandInfo) -> TournamentContext:
    """What the v2 encoder's tournament fields need; bounties are relative to the start."""
    scale = info.starting_bounty if info.starting_bounty > 0 else 1.0
    return TournamentContext(
        starting_stack=info.starting_stack,
        level=info.level,
        hands_to_next_level=info.hands_to_next_level,
        hands_per_level=info.hands_per_level,
        players_left=info.players_left,
        ante=info.ante,
        payout=info.payout,
        bounties=tuple(value / scale for value in info.bounties),
    )


def structure_context(info: TournamentHandInfo) -> StructureContext:
    """What the v3 encoder's structure fields need.

    In an MTT stage (T5) the payout is the field's payout seen from our table (the table
    ladder in `info.prizes`, as shares of its total) and the field size is our table's.
    """
    scale = info.starting_bounty if info.starting_bounty > 0 else 1.0
    payout = info.payout
    if info.stage is not None and info.prizes:
        total = sum(info.prizes)
        payout = tuple(prize / total for prize in info.prizes) if total > 0 else ()
    return StructureContext(
        starting_stack=info.starting_stack,
        level=info.level,
        hands_to_next_level=info.hands_to_next_level,
        hands_per_level=info.hands_per_level,
        players_left=info.players_left,
        field_size=info.field_size or info.players_left,
        ante=info.ante,
        payout=payout,
        bounties=tuple(value / scale for value in info.bounties),
        bounty_share=info.bounty_share,
        cash_share=info.bounty_cash_share if info.bounties else 0.0,
        blind_growth=info.blind_growth or 1.25,
    )


def stage_context(info: TournamentHandInfo) -> StageContext:
    """What the v4 encoder needs: v3's structure plus the field around our table."""
    stage = info.stage
    field = (
        None
        if stage is None
        else FieldContext(
            field_size=stage.field_size,
            field_left=stage.field_left,
            paid=stage.paid,
            field_average_stack=stage.field_average_stack,
            min_cash=stage.min_cash,
            next_prize=stage.next_prize,
        )
    )
    return StageContext(structure_context(info), field)


def hand_context(schema: str, info: TournamentHandInfo) -> object:
    """The tournament context an encoder of this schema takes."""
    if schema == STAGE_SCHEMA:
        return stage_context(info)
    if schema == STRUCTURE_SCHEMA:
        return structure_context(info)
    return tournament_context(info)


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
    double_q: bool = False
    """Double DQN targets: the online network picks the next action, the target network
    values it. Reduces the overestimation that builds up over long episodes."""
    reward_scale: float = 1.0
    """Rewards (in big blinds) are divided by this before Q learning.

    The Huber loss is linear for errors above 1, which makes Q estimate something close to
    the median return instead of the mean when rewards are tens of big blinds. Scaling by
    the stack depth (e.g. 100) keeps errors in the quadratic range. Actions are unchanged
    because every Q value is scaled the same way.
    """
    action_schema: str = ACTION_SCHEMA
    """Which abstract actions the networks output (`learning.actions`)."""


def _config_dict(config: NfspConfig) -> dict[str, Any]:
    """The config as stored in checkpoints; the default action schema is left out so
    checkpoints written before it existed (and their sha256) are unchanged."""
    data = asdict(config)
    if data["action_schema"] == ACTION_SCHEMA:
        del data["action_schema"]
    return data


def _network(inputs: int, hidden: int, outputs: int = ACTION_COUNT) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(inputs, hidden),
        nn.LayerNorm(hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
        nn.ReLU(),
        nn.Linear(hidden, outputs),
    )


def _device(values: Any, device: torch.device) -> Tensor:
    return torch.from_numpy(np.ascontiguousarray(values)).to(device)


def _tensor(values: Sequence[Any], dtype: type, device: torch.device) -> Tensor:
    """A tensor from Python values, built through NumPy (much faster than torch.tensor)."""
    return torch.from_numpy(np.asarray(values, dtype=dtype)).to(device)


def _floats(values: Sequence[Any], device: torch.device) -> Tensor:
    return _tensor(values, np.float32, device)


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
        self.actions = Actions(config.action_schema)
        inputs = self.encoder.feature_size
        outputs = self.actions.count
        self.q_network = _network(inputs, config.hidden_size, outputs).to(self.device)
        self.target_network = _network(inputs, config.hidden_size, outputs).to(self.device)
        self.target_network.load_state_dict(self.q_network.state_dict())
        self.policy_network = _network(inputs, config.hidden_size, outputs).to(self.device)
        self.q_optimizer = torch.optim.Adam(self.q_network.parameters(), config.learning_rate)
        self.policy_optimizer = torch.optim.Adam(
            self.policy_network.parameters(), config.learning_rate
        )
        self.rl_buffer = TransitionMemory(config.rl_capacity, inputs, outputs)
        self.sl_buffer = PolicyReservoir(config.sl_capacity, inputs, outputs)
        self.translator = ActionTranslator()
        self.training = True
        self.best_response_mode = False
        self.train_steps = 0
        self.batch_size = config.batch_size
        self.last_choice: int | None = None
        self.policy_sink: Callable[[PolicySample], None] | None = None
        self.hand_info: TournamentHandInfo | None = None
        self.last_state: tuple[tuple[float, ...], tuple[bool, ...]] | None = None
        """Encoded state and legal mask of the last decision."""

    def start_hand(self) -> None:
        self.best_response_mode = self.training and self.rng.random() < self.config.anticipatory

    def begin_hand(self, info: TournamentHandInfo) -> None:
        """Tournament hook (`TournamentAware`): blinds and tournament state of this hand."""
        if not self.encoder.supports_tables:
            raise ValueError(f"{self.encoder.schema} cannot play tournaments")
        if len(info.seat_players) > self.encoder.max_players:
            raise ValueError(
                f"this model ({self.encoder.schema}) has seats for "
                f"{self.encoder.max_players} players, not {len(info.seat_players)}"
            )
        self.hand_info = info

    def encode(self, observation: Observation | TableObservation) -> list[float]:
        info = self.hand_info
        if info is None:
            return self.encoder.encode(observation, self.config.big_blind)
        context = hand_context(self.encoder.schema, info)
        return self.encoder.encode(observation, info.big_blind, info.small_blind, context)

    def legal_mask(
        self, observation: Observation | TableObservation, legal: LegalActions
    ) -> tuple[bool, ...]:
        if self.actions.schema == ACTION_SCHEMA:
            return self.translator.legal_mask(legal)
        return self.actions.legal_mask(legal, observation.street)

    def spot(self, observation: Observation | TableObservation) -> Spot:
        me = observation.players[int(observation.observer)]
        big_blind = (
            self.hand_info.big_blind if self.hand_info is not None else self.config.big_blind
        )
        return Spot(
            observation.street,
            observation.pot,
            observation.current_bet,
            me.street_contribution,
            big_blind,
        )

    def act(self, observation: Observation | TableObservation, legal: LegalActions) -> Action:
        state = self.encode(observation)
        mask = self.legal_mask(observation, legal)
        choice = self.choose(state, mask)
        self.last_choice = choice
        self.last_state = (tuple(state), mask)
        if self.training and self.best_response_mode:
            sample = PolicySample(tuple(state), mask, int(choice))
            if self.policy_sink is not None:
                self.policy_sink(sample)
            else:
                self.sl_buffer.add(sample, self.rng)
        if self.actions.schema != ACTION_SCHEMA:
            return self.actions.to_action(int(choice), legal, self.spot(observation))
        me = observation.players[int(observation.observer)]
        return self.translator.to_action(
            AbstractAction(choice),
            legal,
            observation.pot,
            observation.current_bet,
            me.street_contribution,
        )

    def choose(self, state: Sequence[float], mask: tuple[bool, ...]) -> int:
        """Index of the chosen abstract action (an `AbstractAction` for the v1 set)."""
        legal = [index for index, allowed in enumerate(mask) if allowed]
        if not legal:
            raise ValueError("no legal abstract actions")
        state_tensor = torch.tensor([state], dtype=torch.float32, device=self.device)
        mask_tensor = torch.tensor([mask], dtype=torch.bool, device=self.device)
        wrap = AbstractAction if self.actions.schema == ACTION_SCHEMA else int
        with torch.no_grad():
            if self.best_response_mode:
                if self.training and self.rng.random() < self.config.epsilon:
                    return wrap(self.rng.choice(legal))
                values = _masked(self.q_network(state_tensor), mask_tensor)
                return wrap(int(values.argmax(dim=1).item()))
            logits = _masked(self.policy_network(state_tensor), mask_tensor)
            probabilities = torch.softmax(logits, dim=1)[0].tolist()
        return wrap(self.rng.choices(range(self.actions.count), probabilities)[0])

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
        states = _device(batch.states, device)
        actions = _device(batch.actions, device)
        # Divide in float64 before converting, exactly as with Python floats.
        rewards = _device((batch.rewards / self.config.reward_scale).astype(np.float32), device)
        done = _device(batch.done, device)
        next_states = _device(batch.next_states, device)
        next_masks = _device(batch.next_masks, device)
        with torch.no_grad():
            target_values = _masked(self.target_network(next_states), next_masks)
            if self.config.double_q:
                chosen = _masked(self.q_network(next_states), next_masks).argmax(dim=1)
                next_values = target_values.gather(1, chosen.unsqueeze(1)).squeeze(1)
            else:
                next_values = target_values.max(dim=1).values
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
        states = _device(batch.states, device)
        masks = _device(batch.masks, device)
        actions = _device(batch.actions, device)
        logits = _masked(self.policy_network(states), masks)
        loss = nn.functional.cross_entropy(logits, actions)
        self.policy_optimizer.zero_grad()
        loss.backward()  # type: ignore[no-untyped-call]
        self.policy_optimizer.step()
        return float(loss.item())

    def to_bundle(self, metadata: dict[str, object] | None = None) -> bytes:
        reservoir_states, reservoir_masks, reservoir_actions = self.sl_buffer.arrays()
        entries = {
            MANIFEST: json_bytes(
                {
                    "action_schema": self.actions.schema,
                    "algorithm": "nfsp",
                    "config": _config_dict(self.config),
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
            RESERVOIR_STATES: array_bytes(np.ascontiguousarray(reservoir_states)),
            RESERVOIR_MASKS: array_bytes(np.ascontiguousarray(reservoir_masks)),
            RESERVOIR_ACTIONS: array_bytes(reservoir_actions.astype(np.int8)),
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
        if manifest["action_schema"] not in ACTION_SCHEMAS:
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
        _load_optimizer(agent.policy_optimizer, "policy_opt", training, state["policy_opt_groups"])
        states = array_from_bytes(entries[RESERVOIR_STATES])
        masks = array_from_bytes(entries[RESERVOIR_MASKS])
        actions = array_from_bytes(entries[RESERVOIR_ACTIONS])
        agent.sl_buffer.load(states, masks, actions, int(state["reservoir_seen"]))
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
    config.setdefault("double_q", False)
    config.setdefault("action_schema", manifest.get("action_schema", ACTION_SCHEMA))
    if manifest.get("observation_schema", DEFAULT_SCHEMA) != config["observation_schema"]:
        raise ValueError("checkpoint observation schema does not match its config")
    if manifest.get("action_schema", ACTION_SCHEMA) != config["action_schema"]:
        raise ValueError("checkpoint action schema does not match its config")
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
                tensors[f"{prefix}.{index}.{name}"] = value.detach().to("cpu").clone().contiguous()
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
        key.removeprefix(marker): value for key, value in state.items() if key.startswith(marker)
    }
