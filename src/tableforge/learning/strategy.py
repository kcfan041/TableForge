"""Preflop strategy tables: what a model does with each of the 169 starting hands.

A strategy table job builds preflop spots directly on the table engine and asks the model
(through `DecisionExplainer`, so any algorithm works) what it would do with every starting
hand:

* ``open``: everyone before the hero folds; the hero may fold, call (limp), raise or shove.
* ``call``: the ``shover`` goes all in, everyone else before the hero folds; the hero
  decides whether to call. The shover's bounty can be 1x, 2x or 3x the starting bounty.

Every player has the same stack (the effective stack of the spot) and the blinds and ante
of the first level. Positions are for a full 6-max table.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from tableforge.domain.artifacts import ArtifactKind
from tableforge.learning.explain import ACTIONS, DecisionExplainer
from tableforge.poker.betting import Action
from tableforge.poker.cards import Card, Rank, Suit
from tableforge.poker.table import TableHand
from tableforge.poker.tournament import TournamentConfig, TournamentHandInfo

STRATEGY_PAYLOAD_TYPE = "tableforge.v1.StrategyTableJobPayload"
STRATEGY_SCHEMA = "tableforge.strategy-table.v1"
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
"""Preflop acting order at a 6-max table."""
DEFAULT_STACKS = (3, 5, 8, 10, 15, 20)
DEFAULT_BOUNTIES = (1.0, 2.0, 3.0)
RANKS = "AKQJT98765432"


def hand_classes() -> list[str]:
    """The 169 starting hands in grid order: row i, column j (pairs on the diagonal,
    suited above it, offsuit below)."""
    names = []
    for row, first in enumerate(RANKS):
        for column, second in enumerate(RANKS):
            if row == column:
                names.append(first + second)
            elif column > row:
                names.append(first + second + "s")
            else:
                names.append(second + first + "o")
    return names


def _rank(symbol: str) -> Rank:
    return Rank(14 - RANKS.index(symbol))


def hole_cards(name: str) -> tuple[Card, Card]:
    high, low = _rank(name[0]), _rank(name[1])
    if len(name) == 3 and name[2] == "s":
        return Card(high, Suit.HEARTS), Card(low, Suit.HEARTS)
    return Card(high, Suit.SPADES), Card(low, Suit.HEARTS)


@dataclass(frozen=True, slots=True)
class StrategyTablePayload:
    schema_version: str
    config_json: str
    artifact_id: str
    sha256: str
    mode: str
    algorithm: str = "nfsp"
    stacks: tuple[float, ...] = DEFAULT_STACKS
    bounties: tuple[float, ...] = DEFAULT_BOUNTIES

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported strategy table payload schema")
        config = TournamentConfig.from_json(self.config_json)
        if config.players != 6:
            raise ValueError("strategy tables use 6-max positions")
        if len(self.sha256) != 64 or not self.artifact_id:
            raise ValueError("model reference is invalid")
        if not self.stacks or any(not 1 <= stack <= 200 for stack in self.stacks):
            raise ValueError("stacks must be between 1 and 200 big blinds")
        if not self.bounties or any(not 0 <= bounty <= 20 for bounty in self.bounties):
            raise ValueError("bounty multiples must be between 0 and 20")

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "config_json": self.config_json,
                "artifact_id": self.artifact_id,
                "sha256": self.sha256,
                "mode": self.mode,
                "algorithm": self.algorithm,
                "stacks": list(self.stacks),
                "bounties": list(self.bounties),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, payload: bytes) -> StrategyTablePayload:
        data = json.loads(payload)
        data["stacks"] = tuple(float(value) for value in data["stacks"])
        data["bounties"] = tuple(float(value) for value in data["bounties"])
        return cls(**data)


def _seat(position: str) -> int:
    """Seat index with the button at seat 0: SB 1, BB 2, UTG 3, HJ 4, CO 5, BTN 0."""
    return {"BTN": 0, "SB": 1, "BB": 2, "UTG": 3, "HJ": 4, "CO": 5}[position]


def spots() -> list[tuple[str, str, str]]:
    """(scenario, hero, shover) for every table in a strategy table job."""
    found = [("open", hero, "") for hero in POSITIONS[:-1]]
    for hero_index, hero in enumerate(POSITIONS):
        for shover in POSITIONS[:hero_index]:
            found.append(("call", hero, shover))
    return found


def _spot_hand(
    config: TournamentConfig, stack_bb: float, scenario: str, hero: str, shover: str
) -> tuple[TableHand, int]:
    level = config.blind_levels[0]
    stack = max(round(stack_bb * level.big_blind), level.big_blind + level.ante + 1)
    hand = TableHand([stack] * 6, 0, level.small_blind, level.big_blind, 0, level.ante)
    hero_seat = _seat(hero)
    shover_seat = _seat(shover) if shover else -1
    while hand.betting.current_actor != hero_seat:
        actor = hand.betting.current_actor
        if actor is None:
            raise AssertionError(f"the hand ended before {hero} acted")
        legal = hand.legal_actions(actor)
        if actor == shover_seat and legal.max_raise_to is not None:
            hand.act(actor, Action.raise_to(legal.max_raise_to))
        elif legal.can_check:
            hand.act(actor, Action.check())
        else:
            hand.act(actor, Action.fold())
    return hand, hero_seat


def build_table(
    explainer: DecisionExplainer,
    config: TournamentConfig,
    mode: str,
    stack_bb: float,
    scenario: str,
    hero: str,
    shover: str,
    bounty_multiple: float,
) -> dict[str, list[float | None]]:
    """Probabilities (and value details) of every starting hand in one spot."""
    hand, hero_seat = _spot_hand(config, stack_bb, scenario, hero, shover)
    level = config.blind_levels[0]
    bounties = [config.starting_bounty] * 6
    if shover:
        bounties[_seat(shover)] = config.starting_bounty * bounty_multiple
    explainer.begin_hand(
        TournamentHandInfo(
            hand_number=0,
            level=1,
            small_blind=level.small_blind,
            big_blind=level.big_blind,
            ante=level.ante,
            hands_to_next_level=level.hands,
            hands_per_level=level.hands,
            players_left=6,
            starting_stack=config.starting_stack,
            payout=config.payout,
            seat=hero_seat,
            player=hero_seat,
            seat_players=tuple(range(6)),
            bounties=tuple(bounties) if config.bounty is not None else (),
            starting_bounty=config.starting_bounty,
        )
    )
    base = hand.observe(hero_seat)
    legal = hand.legal_actions(hero_seat)
    table: dict[str, list[float | None]] = {}
    for name in hand_classes():
        observation = replace(base, hole_cards=hole_cards(name))
        explanation = explainer.explain(observation, legal, mode)
        values: list[float | None] = [
            round(explanation.probabilities[action], 4) for action in ACTIONS
        ]
        per_action = explanation.details[0].per_action if explanation.details else {}
        for action in ACTIONS:
            value = per_action.get(action)
            if value is None and action == "raise":
                sizes = [item for key, item in per_action.items() if key.startswith("raise_")]
                value = max(sizes) if sizes else None
            values.append(None if value is None else round(value, 4))
        table[name] = values
    return table


def build_strategy(
    explainer: DecisionExplainer,
    config: TournamentConfig,
    mode: str,
    stacks: Sequence[float],
    bounties: Sequence[float],
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, object]:
    tables: dict[str, dict[str, list[float | None]]] = {}
    done = 0
    for scenario, hero, shover in spots():
        multiples = bounties if scenario == "call" and config.bounty is not None else (1.0,)
        for stack in stacks:
            for multiple in multiples:
                key = f"{scenario}|{hero}|{shover}|{stack:g}|{multiple:g}"
                tables[key] = build_table(
                    explainer, config, mode, stack, scenario, hero, shover, multiple
                )
                done += 1
                if on_progress is not None:
                    on_progress(done)
    return {
        "schema_version": STRATEGY_SCHEMA,
        "config": json.loads(config.to_json()),
        "mode": mode,
        "hands": hand_classes(),
        "actions": list(ACTIONS),
        "positions": list(POSITIONS),
        "stacks": list(stacks),
        "bounties": list(bounties) if config.bounty is not None else [1.0],
        "value_unit": "buy-in",
        "columns": [*(f"p_{action}" for action in ACTIONS), *(f"q_{action}" for action in ACTIONS)],
        "tables": tables,
    }


@dataclass(frozen=True, slots=True)
class StrategyTableExecutor:
    fetch_artifact: Callable[[str], bytes]
    output_kind: ArtifactKind = ArtifactKind.EVALUATION_RESULT
    output_media_type: str = "application/json"
    output_schema: str = STRATEGY_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        from tableforge.learning.experience import verified

        payload = StrategyTablePayload.from_bytes(payload_bytes)
        if payload.algorithm != "nfsp":
            raise ValueError(f"unsupported algorithm: {payload.algorithm}")
        from tableforge.learning.nfsp_explain import NfspExplainer

        explainer = NfspExplainer(
            verified(self.fetch_artifact(payload.artifact_id), payload.sha256)
        )
        result = build_strategy(
            explainer,
            TournamentConfig.from_json(payload.config_json),
            payload.mode,
            payload.stacks,
            payload.bounties,
            on_progress,
        )
        return json.dumps(result, separators=(",", ":")).encode()
