from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.cli.main import _learning_executors, _rule_executors  # noqa: E402
from tableforge.composition import build_services  # noqa: E402
from tableforge.coordinator.experiments import apply_overrides  # noqa: E402
from tableforge.domain.auth import OperatorRole  # noqa: E402
from tableforge.domain.experiments import (  # noqa: E402
    BUILTIN_PRESETS,
    ExperimentKind,
    ExperimentStatus,
)
from tableforge.domain.learning import NfspInitPayload, NfspMatchPayload  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.web.views import ClusterViews, ViewError  # noqa: E402
from tableforge.worker.loop import WorkerLoop  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402
from tests.integration.test_experiment_workflow import CapturingLogger, capability  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def preset(key: str) -> object:
    return next(item for item in BUILTIN_PRESETS if item.key == key)


def test_overrides_are_validated() -> None:
    base = preset("hu-nfsp-cpu-iterative-v2").config()  # type: ignore[attr-defined]

    changed = apply_overrides(
        base, {"learning_rate": "0.0005", "rounds": 5, "evaluation_deals": 400}
    )

    assert changed.learning_rate == 0.0005 and changed.rounds == 5
    assert changed.max_evaluation_deals >= changed.evaluation_deals
    for bad in ({"rounds": 0}, {"hidden_size": 10_000}, {"seed_start": "x"}, {"players": 6}):
        with pytest.raises(ValueError):
            apply_overrides(base, bad)
    with pytest.raises(ValueError, match="only available"):
        apply_overrides(preset("hu-rule-smoke").config(), {"rounds": 2})  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        apply_overrides(base, {"observation_schema": "poker-hunlhe-vector-v9"})


def test_payloads_of_existing_presets_are_byte_identical() -> None:
    legacy_init = NfspInitPayload("1.0", agent_seed=0, hidden_size=64, batch_size=64)
    assert set(json.loads(legacy_init.to_bytes())) == {
        "agent_seed",
        "batch_size",
        "hidden_size",
        "schema_version",
    }
    tuned = NfspInitPayload("1.0", 0, learning_rate=0.0005, target_update_steps=50)
    decoded = NfspInitPayload.from_bytes(tuned.to_bytes())
    assert (decoded.learning_rate, decoded.target_update_steps) == (0.0005, 50)
    # Match payloads of presets without a benchmark keep the old reference evaluation.
    assert NfspMatchPayload.__dataclass_fields__["reference_deals"].default == 200


def test_group_creates_one_experiment_per_seed(tmp_path: Path) -> None:
    services = build_services(Settings(data_dir=tmp_path))

    group = services.experiments.create_group("hu-nfsp-v2-train", [7, 8, 9], NOW, {"rounds": 2})

    assert [item.config.seed_start for item in group] == [7, 8, 9]
    assert len({item.config.group_id for item in group}) == 1
    assert all(item.config.rounds == 2 for item in group)
    with pytest.raises(ValueError):
        services.experiments.create_group("hu-nfsp-v2-train", [1, 1], NOW)
    with pytest.raises(ValueError):
        services.experiments.create_group("hu-nfsp-v2-train", list(range(17)), NOW)


def test_training_curve_has_a_benchmark_against_every_rule_bot(tmp_path: Path) -> None:
    services = build_services(Settings(data_dir=tmp_path))
    experiments = services.experiments
    created = experiments.create(
        "hu-nfsp-cpu-iterative-v2",
        NOW,
        {
            "rounds": 2,
            "hands_per_job": 200,
            "train_steps_per_round": 20,
            "benchmark_deals": 20,
            "evaluation_deals": 20,
            "max_evaluation_deals": 20,
            "hidden_size": 16,
            "batch_size": 16,
        },
    )
    experiments.start(created.experiment_id, NOW)
    worker_capability = capability()
    runtime = WorkerRuntime(
        node_id=worker_capability.node_id,
        capability=replace(
            worker_capability,
            enabled_roles=("simulation", "evaluation", "nfsp_learner", "nfsp_actor"),
            logical_cpu_cores=8,
            total_memory_bytes=32 * 1024**3,
            available_memory_bytes=32 * 1024**3,
        ),
        coordinator=services.coordinator,
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
        extra_executors={
            **_rule_executors(),
            **_learning_executors(services.coordinator),  # type: ignore[arg-type]
        },
    )

    def advance() -> bool:
        return experiments.reconcile_all(NOW) > 0

    WorkerLoop(runtime, CapturingLogger(), idle_seconds=0, on_idle=advance).run(max_jobs=40)
    experiments.reconcile_all(NOW)

    curve = experiments.training_curve(created.experiment_id)

    assert [point["round"] for point in curve] == [1, 2]
    for point in curve:
        benchmark = point["benchmark"]
        assert set(benchmark) == {"call", "random", "tight", "aggro"}  # type: ignore[arg-type]
        for values in benchmark.values():  # type: ignore[union-attr]
            assert values["hands"] == 40
            assert values["low"] <= values["bb_per_100"] <= values["high"]
    assert experiments.experiments.get(created.experiment_id).status is ExperimentStatus.COMPLETED


def test_views_create_groups_and_expose_training_options(tmp_path: Path) -> None:
    services = build_services(Settings(data_dir=tmp_path))
    views = ClusterViews(services, Clock())
    admin = services.repositories.auth.authenticate_operator(
        services.repositories.auth.issue_operator(
            OperatorRole.ADMIN, "a", NOW, timedelta(days=1)
        ).token,
        NOW,
    )

    options = views.query("training_options", "", admin)
    created = views.command(
        "create_experiment",
        {"preset": "hu-nfsp-v2-train", "settings": {"learning_rate": 0.0003}, "seeds": [1, 2]},
        admin,
    )
    detail = views.query("experiment", created["group"][0]["experiment_id"], admin)

    assert "hu-nfsp-v2-train" in options["presets"]
    assert options["presets"]["hu-nfsp-v2-train"]["benchmark_deals"] == 1_000
    assert {item["seed"] for item in created["group"]} == {1, 2}
    assert detail["config"]["learning_rate"] == 0.0003
    assert detail["curve"] == []
    assert detail["group_id"] == created["group"][1]["group_id"]
    with pytest.raises(ViewError):
        views.command(
            "create_experiment", {"preset": "hu-rule-smoke", "settings": {"rounds": 3}}, admin
        )
    assert all(
        item.kind is not ExperimentKind.NFSP_ITERATIVE or item.key in options["presets"]
        for item in BUILTIN_PRESETS
    )
