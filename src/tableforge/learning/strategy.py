"""Preflop strategy tables: what a model does with each of the 169 starting hands.

A strategy table job builds preflop spots directly on the table engine and asks the model
(through `DecisionExplainer`, so any algorithm works) what it would do with every starting
hand. Spots follow the model's tournament: positions for its table size, the blinds and
ante of a chosen level, and stack depths relative to its starting stack.

* ``open``: everyone before the hero folds; the hero may fold, call (limp), raise or shove.
* ``raise``: the ``opener`` raises to 2.5 big blinds, everyone else before the hero folds;
  the hero may fold, call, re-raise or shove.
* ``call``: the ``opener`` goes all in, everyone else before the hero folds; the hero
  decides whether to call. The shover's bounty can be 1x, 2x or 3x the starting bounty.

Every player has the same stack (the effective stack of the spot).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace

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
STACK_FACTORS = (0.3, 0.5, 0.8, 1.0, 1.5, 2.0)
"""Default stack depths, as multiples of the tournament's starting stack."""
DEFAULT_BOUNTIES = (1.0, 2.0, 3.0)
OPEN_RAISE_BB = 2.5
SCENARIOS = ("open", "raise", "call")


def positions(players: int) -> tuple[str, ...]:
    """Preflop acting order for a table of `players` (heads-up: SB is the button)."""
    if players == 2:
        return ("SB", "BB")
    return POSITIONS[6 - players :]


def seat_of(position: str, players: int) -> int:
    """Seat with the button at seat 0: SB 1, BB 2, then the early positions in order."""
    if players == 2:
        return {"SB": 0, "BB": 1}[position]
    order = positions(players)
    fixed = {"BTN": 0, "SB": 1, "BB": 2}
    if position in fixed:
        return fixed[position]
    return 3 + order.index(position)


def default_stacks(starting_stack_bb: float) -> tuple[float, ...]:
    """Stack depths around the starting stack (10 BB -> 3, 5, 8, 10, 15, 20)."""
    return tuple(
        sorted({max(1.0, float(round(starting_stack_bb * factor))) for factor in STACK_FACTORS})
    )


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
    stacks: tuple[float, ...] = ()
    """Stack depths in big blinds; empty = around the tournament's starting stack."""
    bounties: tuple[float, ...] = DEFAULT_BOUNTIES
    level: int = 1
    """Blind level (1 = first) whose blinds and ante the spots use."""

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported strategy table payload schema")
        config = TournamentConfig.from_json(self.config_json)
        if not 1 <= self.level <= len(config.blind_levels):
            raise ValueError(f"level must be between 1 and {len(config.blind_levels)}")
        if not self.stacks:
            object.__setattr__(self, "stacks", default_stacks(config.starting_stack_bb))
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
                "level": self.level,
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


def spots(players: int = 6) -> list[tuple[str, str, str]]:
    """(scenario, hero, opener) for every table in a strategy table job."""
    order = positions(players)
    found = [("open", hero, "") for hero in order[:-1]]
    for scenario in ("raise", "call"):
        for hero_index, hero in enumerate(order):
            for opener in order[:hero_index]:
                found.append((scenario, hero, opener))
    return found


def _spot_hand(
    config: TournamentConfig,
    level_number: int,
    stack_bb: float,
    scenario: str,
    hero: str,
    opener: str,
) -> tuple[TableHand, int]:
    level = config.blind_levels[level_number - 1]
    players = config.players
    stack = max(round(stack_bb * level.big_blind), level.big_blind + level.ante + 1)
    hand = TableHand([stack] * players, 0, level.small_blind, level.big_blind, 0, level.ante)
    hero_seat = seat_of(hero, players)
    opener_seat = seat_of(opener, players) if opener else -1
    while hand.betting.current_actor != hero_seat:
        actor = hand.betting.current_actor
        if actor is None:
            raise AssertionError(f"the hand ended before {hero} acted")
        legal = hand.legal_actions(actor)
        if actor == opener_seat and legal.max_raise_to is not None:
            if scenario == "call" or legal.min_raise_to is None:
                hand.act(actor, Action.raise_to(legal.max_raise_to))
            else:
                target = round(OPEN_RAISE_BB * level.big_blind)
                amount = min(max(target, legal.min_raise_to), legal.max_raise_to)
                hand.act(actor, Action.raise_to(amount))
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
    opener: str,
    bounty_multiple: float,
    level_number: int = 1,
) -> dict[str, list[float | None]]:
    """Probabilities (and value details) of every starting hand in one spot."""
    hand, hero_seat = _spot_hand(config, level_number, stack_bb, scenario, hero, opener)
    level = config.blind_levels[level_number - 1]
    players = config.players
    bounties = [config.starting_bounty] * players
    if opener:
        bounties[seat_of(opener, players)] = config.starting_bounty * bounty_multiple
    explainer.begin_hand(
        TournamentHandInfo(
            hand_number=sum(item.hands for item in config.blind_levels[: level_number - 1]),
            level=level_number,
            small_blind=level.small_blind,
            big_blind=level.big_blind,
            ante=level.ante,
            hands_to_next_level=level.hands,
            hands_per_level=level.hands,
            players_left=players,
            starting_stack=config.starting_stack,
            payout=config.payout,
            seat=hero_seat,
            player=hero_seat,
            seat_players=tuple(range(players)),
            bounties=tuple(bounties) if config.bounty is not None else (),
            starting_bounty=config.starting_bounty,
            prizes=tuple(config.payout_for_place(place) for place in range(1, players + 1)),
            bounty_cash_share=config.bounty.cash_share if config.bounty is not None else 0.5,
            winner_keeps_own=config.bounty.winner_keeps_own if config.bounty is not None else True,
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
    level: int = 1,
    pushfold_baseline: bool = True,
) -> dict[str, object]:
    tables: dict[str, dict[str, list[float | None]]] = {}
    done = 0
    for scenario, hero, opener in spots(config.players):
        multiples = bounties if scenario == "call" and config.bounty is not None else (1.0,)
        for stack in stacks:
            for multiple in multiples:
                key = f"{scenario}|{hero}|{opener}|{stack:g}|{multiple:g}"
                tables[key] = build_table(
                    explainer, config, mode, stack, scenario, hero, opener, multiple, level
                )
                done += 1
                if on_progress is not None:
                    on_progress(done)
    document: dict[str, object] = {
        "schema_version": STRATEGY_SCHEMA,
        "config": json.loads(config.to_json()),
        "mode": mode,
        "hands": hand_classes(),
        "actions": list(ACTIONS),
        "scenarios": list(SCENARIOS),
        "positions": list(positions(config.players)),
        "players": config.players,
        "starting_stack_bb": config.starting_stack_bb,
        "level": level,
        "blinds": asdict(config.blind_levels[level - 1]),
        "open_raise_bb": OPEN_RAISE_BB,
        "stacks": list(stacks),
        "bounties": list(bounties) if config.bounty is not None else [1.0],
        "value_unit": "buy-in",
        "columns": [*(f"p_{action}" for action in ACTIONS), *(f"q_{action}" for action in ACTIONS)],
        "tables": tables,
    }
    if pushfold_baseline:
        document["pushfold_nash"] = pushfold_tables(config, stacks, bounties, level)
    return document


PUSHFOLD_MAX_STACK_BB = 25.0
"""Push/Fold Nash is only computed for stacks up to this depth (it is a short-stack model)."""


def pushfold_tables(
    config: TournamentConfig, stacks: Sequence[float], bounties: Sequence[float], level: int
) -> dict[str, object]:
    """The ICM Push/Fold Nash baseline for the ``open`` and ``call`` spots of a table.

    For ``open`` the value is the chance to shove; for ``call`` the chance to call. Each spot
    also reports ``gap``: how much any player could still gain by deviating (buy-ins).
    This is a simplified all-in-or-fold game, not a Nash equilibrium of full hold'em.
    """
    from tableforge.learning.pushfold_nash import BASELINE_NAME, solve, spot_game
    from tableforge.poker.pushfold_ranges import HAND_CLASSES

    players = config.players
    tables: dict[str, dict[str, float]] = {}
    gaps: dict[str, float] = {}
    solved: dict[tuple[float, float, int | None], object] = {}
    for scenario, hero, opener in spots(players):
        if scenario == "raise":
            continue
        multiples = bounties if scenario == "call" and config.bounty is not None else (1.0,)
        for stack in stacks:
            if stack > PUSHFOLD_MAX_STACK_BB:
                continue
            for multiple in multiples:
                shover = seat_of(opener, players) if opener else None
                cache_key = (stack, multiple, shover if multiple != 1.0 else None)
                solution = solved.get(cache_key)
                if solution is None:
                    solution = solve(
                        spot_game(config, level, stack, multiple, shover), 800, 0.0005
                    )
                    solved[cache_key] = solution
                hero_seat = seat_of(hero, players)
                if scenario == "open":
                    values = solution.push.get(hero_seat)  # type: ignore[attr-defined]
                else:
                    values = solution.call.get((shover, hero_seat))  # type: ignore[attr-defined]
                if values is None:
                    continue
                key = f"{scenario}|{hero}|{opener}|{stack:g}|{multiple:g}"
                tables[key] = {
                    name: round(value, 4) for name, value in zip(HAND_CLASSES, values, strict=True)
                }
                gaps[key] = round(solution.max_gap, 6)  # type: ignore[attr-defined]
    return {
        "name": BASELINE_NAME,
        "description": "ICM Push/Fold Nash: all-in or fold only; not a full-game equilibrium",
        "max_stack_bb": PUSHFOLD_MAX_STACK_BB,
        "tables": tables,
        "gap": gaps,
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
            payload.level,
        )
        return json.dumps(result, separators=(",", ":")).encode()
