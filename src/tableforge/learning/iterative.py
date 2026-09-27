from __future__ import annotations

import json
from collections.abc import Callable, Mapping
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
from tableforge.domain.nfsp_pipeline import promotion_decision
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
                observation_schema=payload.observation_schema,
                learning_rate=payload.learning_rate,
                anticipatory=payload.anticipatory,
                epsilon=payload.epsilon,
                target_update_steps=payload.target_update_steps,
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
        picker = None
        if payload.population_artifact_id:
            from tableforge.domain.league import LeaguePopulation
            from tableforge.learning.league import MemberLoader, population_picker

            population = LeaguePopulation.from_bytes(
                verified(
                    self.fetch_artifact(payload.population_artifact_id),
                    payload.population_sha256,
                )
            )
            if population.learner_sha256 != payload.checkpoint_sha256:
                raise ValueError("league population belongs to a different checkpoint")
            picker = population_picker(
                population, MemberLoader(self.fetch_artifact), payload.actor_seed
            )
        transitions, samples = collect_experience(
            agent, payload.hands, payload.seed_start, on_progress, picker
        )
        return ExperienceShard(
            checkpoint_sha256=payload.checkpoint_sha256,
            seed_start=payload.seed_start,
            hands=payload.hands,
            transitions=tuple(transitions),
            policy_samples=tuple(samples),
            observation_schema=agent.encoder.schema,
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
        return self.execute_with_plan(payload_bytes, {}, on_progress)

    def execute_with_plan(
        self,
        payload_bytes: bytes,
        plan: Mapping[str, object],
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes:
        device, batch_size = _plan_device_and_batch(plan)
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
        agent = NfspAgent.from_bundle(checkpoint, device=device)
        transitions: list[Transition] = []
        samples: list[PolicySample] = []
        for reference in snapshot.shards:
            shard = ExperienceShard.from_bytes(
                verified(self.fetch_artifact(reference.artifact_id), reference.sha256)
            )
            if shard.checkpoint_sha256 != payload.checkpoint_sha256:
                raise ValueError("experience shard was generated from a different checkpoint")
            if shard.observation_schema != agent.encoder.schema:
                raise ValueError(
                    f"experience shard uses {shard.observation_schema}, "
                    f"the model uses {agent.encoder.schema}"
                )
            transitions.extend(shard.transitions)
            samples.extend(shard.policy_samples)
        learn_from_experience(
            agent, transitions, samples, payload.train_steps, on_progress, batch_size
        )
        return agent.to_bundle(
            {
                "parent_sha256": payload.checkpoint_sha256,
                "round_index": payload.round_index,
                "shard_sha256": [reference.sha256 for reference in snapshot.shards],
            }
        )


def _plan_device_and_batch(plan: Mapping[str, object]) -> tuple[str, int | None]:
    runtime = plan.get("runtime")
    device_id = plan.get("device_id")
    batch = plan.get("batch_size")
    device = str(device_id) if runtime == "cuda" and isinstance(device_id, str) else "cpu"
    return device, int(batch) if isinstance(batch, int) else None


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


__all__ = [
    "NfspActorExecutor",
    "NfspInitExecutor",
    "NfspLearnerExecutor",
    "NfspMatchExecutor",
    "promotion_decision",
]
