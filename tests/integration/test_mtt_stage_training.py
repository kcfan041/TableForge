"""T5: v4 models on MTT stages, and the stage presets on the platform."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.composition import build_services  # noqa: E402
from tableforge.domain.experiments import ExperimentStatus  # noqa: E402
from tableforge.learning import encoding_v3, encoding_v4  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.ppo import PpoAgent, PpoConfig  # noqa: E402
from tableforge.learning.tournament_training import play_training_tournament  # noqa: E402
from tableforge.poker.mtt_stage import STAGES  # noqa: E402
from tableforge.poker.tournament import BountyConfig, preset_config  # noqa: E402
from tableforge.poker.tournament_sampler import TournamentSampler  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tests.integration.test_ppo_training import run_experiment  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)
V4 = {"observation_schema": "poker-nlhe-vector-v4", "action_schema": "nlhe-discrete-v2"}


def test_v4_sees_the_field_and_keeps_v3_in_front() -> None:
    agent = NfspAgent(NfspConfig(hidden_size=16, batch_size=16, **V4))
    sampler = TournamentSampler(players=(6, 9), stages=("bubble",), stage_share=0.5)
    transitions = []
    for seed in range(8):
        play_training_tournament(agent, sampler, seed, transitions.append, 0.5, hand_horizon=True)

    assert len(transitions[0].state) == encoding_v4.FEATURE_SIZE
    flags = {item.state[encoding_v3.FEATURE_SIZE] for item in transitions}
    assert flags == {0.0, 1.0}, "both single tables and stages were played"
    assert all(
        all(0.0 <= value <= 1.0 for value in item.state[encoding_v3.FEATURE_SIZE :])
        for item in transitions
    )


def test_v3_models_refuse_nothing_but_ppo_v4_trains_on_stages() -> None:
    config = replace(preset_config(25, players=9, bounty=BountyConfig()), stage=STAGES["itm"])
    agent = PpoAgent(PpoConfig(hidden_size=16, **V4))
    from tableforge.learning.ppo_training import ppo_self_play_train

    report = ppo_self_play_train(agent, config, 4, 10, 2, 0.75, True)
    assert report.updates == 2 and report.decisions > 0


def test_stage_benchmark_preset_runs_on_the_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tableforge.domain import experiments as presets

    small = tuple(
        replace(preset, hands=4, hands_per_job=2)
        if preset.key == "mtt-pko-final-tables-pushfold"
        else preset
        for preset in presets.BUILTIN_PRESETS
    )
    monkeypatch.setattr("tableforge.coordinator.experiments.BUILTIN_PRESETS", small)
    services, created = run_experiment(tmp_path, "mtt-pko-final-tables-pushfold")
    experiment = services.experiments.experiments.get(created.experiment_id)
    result = services.experiments.tournament_result(created.experiment_id)

    assert experiment.status is ExperimentStatus.COMPLETED
    assert result is not None and result["tournaments"] == 4 * 9
    assert len(result["place_share"]) == 9  # type: ignore[arg-type]


def test_v4_smoke_trains_on_the_platform(tmp_path: Path) -> None:
    services, created = run_experiment(tmp_path, "mtt-pko-v4-smoke")
    experiments = services.experiments
    assert experiments.experiments.get(created.experiment_id).status is ExperimentStatus.COMPLETED
    assert [point["round"] for point in experiments.training_curve(created.experiment_id)] == [
        1,
        2,
    ]


def test_stage_training_needs_v4(tmp_path: Path) -> None:
    experiments = build_services(Settings(data_dir=tmp_path)).experiments
    with pytest.raises(ValueError, match="v4"):
        experiments.create(
            "mixed-pko-v3-smoke", NOW, {"tournament": {"players": 9, "stage": "bubble"}}
        )
    with pytest.raises(ValueError, match="v4"):
        experiments.create(
            "mixed-pko-v3-smoke",
            NOW,
            {"sampler": {"players": [9], "stages": ["bubble"], "stage_share": 0.5}},
        )
    created = experiments.create(
        "mtt-pko-v4-smoke", NOW, {"tournament": {"players": 8, "stage": "final_tables"}}
    )
    assert created.config.players == 8
