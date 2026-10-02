"""PPO's worker jobs (A1): init, actor, learner and match, on the NFSP round pipeline.

The payloads are NFSP's (`domain.learning`) under PPO payload types; experience travels in
the same shard format (`ExperienceShard`, transitions only). Differences from NFSP:

* actors keep every learning seat's decisions together, so episodes are consecutive;
* the learner must use the checkpoint the actors played with (on-policy; checked by
  sha256, as for NFSP) and runs `train_steps` PPO epochs over the round's experience;
* the match benchmarks the sampled policy (the default) and the greedy policy.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tableforge.domain.artifacts import ArtifactKind
from tableforge.domain.learning import (
    MATCH_SCHEMA,
    PPO_CHECKPOINT_SCHEMA,
    SHARD_OUTPUT_SCHEMA,
    NfspActorPayload,
    NfspLearnerPayload,
    NfspMatchPayload,
    PpoInitPayload,
)
from tableforge.learning.buffers import Transition
from tableforge.learning.experience import DatasetSnapshot, ExperienceShard, verified
from tableforge.learning.iterative import load_pool, tournament_match
from tableforge.learning.ppo import PpoAgent, PpoConfig, PpoTablePlayer, Rollout

Fetch = Callable[[str], bytes]


@dataclass(frozen=True, slots=True)
class PpoInitExecutor:
    output_kind: ArtifactKind = ArtifactKind.MODEL_CHECKPOINT
    output_media_type: str = "application/zip"
    output_schema: str = PPO_CHECKPOINT_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        del on_progress
        payload = PpoInitPayload.from_bytes(payload_bytes)
        agent = PpoAgent(
            PpoConfig(
                hidden_size=payload.hidden_size,
                minibatch_size=payload.minibatch_size,
                observation_schema=payload.observation_schema,
                action_schema=payload.action_schema,
                learning_rate=payload.learning_rate,
                reward_scale=payload.reward_scale,
                entropy_coef=payload.entropy_coef,
                gae_lambda=payload.gae_lambda,
                clip=payload.clip,
                seed=payload.agent_seed,
            )
        )
        return agent.to_bundle({"round_index": 0})


@dataclass(frozen=True, slots=True)
class PpoActorExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.EXPERIENCE_SHARD
    output_media_type: str = "application/zip"
    output_schema: str = SHARD_OUTPUT_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        from tableforge.learning.ppo_training import collect
        from tableforge.poker.tournament import TournamentConfig
        from tableforge.poker.tournament_sampler import TournamentSampler

        payload = NfspActorPayload.from_bytes(payload_bytes)
        if not payload.tournament_json:
            raise ValueError("PPO actors play tournaments")
        agent = PpoAgent.from_bundle(
            verified(self.fetch_artifact(payload.checkpoint_artifact_id), payload.checkpoint_sha256)
        )
        agent.rng.seed(payload.actor_seed)
        config: TournamentConfig | TournamentSampler = TournamentConfig.from_json(
            payload.tournament_json
        )
        if payload.sampler_json:
            config = TournamentSampler.from_json(payload.sampler_json)
        opponents = None
        if payload.pool_artifact_id:
            opponents = load_pool(
                self.fetch_artifact, payload.pool_artifact_id, payload.pool_sha256
            )
        transitions, _ = collect(
            agent,
            config,
            payload.hands,
            payload.seed_start,
            payload.self_play_share,
            payload.hand_horizon,
            opponents,
            on_progress,
        )
        return ExperienceShard(
            checkpoint_sha256=payload.checkpoint_sha256,
            seed_start=payload.seed_start,
            hands=payload.hands,
            transitions=tuple(transitions),
            policy_samples=(),
            observation_schema=agent.encoder.schema,
            action_schema=agent.actions.schema,
        ).to_bytes()


@dataclass(frozen=True, slots=True)
class PpoLearnerExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.MODEL_CHECKPOINT
    output_media_type: str = "application/zip"
    output_schema: str = PPO_CHECKPOINT_SCHEMA

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
        runtime, device_id = plan.get("runtime"), plan.get("device_id")
        device = str(device_id) if runtime == "cuda" and isinstance(device_id, str) else "cpu"
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
        agent = PpoAgent.from_bundle(checkpoint, device=device)
        transitions: list[Transition] = []
        for reference in snapshot.shards:
            shard = ExperienceShard.from_bytes(
                verified(self.fetch_artifact(reference.artifact_id), reference.sha256)
            )
            if shard.checkpoint_sha256 != payload.checkpoint_sha256:
                raise ValueError("PPO learns only from experience of the current checkpoint")
            if (shard.observation_schema, shard.action_schema) != (
                agent.encoder.schema,
                agent.actions.schema,
            ):
                raise ValueError("experience shard does not match the model's schemas")
            transitions.extend(shard.transitions)
        if transitions:
            agent.learn(
                Rollout.of(transitions, agent.encoder.feature_size, agent.actions.count),
                epochs=payload.train_steps,
                on_progress=on_progress,
            )
        return agent.to_bundle(
            {
                "parent_sha256": payload.checkpoint_sha256,
                "round_index": payload.round_index,
                "shard_sha256": [reference.sha256 for reference in snapshot.shards],
            }
        )


@dataclass(frozen=True, slots=True)
class PpoMatchExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.EVALUATION_RESULT
    output_media_type: str = "application/json"
    output_schema: str = MATCH_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        payload = NfspMatchPayload.from_bytes(payload_bytes)
        if not payload.tournament_json:
            raise ValueError("PPO matches are tournament benchmarks")
        candidate = PpoAgent.from_bundle(
            verified(self.fetch_artifact(payload.candidate_artifact_id), payload.candidate_sha256)
        )
        baseline = PpoAgent.from_bundle(
            verified(self.fetch_artifact(payload.baseline_artifact_id), payload.baseline_sha256)
        )
        greedy = PpoAgent.from_bundle(
            verified(self.fetch_artifact(payload.candidate_artifact_id), payload.candidate_sha256)
        )
        return tournament_match(
            self.fetch_artifact,
            payload,
            (
                ("sample", lambda: PpoTablePlayer(candidate)),
                ("greedy", lambda: PpoTablePlayer(greedy, greedy=True)),
            ),
            lambda: PpoTablePlayer(baseline),
            candidate.train_steps,
            on_progress,
        )
