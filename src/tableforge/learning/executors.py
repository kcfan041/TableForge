from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.domain.artifacts import ArtifactKind
from tableforge.domain.learning import (
    CHECKPOINT_SCHEMA,
    EVALUATION_SCHEMA,
    NfspEvaluationPayload,
    NfspTrainingPayload,
)
from tableforge.learning.nfsp import NfspAgent, NfspConfig
from tableforge.learning.training import evaluate, self_play_train


@dataclass(frozen=True, slots=True)
class NfspTrainingExecutor:
    @property
    def output_kind(self) -> ArtifactKind:
        return ArtifactKind.MODEL_CHECKPOINT

    @property
    def output_media_type(self) -> str:
        return "application/zip"

    @property
    def output_schema(self) -> str:
        return CHECKPOINT_SCHEMA

    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        payload = NfspTrainingPayload.from_bytes(payload_bytes)
        agent = NfspAgent(
            NfspConfig(
                hidden_size=payload.hidden_size,
                batch_size=payload.batch_size,
                seed=payload.agent_seed,
            )
        )
        self_play_train(
            agent,
            payload.hands,
            payload.seed_start,
            train_every=payload.train_every,
            on_progress=on_progress,
        )
        return agent.to_bundle()


def opponent_factory(name: str, seed: int) -> Callable[[], PokerAgent]:
    if name == "call":
        return CallBot
    if name == "tight":
        return TightBot
    if name == "aggro":
        return AggroBot
    calls = [0]

    def random_bot() -> PokerAgent:
        calls[0] += 1
        return RandomBot(seed + calls[0])

    return random_bot


@dataclass(frozen=True, slots=True)
class NfspEvaluationExecutor:
    fetch_artifact: Callable[[str], bytes]

    @property
    def output_kind(self) -> ArtifactKind:
        return ArtifactKind.EVALUATION_RESULT

    @property
    def output_media_type(self) -> str:
        return "application/json"

    @property
    def output_schema(self) -> str:
        return EVALUATION_SCHEMA

    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        payload = NfspEvaluationPayload.from_bytes(payload_bytes)
        content = self.fetch_artifact(payload.checkpoint_artifact_id)
        if hashlib.sha256(content).hexdigest() != payload.checkpoint_sha256:
            raise ValueError("checkpoint content does not match referenced hash")
        candidate = NfspAgent.from_bundle(content, inference_only=True)
        results: dict[str, dict[str, float | int]] = {}
        for index, name in enumerate(payload.opponents):
            report = evaluate(
                candidate,
                opponent_factory(name, payload.seed_start + index * 1_000_003),
                payload.deals,
                payload.seed_start,
                on_progress=on_progress,
            )
            results[name] = {
                "bb_per_100": round(report.bb_per_100, 6),
                "confidence_high": round(report.confidence_high, 6),
                "confidence_low": round(report.confidence_low, 6),
                "hands": report.hands,
                "standard_error": round(report.standard_error, 6),
            }
        return json.dumps(
            {
                "checkpoint_sha256": payload.checkpoint_sha256,
                "deals": payload.deals,
                "opponents": results,
                "schema_version": EVALUATION_SCHEMA,
                "train_steps": candidate.train_steps,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
