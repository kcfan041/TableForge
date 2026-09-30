"""Push/Fold Nash baseline (ICM Push/Fold Nash) for short-stacked tournament play.

This is **not** a Nash equilibrium of full no-limit hold'em. It solves a simplified
preflop game that is the usual reference for short stacks:

* every player, in turn, may only go **all in or fold** until someone goes all in;
* after an all-in, each later player may only **call or fold**; the first caller closes
  the action (a single caller: overcalls are not modelled);
* the hand is then dealt out; hands are the 169 starting-hand classes, with all-in
  equities from `tableforge.poker.pushfold_ranges.preflop_equity()`; card removal is
  modelled between the all-in player and each caller, not across three or more players;
* money is valued with ICM (Malmuth-Harville) on the stacks after the hand, plus, for
  PKO, bounty cash won in the hand and the bounty on one's own head times one's chance of
  winning the tournament (chip share) when the winner keeps their own bounty. Bounties on
  other heads that could be won later are not valued.

The equilibrium is found by fictitious play (every decision best-responds to the average
of the others' strategies). ``gap`` reports, per player, how much a player could still
gain (in buy-ins, averaged over hands) by changing only their own strategy; a small gap
means the strategies are close to an equilibrium of this simplified game.

With a winner-take-all heads-up payout, ICM is linear in chips and the result is the
classic chip-EV heads-up push/fold chart.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from tableforge.poker.pushfold_ranges import (
    HAND_CLASSES,
    compatible_combos,
    preflop_equity,
    weights,
)
from tableforge.poker.tournament import icm_equities

BASELINE_NAME = "Push/Fold Nash"
"""How the UI and documents name this baseline (never just "Nash")."""


def action_order(players: int) -> tuple[int, ...]:
    """Seats in preflop acting order, with the button at seat 0.

    Heads-up: seat 0 is the button and small blind, seat 1 the big blind. Otherwise seat 1
    is the small blind, seat 2 the big blind and seats 3.. act first (UTG onwards).
    """
    if players == 2:
        return (0, 1)
    return (*range(3, players), 0, 1, 2)


def blind_seats(players: int) -> tuple[int, int]:
    return (0, 1) if players == 2 else (1, 2)


@dataclass(frozen=True, slots=True)
class PushFoldGame:
    """One preflop spot. Seats are numbered with the button at seat 0."""

    stacks: tuple[int, ...]
    small_blind: int
    big_blind: int
    ante: int
    prizes: tuple[float, ...]
    """Prize for places 1..n among these players, in buy-ins (missing places pay 0)."""
    heads: tuple[float, ...] = ()
    """Bounty on each seat's head in buy-ins (PKO); empty without bounties."""
    cash_share: float = 0.5
    winner_keeps_own: bool = True

    def __post_init__(self) -> None:
        if not 2 <= len(self.stacks) <= 9:
            raise ValueError("a push/fold spot has 2 to 9 players")
        if self.heads and len(self.heads) != len(self.stacks):
            raise ValueError("one bounty per seat")
        if not 0 < self.small_blind < self.big_blind or self.ante < 0:
            raise ValueError("invalid blinds")

    @property
    def players(self) -> int:
        return len(self.stacks)


@dataclass(frozen=True, slots=True)
class PushFoldSolution:
    game: PushFoldGame
    order: tuple[int, ...]
    push: dict[int, tuple[float, ...]]
    """Opening all-in probability per hand class, for each seat that can open."""
    call: dict[tuple[int, int], tuple[float, ...]]
    """Call probability per hand class, for (all-in seat, calling seat)."""
    gap: tuple[float, ...]
    """Per seat: what that player could still gain by deviating, in buy-ins."""
    iterations: int
    values: tuple[float, ...] = field(default=())
    """Expected value of the spot per seat under the solution, in buy-ins."""

    @property
    def max_gap(self) -> float:
        return max(self.gap)


class _Outcomes:
    """Values (per seat) of every way the hand can end."""

    def __init__(self, game: PushFoldGame) -> None:
        self.game = game
        players = game.players
        sb, bb = blind_seats(players)
        stacks = list(game.stacks)
        # A blind that would be all in just by posting is given one extra chip: this game
        # has no decision for such a player.
        minimum = {sb: game.small_blind + 1, bb: game.big_blind + game.ante + 1}
        for seat, least in minimum.items():
            stacks[seat] = max(stacks[seat], least)
        self.stacks = stacks
        self.sb, self.bb = sb, bb
        self.post = [0] * players
        self.post[sb] = game.small_blind
        self.post[bb] = game.big_blind
        self.total = sum(stacks)

    def value(
        self, stacks: Sequence[int], busted: int | None, eliminator: int | None
    ) -> np.ndarray:
        game = self.game
        players = game.players
        heads = list(game.heads) if game.heads else [0.0] * players
        cash = [0.0] * players
        values = np.zeros(players)
        alive = [seat for seat in range(players) if seat != busted]
        if busted is not None and eliminator is not None and game.heads:
            bounty = heads[busted]
            cash[eliminator] += bounty * game.cash_share
            heads[eliminator] += bounty * (1 - game.cash_share)
            heads[busted] = 0.0
        prizes = list(game.prizes) + [0.0] * players
        shares = icm_equities([stacks[seat] for seat in alive], prizes[: len(alive)])
        total = sum(stacks)
        for index, seat in enumerate(alive):
            own = heads[seat] * stacks[seat] / total if game.winner_keeps_own else 0.0
            values[seat] = shares[index] + cash[seat] + own
        if busted is not None:
            values[busted] = prizes[len(alive)]
        return values

    def walk(self) -> np.ndarray:
        stacks = list(self.stacks)
        stacks[self.sb] -= self.post[self.sb]
        stacks[self.bb] += self.post[self.sb]
        return self.value(stacks, None, None)

    def steal(self, opener: int) -> np.ndarray:
        stacks = list(self.stacks)
        won = 0
        for seat in (self.sb, self.bb):
            if seat != opener:
                paid = self.post[seat] + (self.game.ante if seat == self.bb else 0)
                stacks[seat] -= paid
                won += paid
        stacks[opener] += won
        return self.value(stacks, None, None)

    def showdown(self, opener: int, caller: int, winner: int) -> np.ndarray:
        game = self.game
        live = {
            seat: self.stacks[seat] - (game.ante if seat == self.bb else 0)
            for seat in (opener, caller)
        }
        effective = min(live.values())
        contribution = [0] * game.players
        for seat in (self.sb, self.bb):
            contribution[seat] = self.post[seat]
        contribution[self.bb] += game.ante
        contribution[opener] = effective + (game.ante if opener == self.bb else 0)
        contribution[caller] = effective + (game.ante if caller == self.bb else 0)
        stacks = [stack - paid for stack, paid in zip(self.stacks, contribution, strict=True)]
        stacks[winner] += sum(contribution)
        loser = caller if winner == opener else opener
        busted = loser if stacks[loser] == 0 else None
        return self.value(stacks, busted, winner if busted is not None else None)


def spot_game(
    config: object,
    level_number: int,
    stack_bb: float,
    shover_bounty_multiple: float = 1.0,
    shover_seat: int | None = None,
) -> PushFoldGame:
    """The push/fold game of a strategy-table spot: equal stacks, the tournament's blinds
    at `level_number`, all its players left, starting bounties (the shover's multiplied)."""
    from tableforge.poker.tournament import TournamentConfig

    assert isinstance(config, TournamentConfig)
    level = config.blind_levels[level_number - 1]
    players = config.players
    stack = max(round(stack_bb * level.big_blind), level.big_blind + level.ante + 1)
    heads: tuple[float, ...] = ()
    bounty = config.bounty
    if bounty is not None:
        values = [config.starting_bounty] * players
        if shover_seat is not None:
            values[shover_seat] = config.starting_bounty * shover_bounty_multiple
        heads = tuple(values)
    return PushFoldGame(
        (stack,) * players,
        level.small_blind,
        level.big_blind,
        level.ante,
        tuple(config.payout_for_place(place) for place in range(1, players + 1)),
        heads=heads,
        cash_share=bounty.cash_share if bounty is not None else 0.5,
        winner_keeps_own=bounty.winner_keeps_own if bounty is not None else True,
    )


def solve(game: PushFoldGame, iterations: int = 2_000, tolerance: float = 0.0) -> PushFoldSolution:
    """Fictitious play on the push/fold game; see the module docstring.

    With `tolerance` > 0 it stops early (checked every 25 iterations) once no player can
    gain more than `tolerance` buy-ins by deviating.
    """
    equity = np.array(preflop_equity())
    w = np.array(weights())
    players = game.players
    order = action_order(players)
    openers = order[:-1]
    outcomes = _Outcomes(game)
    walk = outcomes.walk()
    steal = {opener: outcomes.steal(opener) for opener in openers}
    callers = {opener: order[order.index(opener) + 1 :] for opener in openers}
    win = {
        (opener, caller): outcomes.showdown(opener, caller, opener)
        for opener in openers
        for caller in callers[opener]
    }
    lose = {
        (opener, caller): outcomes.showdown(opener, caller, caller)
        for opener in openers
        for caller in callers[opener]
    }
    classes = len(HAND_CLASSES)
    push = {opener: np.full(classes, 0.5) for opener in openers}
    call = {key: np.full(classes, 0.5) for key in win}
    # Card removal between the all-in player and a caller: joint[i][j] is the chance that
    # they hold classes i and j; joint_win adds "and the all-in player wins".
    pairs = np.array(compatible_combos(), dtype=float)
    joint = pairs / pairs.sum()
    joint_win = joint * equity
    # Row-normalised versions: conditional on one's own class.
    given = joint / w[:, None]
    given_win = joint_win / w[:, None]
    given_t = joint.T / w[:, None]
    given_win_t = joint_win.T / w[:, None]

    def evaluate(
        push: dict[int, np.ndarray], call: dict[tuple[int, int], np.ndarray]
    ) -> tuple[
        dict[int, tuple[np.ndarray, np.ndarray]],
        dict[tuple[int, int], tuple[np.ndarray, np.ndarray]],
        dict[int, float],
        dict[tuple[int, int], float],
        np.ndarray,
    ]:
        """For every decision: EV per own class of the aggressive action and of folding,
        and the chance the decision is reached; plus the value of the spot per seat.

        Card removal is modelled between the all-in player and each caller; whether a
        later player calls is treated as independent of an earlier caller's cards.
        """
        rate = {opener: float(w @ push[opener]) for opener in openers}
        # called[o, c]: chance c calls o's all-in (o's range, card removal) if reached.
        called = {
            (opener, caller): float(push[opener] @ joint @ call[opener, caller]) / rate[opener]
            if rate[opener] > 0
            else 0.0
            for opener, caller in call
        }
        wins_when_called = {
            (opener, caller): float(push[opener] @ joint_win @ call[opener, caller])
            / (rate[opener] * called[opener, caller])
            if rate[opener] > 0 and called[opener, caller] > 0
            else 0.5
            for opener, caller in call
        }

        def after(opener: int, start: int) -> np.ndarray:
            """Every seat's value once `opener` is all in and callers from `start` on
            may call (averaged over the ranges)."""
            result = np.zeros(players)
            remaining = 1.0
            for caller in callers[opener][start:]:
                chance = called[opener, caller]
                wins = wins_when_called[opener, caller]
                result += (
                    remaining
                    * chance
                    * (wins * win[opener, caller] + (1 - wins) * lose[opener, caller])
                )
                remaining *= 1 - chance
            return result + remaining * steal[opener]

        continuation: list[np.ndarray] = [walk] * (len(openers) + 1)
        for index in range(len(openers) - 1, -1, -1):
            opener = openers[index]
            continuation[index] = (
                rate[opener] * after(opener, 0) + (1 - rate[opener]) * continuation[index + 1]
            )

        open_ev: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        call_ev: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        open_reach: dict[int, float] = {}
        call_reach: dict[tuple[int, int], float] = {}
        reach = 1.0
        for index, opener in enumerate(openers):
            open_reach[opener] = reach
            # Shoving, per own class: each caller calls with P(call | my class).
            shove_ev = np.zeros(classes)
            remaining = np.ones(classes)
            for caller in callers[opener]:
                calls = given @ call[opener, caller]
                won = given_win @ call[opener, caller]
                shove_ev += remaining * (
                    won * win[opener, caller][opener] + (calls - won) * lose[opener, caller][opener]
                )
                remaining = remaining * (1 - calls)
            shove_ev += remaining * steal[opener][opener]
            open_ev[opener] = (shove_ev, np.full(classes, continuation[index + 1][opener]))
            caller_reach = reach * rate[opener]
            for position, caller in enumerate(callers[opener]):
                call_reach[opener, caller] = caller_reach
                # Calling, per own class: the opener's range given my cards.
                shoves = given_t @ push[opener]
                beaten = given_win_t @ push[opener]
                safe = np.where(shoves > 0, shoves, 1.0)
                # `beaten`: the all-in player wins, i.e. outcome `win[opener, caller]`.
                lose_share = np.where(shoves > 0, beaten / safe, 0.5)
                call_value = (
                    lose_share * win[opener, caller][caller]
                    + (1 - lose_share) * lose[opener, caller][caller]
                )
                fold_value = after(opener, position + 1)[caller]
                call_ev[opener, caller] = (call_value, np.full(classes, fold_value))
                caller_reach *= 1 - called[opener, caller]
            reach *= 1 - rate[opener]
        return open_ev, call_ev, open_reach, call_reach, continuation[0]

    def gaps(
        open_ev: dict[int, tuple[np.ndarray, np.ndarray]],
        call_ev: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]],
        open_reach: dict[int, float],
        call_reach: dict[tuple[int, int], float],
    ) -> np.ndarray:
        gap = np.zeros(players)
        for opener, (ev_push, ev_fold) in open_ev.items():
            current = push[opener] * ev_push + (1 - push[opener]) * ev_fold
            best = np.maximum(ev_push, ev_fold)
            gap[opener] += open_reach[opener] * float(w @ (best - current))
        for key, (ev_call, ev_fold) in call_ev.items():
            current = call[key] * ev_call + (1 - call[key]) * ev_fold
            best = np.maximum(ev_call, ev_fold)
            gap[key[1]] += call_reach[key] * float(w @ (best - current))
        return gap

    done = 0
    for step in range(1, iterations + 1):
        open_ev, call_ev, open_reach, call_reach, _ = evaluate(push, call)
        checkpoint = tolerance > 0 and step % 25 == 0
        if checkpoint and gaps(open_ev, call_ev, open_reach, call_reach).max() <= tolerance:
            break
        rate_of_change = 1.0 / (step + 1)
        for opener, (ev_push, ev_fold) in open_ev.items():
            push[opener] += ((ev_push > ev_fold + 1e-12) - push[opener]) * rate_of_change
        for key, (ev_call, ev_fold) in call_ev.items():
            call[key] += ((ev_call > ev_fold + 1e-12) - call[key]) * rate_of_change
        done = step

    open_ev, call_ev, open_reach, call_reach, values = evaluate(push, call)
    gap = gaps(open_ev, call_ev, open_reach, call_reach)
    return PushFoldSolution(
        game=game,
        order=order,
        push={seat: tuple(float(value) for value in push[seat]) for seat in openers},
        call={key: tuple(float(value) for value in call[key]) for key in call},
        gap=tuple(float(value) for value in gap),
        iterations=done,
        values=tuple(float(value) for value in values),
    )
