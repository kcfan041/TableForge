"""Tournament replays: play one tournament and record everything that happened.

A replay job plays a single tournament with the given seed and seats (models or rule
bots) and records, for every hand, the blinds, stacks, bounties, all hole cards, the board
and every action with its amount. For seats played by a model, every decision also carries
the model's explanation (`DecisionExplainer`), so the UI can show probabilities and
algorithm-specific values without knowing the algorithm.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tableforge.domain.artifacts import ArtifactKind
from tableforge.learning.explain import DecisionExplainer, RuleBotExplainer, canonical_action
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.table import (
    TableAgent,
    TableHand,
    TableObservation,
    TableResult,
    TableRunner,
)
from tableforge.poker.tournament import Tournament, TournamentConfig, TournamentHandInfo

REPLAY_PAYLOAD_TYPE = "tableforge.v1.TournamentReplayJobPayload"
REPLAY_SCHEMA = "tableforge.tournament-replay.v1"
MAX_REPLAY_HANDS = 400


@dataclass(frozen=True, slots=True)
class SeatSpec:
    """Who plays a player id: a rule bot by name, or a model checkpoint artifact."""

    kind: str
    name: str
    artifact_id: str = ""
    sha256: str = ""
    mode: str = ""
    algorithm: str = "nfsp"

    def __post_init__(self) -> None:
        if self.kind not in ("rule", "model"):
            raise ValueError("seat kind must be rule or model")
        if self.kind == "model" and (not self.artifact_id or len(self.sha256) != 64):
            raise ValueError("a model seat needs its checkpoint artifact and sha256")


@dataclass(frozen=True, slots=True)
class ReplayJobPayload:
    schema_version: str
    config_json: str
    seed: int
    seats: tuple[SeatSpec, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported replay payload schema")
        config = TournamentConfig.from_json(self.config_json)
        if len(self.seats) != config.players:
            raise ValueError("one seat is required per player")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "config_json": self.config_json,
                "seed": self.seed,
                "seats": [
                    {
                        "kind": seat.kind,
                        "name": seat.name,
                        "artifact_id": seat.artifact_id,
                        "sha256": seat.sha256,
                        "mode": seat.mode,
                        "algorithm": seat.algorithm,
                    }
                    for seat in self.seats
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, payload: bytes) -> ReplayJobPayload:
        data = json.loads(payload)
        return cls(
            str(data["schema_version"]),
            str(data["config_json"]),
            int(data["seed"]),
            tuple(SeatSpec(**seat) for seat in data["seats"]),
        )


@dataclass
class _Seat:
    """Plays one player id and appends each decision (with explanation) to a shared log."""

    player: int
    explainer: DecisionExplainer
    mode: str
    explain: bool
    log: list[dict[str, object]]

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.explainer.begin_hand(info)

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        explanation = (
            self.explainer.explain(observation, legal, self.mode).as_dict()
            if self.explain
            else None
        )
        action = self.explainer.act(observation, legal, self.mode)
        me = observation.players[observation.observer]
        self.log.append(
            {
                "player": self.player,
                "street": observation.street.name.lower(),
                "pot": observation.pot,
                "to_call": max(0, observation.current_bet - me.street_contribution),
                "stack": me.stack,
                "action": canonical_action(action, legal),
                "amount": action.amount,
                "explanation": explanation,
            }
        )
        return action


class _Runner:
    """Runs hands like `TableRunner` and remembers the last one (cards and result)."""

    def __init__(self) -> None:
        self.runner = TableRunner()
        self.last: tuple[TableHand, TableResult] | None = None

    def run(self, hand: TableHand, agents: Sequence[TableAgent]) -> TableResult:
        result = self.runner.run(hand, agents)
        self.last = (hand, result)
        return result


def play_replay(
    config: TournamentConfig,
    seed: int,
    players: Sequence[tuple[DecisionExplainer, str, bool]],
    labels: Sequence[str],
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, object]:
    """Play one tournament; `players[i]` is (explainer, play mode, record explanations)."""
    log: list[dict[str, object]] = []
    seats = [
        _Seat(player, explainer, mode, explain, log)
        for player, (explainer, mode, explain) in enumerate(players)
    ]
    tournament = Tournament(config, seed)
    runner = _Runner()
    hands: list[dict[str, object]] = []
    while not tournament.complete:
        log.clear()
        record = tournament.play_hand(seats, runner, record=True)
        assert record is not None and runner.last is not None
        hand, result = runner.last
        hands.append(
            {
                "hand": record.hand_number + 1,
                "level": record.level,
                "small_blind": record.small_blind,
                "big_blind": record.big_blind,
                "ante": record.ante,
                "button": record.button_player,
                "players": list(record.seat_players),
                "stacks_before": list(record.stacks_before),
                "stacks_after": list(record.stacks_after),
                "bounties_before": [round(value, 6) for value in record.bounties_before],
                "bounties_after": [round(value, 6) for value in record.bounties_after],
                "icm_before": [round(value, 6) for value in record.icm_before],
                "icm_after": [round(value, 6) for value in record.icm_after],
                "hole_cards": [[str(card) for card in cards] for cards in hand.hole_cards],
                "board": [str(card) for card in hand.board],
                "actions": list(log),
                "busted": list(record.busted),
                "knockouts": [
                    {
                        "eliminated": knockout.eliminated,
                        "collector": knockout.collector,
                        "cash": round(knockout.cash, 6),
                        "added_to_bounty": round(knockout.added_to_bounty, 6),
                    }
                    for knockout in record.knockouts
                ],
                "showdown": not result.ended_by_fold,
                "winners": [record.seat_players[index] for index in result.winners]
                if not result.ended_by_fold
                else [
                    record.seat_players[index]
                    for index, payout in enumerate(result.payouts)
                    if payout > 0
                ],
            }
        )
        if on_progress is not None:
            on_progress(len(hands))
        if len(hands) > MAX_REPLAY_HANDS:
            raise RuntimeError("replay is longer than the hand limit")
    final = tournament.finish()
    return {
        "schema_version": REPLAY_SCHEMA,
        "config": json.loads(config.to_json()),
        "seed": seed,
        "labels": list(labels),
        "hands": hands,
        "result": {
            "places": list(final.places),
            "prizes": [round(value, 6) for value in final.prizes],
            "bounties": [round(value, 6) for value in final.bounties],
            "results": [round(value, 6) for value in final.results],
        },
    }


def rule_explainer(name: str, seed: int) -> RuleBotExplainer:
    from tableforge.worker.tournament import rule_bot

    return RuleBotExplainer(rule_bot(name, seed))


@dataclass(frozen=True, slots=True)
class ReplayExecutor:
    """Worker side of replay jobs; needs PyTorch only when a seat is a model."""

    fetch_artifact: Callable[[str], bytes]
    output_kind: ArtifactKind = ArtifactKind.HAND_HISTORY
    output_media_type: str = "application/json"
    output_schema: str = REPLAY_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        from tableforge.learning.experience import verified

        payload = ReplayJobPayload.from_bytes(payload_bytes)
        config = TournamentConfig.from_json(payload.config_json)
        players: list[tuple[DecisionExplainer, str, bool]] = []
        labels: list[str] = []
        for player, seat in enumerate(payload.seats):
            if seat.kind == "rule":
                players.append(
                    (rule_explainer(seat.name, payload.seed * 16 + player), "rule", False)
                )
                labels.append(seat.name)
                continue
            if seat.algorithm != "nfsp":
                raise ValueError(f"unsupported algorithm: {seat.algorithm}")
            from tableforge.learning.nfsp_explain import NfspExplainer

            # Each seat gets its own explainer: agents keep per-hand state.
            content = verified(self.fetch_artifact(seat.artifact_id), seat.sha256)
            explainer = NfspExplainer(content)
            players.append((explainer, seat.mode or explainer.play_modes[0].key, True))
            labels.append(seat.name)
        replay = play_replay(config, payload.seed, players, labels, on_progress)
        return json.dumps(replay, separators=(",", ":")).encode()
