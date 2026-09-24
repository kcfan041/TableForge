from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from tableforge.domain.artifacts import ArtifactKind
from tableforge.domain.learning import (
    CHECKPOINT_SCHEMA,
    MATCH_SCHEMA,
    SHARD_OUTPUT_SCHEMA,
    NfspActorPayload,
    NfspInitPayload,
    NfspLearnerPayload,
    NfspMatchPayload,
)
from tableforge.learning.buffers import PolicySample, Transition
from tableforge.learning.executors import opponent_factory
from tableforge.learning.experience import (
    DatasetSnapshot,
    ExperienceShard,
    verified,
)
from tableforge.learning.nfsp import NfspAgent, NfspConfig, checkpoint_manifest
from tableforge.learning.training import (
    collect_experience,
    evaluate,
    evaluate_until,
    learn_from_experience,
)

Fetch = Callable[[str], bytes]


def _rounded(value: float) -> float:
    return round(value, 6)


@dataclass(frozen=True, slots=True)
class NfspInitExecutor:
    output_kind: ArtifactKind = ArtifactKind.MODEL_CHECKPOINT
    output_media_type: str = "application/zip"
    output_schema: str = CHECKPOINT_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        del on_progress
        payload = NfspInitPayload.from_bytes(payload_bytes)
        agent = NfspAgent(
            NfspConfig(
                hidden_size=payload.hidden_size,
                batch_size=payload.batch_size,
                seed=payload.agent_seed,
            )
        )
        return agent.to_bundle({"round_index": 0})


@dataclass(frozen=True, slots=True)
class NfspActorExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.EXPERIENCE_SHARD
    output_media_type: str = "application/zip"
    output_schema: str = SHARD_OUTPUT_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        payload = NfspActorPayload.from_bytes(payload_bytes)
        content = verified(
            self.fetch_artifact(payload.checkpoint_artifact_id), payload.checkpoint_sha256
        )
        agent = NfspAgent.from_actor_bundle(content)
        agent.rng.seed(payload.actor_seed)
        transitions, samples = collect_experience(
            agent, payload.hands, payload.seed_start, on_progress
        )
        return ExperienceShard(
            checkpoint_sha256=payload.checkpoint_sha256,
            seed_start=payload.seed_start,
            hands=payload.hands,
            transitions=tuple(transitions),
            policy_samples=tuple(samples),
        ).to_bytes()


@dataclass(frozen=True, slots=True)
class NfspLearnerExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.MODEL_CHECKPOINT
    output_media_type: str = "application/zip"
    output_schema: str = CHECKPOINT_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        payload = NfspLearnerPayload.from_bytes(payload_bytes)
        checkpoint = verified(
            self.fetch_artifact(payload.checkpoint_artifact_id), payload.checkpoint_sha256
        )
        snapshot = DatasetSnapshot.from_bytes(
            verified(self.fetch_artifact(payload.snapshot_artifact_id), payload.snapshot_sha256)
        )
        if snapshot.checkpoint_sha256 != payload.checkpoint_sha256:
            raise ValueError("dataset snapshot was generated from a different checkpoint")
        if snapshot.round_index != payload.round_index:
            raise ValueError("dataset snapshot round does not match learner round")
        transitions: list[Transition] = []
        samples: list[PolicySample] = []
        for reference in snapshot.shards:
            shard = ExperienceShard.from_bytes(
                verified(self.fetch_artifact(reference.artifact_id), reference.sha256)
            )
            if shard.checkpoint_sha256 != payload.checkpoint_sha256:
                raise ValueError("experience shard was generated from a different checkpoint")
            transitions.extend(shard.transitions)
            samples.extend(shard.policy_samples)
        agent = NfspAgent.from_bundle(checkpoint)
        learn_from_experience(agent, transitions, samples, payload.train_steps, on_progress)
        return agent.to_bundle(
            {
                "parent_sha256": payload.checkpoint_sha256,
                "round_index": payload.round_index,
                "snapshot_sha256": payload.snapshot_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class NfspMatchExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.EVALUATION_RESULT
    output_media_type: str = "application/json"
    output_schema: str = MATCH_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        payload = NfspMatchPayload.from_bytes(payload_bytes)
        candidate_content = verified(
            self.fetch_artifact(payload.candidate_artifact_id), payload.candidate_sha256
        )
        candidate = NfspAgent.from_bundle(candidate_content, inference_only=True)
        baseline = NfspAgent.from_bundle(
            verified(self.fetch_artifact(payload.baseline_artifact_id), payload.baseline_sha256),
            inference_only=True,
        )
        head_to_head = evaluate_until(
            candidate,
            lambda: baseline,
            payload.seed_start,
            payload.min_deals,
            payload.max_deals,
            payload.batch_deals,
            payload.target_width,
            on_progress,
        )
        references: dict[str, dict[str, float | int]] = {}
        for index, name in enumerate(payload.reference_opponents):
            report = evaluate(
                candidate,
                opponent_factory(name, payload.seed_start + (index + 1) * 1_000_003),
                payload.reference_deals,
                payload.seed_start + (index + 1) * 10_000_019,
            )
            references[name] = {
                "bb_per_100": _rounded(report.bb_per_100),
                "confidence_high": _rounded(report.confidence_high),
                "confidence_low": _rounded(report.confidence_low),
                "hands": report.hands,
            }
        return json.dumps(
            {
                "baseline_sha256": payload.baseline_sha256,
                "candidate_sha256": payload.candidate_sha256,
                "candidate_train_steps": int(
                    checkpoint_manifest(candidate_content)["train_steps"]
                ),
                "head_to_head": {
                    "bb_per_100": _rounded(head_to_head.bb_per_100),
                    "confidence_high": _rounded(head_to_head.confidence_high),
                    "confidence_low": _rounded(head_to_head.confidence_low),
                    "hands": head_to_head.hands,
                    "standard_error": _rounded(head_to_head.standard_error),
                },
                "references": references,
                "schema_version": MATCH_SCHEMA,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def promotion_decision(result: dict[str, object]) -> tuple[bool, str]:
    head = result["head_to_head"]
    if not isinstance(head, dict):
        raise ValueError("match result is missing head_to_head")
    high = float(head["confidence_high"])
    mean = float(head["bb_per_100"])
    if high < 0:
        return False, f"significantly worse than active: {mean:.1f} bb/100, CI high {high:.1f}"
    return True, f"not significantly worse than active: {mean:.1f} bb/100, CI high {high:.1f}"
