from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

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
                reward_scale=payload.reward_scale,
                double_q=payload.double_q,
                rl_capacity=payload.rl_capacity,
                sl_capacity=payload.sl_capacity,
                action_schema=payload.action_schema,
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
        if payload.tournament_json:
            return _tournament_shard(agent, payload, on_progress, self.fetch_artifact)
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
            action_schema=agent.actions.schema,
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
            if shard.action_schema != agent.actions.schema:
                raise ValueError(
                    f"experience shard uses {shard.action_schema}, "
                    f"the model uses {agent.actions.schema}"
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
        if payload.tournament_json:
            return _tournament_match(self.fetch_artifact, payload, on_progress)
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
                "candidate_train_steps": int(checkpoint_manifest(candidate_content)["train_steps"]),
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


def load_pool(fetch: Fetch, artifact_id: str, sha256: str, models_only: bool = False) -> Any:
    """Build an `OpponentPool` from a pool artifact (checkpoints verified by sha256)."""
    from tableforge.domain.opponent_pool import OpponentPoolSpec
    from tableforge.learning.opponents import OpponentPool, model_member, rule_members

    spec = OpponentPoolSpec.from_bytes(verified(fetch(artifact_id), sha256))
    members = [] if models_only else rule_members(spec.rules)
    for model in spec.models:
        members.append(
            model_member(
                model.name,
                verified(fetch(model.artifact_id), model.sha256),
                model.kind,
                algorithm=model.algorithm,
            )
        )
    if not members:
        return None
    return OpponentPool(members, weighting=spec.weighting)


def _tournament_shard(
    agent: NfspAgent,
    payload: NfspActorPayload,
    on_progress: Callable[[int], None] | None,
    fetch: Fetch | None = None,
) -> bytes:
    """Play `payload.hands` training tournaments and return their experience."""
    from tableforge.learning.tournament_training import play_training_tournament
    from tableforge.poker.tournament import TournamentConfig
    from tableforge.poker.tournament_sampler import TournamentSampler

    config: TournamentConfig | TournamentSampler = TournamentConfig.from_json(
        payload.tournament_json
    )
    if payload.sampler_json:
        config = TournamentSampler.from_json(payload.sampler_json)
    opponents = None
    if payload.pool_artifact_id and fetch is not None:
        opponents = load_pool(fetch, payload.pool_artifact_id, payload.pool_sha256)
    transitions: list[Transition] = []
    samples: list[PolicySample] = []
    agent.policy_sink = samples.append
    try:
        for index in range(payload.hands):
            play_training_tournament(
                agent,
                config,
                payload.seed_start + index,
                transitions.append,
                payload.self_play_share,
                hand_horizon=payload.hand_horizon,
                opponents=opponents,
            )
            if on_progress is not None and (index + 1) % 10 == 0:
                on_progress(index + 1)
    finally:
        agent.policy_sink = None
    return ExperienceShard(
        checkpoint_sha256=payload.checkpoint_sha256,
        seed_start=payload.seed_start,
        hands=payload.hands,
        transitions=tuple(transitions),
        policy_samples=tuple(samples),
        observation_schema=agent.encoder.schema,
        action_schema=agent.actions.schema,
    ).to_bytes()


def _tournament_match(
    fetch: Fetch, payload: NfspMatchPayload, on_progress: Callable[[int], None] | None
) -> bytes:
    """Benchmark candidate and baseline on the same tournaments against a rule-bot field.

    ``head_to_head`` is the paired ROI difference (candidate minus baseline, in ROI
    percentage points) so the usual promotion rule applies: a candidate is rejected only
    when it is significantly worse.
    """
    from tableforge.learning.tournament_training import (
        FrozenTournamentPlayer,
        GreedyTournamentPlayer,
    )
    from tableforge.poker.tournament import TournamentConfig
    from tableforge.poker.tournament_benchmark import (
        Estimate,
        TournamentBenchmark,
        collect_samples,
    )
    from tableforge.worker.tournament import field_factory

    config = TournamentConfig.from_json(payload.tournament_json)
    candidate_content = verified(fetch(payload.candidate_artifact_id), payload.candidate_sha256)
    baseline_content = verified(fetch(payload.baseline_artifact_id), payload.baseline_sha256)
    candidate = NfspAgent.from_bundle(candidate_content, inference_only=True)
    baseline = NfspAgent.from_bundle(baseline_content, inference_only=True)
    greedy = NfspAgent.from_actor_bundle(candidate_content)
    field = field_factory(payload.tournament_field)
    seeds = payload.min_deals
    samples = {
        name: collect_samples(
            factory,
            field,
            config,
            seeds,
            payload.seed_start,
            True,
            on_progress if name == "average" else None,
        )
        for name, factory in (
            ("average", lambda: FrozenTournamentPlayer(candidate)),
            ("best_response", lambda: GreedyTournamentPlayer(greedy)),
            ("baseline", lambda: FrozenTournamentPlayer(baseline)),
        )
    }
    difference = Estimate.of(
        [
            (new - old) / config.buy_in * 100
            for new, old in zip(samples["average"].total, samples["baseline"].total, strict=True)
        ]
    )

    def roi(name: str) -> dict[str, float | int]:
        summary = TournamentBenchmark.from_samples(samples[name], config.name)
        return {
            "bb_per_100": _rounded(summary.roi.mean * 100),
            "confidence_low": _rounded(summary.roi.low * 100),
            "confidence_high": _rounded(summary.roi.high * 100),
            "hands": summary.tournaments,
        }

    summaries: dict[str, Any] = {
        name: TournamentBenchmark.from_samples(samples[name], config.name).summary()
        for name in samples
    }
    references = {"average": roi("average"), "best_response": roi("best_response")}
    if payload.pool_artifact_id and payload.exploit_seeds:
        from tableforge.learning.pool_benchmark import rotating_field

        pool = load_pool(fetch, payload.pool_artifact_id, payload.pool_sha256)
        if pool is None:
            raise ValueError("the exploit target pool is empty")
        target_field = rotating_field(pool.all_members())
        exploit: dict[str, Any] = {"target": [member.name for member in pool.all_members()]}
        for name, factory in (
            ("average", lambda: FrozenTournamentPlayer(candidate)),
            ("best_response", lambda: GreedyTournamentPlayer(greedy)),
        ):
            summary = TournamentBenchmark.from_samples(
                collect_samples(
                    factory,
                    target_field,
                    config,
                    payload.exploit_seeds,
                    payload.seed_start + 9_000_000,
                ),
                config.name,
            )
            exploit[name] = summary.summary()
            references[f"target_{name}"] = {
                "bb_per_100": _rounded(summary.roi.mean * 100),
                "confidence_low": _rounded(summary.roi.low * 100),
                "confidence_high": _rounded(summary.roi.high * 100),
                "hands": summary.tournaments,
            }
        summaries["exploit"] = exploit
    if payload.pool_artifact_id and payload.pool_seeds:
        from tableforge.learning.pool_benchmark import pool_benchmark

        pool = load_pool(fetch, payload.pool_artifact_id, payload.pool_sha256, models_only=True)
        if pool is not None:
            result = pool_benchmark(
                lambda: FrozenTournamentPlayer(candidate),
                pool.all_members(),
                config,
                payload.pool_seeds,
                payload.seed_start + 7_000_000,
                payload.pool_matchup_seeds,
            )
            summaries["pool"] = result
            overall = result["overall"]["roi"]  # type: ignore[index]
            references["pool"] = {
                "bb_per_100": _rounded(overall["mean"] * 100),
                "confidence_low": _rounded(overall["low"] * 100),
                "confidence_high": _rounded(overall["high"] * 100),
                "hands": result["overall"]["tournaments"],  # type: ignore[index]
            }
    return json.dumps(
        {
            "baseline_sha256": payload.baseline_sha256,
            "candidate_sha256": payload.candidate_sha256,
            "candidate_train_steps": int(checkpoint_manifest(candidate_content)["train_steps"]),
            "head_to_head": {
                "bb_per_100": _rounded(difference.mean),
                "confidence_high": _rounded(difference.high),
                "confidence_low": _rounded(difference.low),
                "hands": samples["average"].tournaments,
                "standard_error": _rounded((difference.high - difference.low) / 3.92),
                "unit": "roi_points",
            },
            "references": references,
            "reference_unit": "roi_percent",
            "tournament": summaries,
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
