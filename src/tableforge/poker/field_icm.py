"""Field ICM for MTT stages (T5): Malmuth-Harville equity over a large field.

Exact Harville is exponential in the number of players. Here it is computed as the
equivalent exponential race: player i "finishes" at T_i ~ Exp(stack_i) and places are in
reverse order of T. Given T_i = t every other player has finished before i independently
with probability 1 - exp(-s_j t), so P(place) is a Poisson-binomial integrated over t with
Gauss-Legendre quadrature. Measured against the exact recursion (`tournament.icm_equities`):
below 0.01% of a buy-in. The other tables' part is cached per node set.

Needs NumPy (the `learning` extra); `mtt_stage` imports it only when equity is needed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

QUADRATURE_NODES = 96


def _poisson_binomial(ahead: NDArray[np.float64], places: int) -> NDArray[np.float64]:
    """Rows of `ahead` are quadrature nodes; columns are players. Returns, per node, the
    probability that exactly k of them are ahead (k < places; the rest is dropped)."""
    counts = np.zeros((ahead.shape[0], places))
    counts[:, 0] = 1.0
    for column in range(ahead.shape[1]):
        p = ahead[:, column : column + 1]
        shifted = np.zeros_like(counts)
        shifted[:, 1:] = counts[:, :-1]
        counts = counts * (1.0 - p) + shifted * p
    return counts


def _nodes(rate: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Times t and weights dt so that sum f(t) dt integrates over t >= 0 for a player
    whose finishing time has this rate (t = -log(1-u) / rate, u Gauss-Legendre on [0,1])."""
    x, w = np.polynomial.legendre.leggauss(QUADRATURE_NODES)
    u = (x + 1.0) / 2.0
    t = -np.log1p(-u) / rate
    return t, w / 2.0 / ((1.0 - u) * rate)


class FieldIcm:
    """ICM equity of the players at our table, the rest of the field held fixed.

    `others` are the stacks at the other tables; `prizes[k]` is paid for field place k+1.
    The other tables' part is computed once per node set (one per half-octave of stack
    size) and reused while the field does not change.
    """

    def __init__(self, others: Sequence[float], prizes: Sequence[float]) -> None:
        self.others = np.asarray(others, dtype=np.float64)
        if np.any(self.others <= 0):
            raise ValueError("stacks in the field must be positive")
        self.prizes = np.asarray(prizes, dtype=np.float64)
        self._field: dict[
            int, tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]
        ] = {}

    def _places(self, table: int) -> int:
        return min(len(self.prizes), len(self.others) + table)

    def _field_counts(
        self, stack: float, places: int
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        key = round(math.log2(stack) * 2)
        cached = self._field.get(key)
        if cached is None or cached[2].shape[1] != places:
            t, dt = _nodes(2.0 ** (key / 2))
            counts = _poisson_binomial(-np.expm1(-np.outer(t, self.others)), places)
            cached = (t, dt, counts)
            self._field[key] = cached
        return cached

    def table_ladder(self, table: Sequence[float]) -> list[float]:
        """What finishing 1st, 2nd, ... among our table's players is worth (buy-ins).

        For a player with the table's average stack: the expected field prize given that
        exactly k-1 of the other players at our table finish ahead of them. This is the
        table's view of the field's payout (it shows the bubble), used where a single
        table's prize list is expected (the Push/Fold Nash baseline, model inputs).
        """
        stacks = np.asarray(table, dtype=np.float64)
        count = len(stacks)
        places = self._places(count)
        prizes = np.concatenate([self.prizes[:places], np.zeros(count + 1)])
        average = float(stacks.mean())
        t, dt, field = self._field_counts(average, places)
        mates = stacks[1:] if count > 1 else stacks[:0]
        own = _poisson_binomial(-np.expm1(-np.outer(t, mates)), count)
        density = average * np.exp(-average * t) * dt
        ladder = []
        for k in range(count):
            # Field places: 1 + (players of other tables ahead) + k table mates ahead.
            values = np.zeros(len(t))
            for ahead in range(places):
                values += field[:, ahead] * prizes[ahead + k]
            weight = density * own[:, k]
            total = weight.sum()
            ladder.append(float(weight @ values / total) if total > 0 else 0.0)
        for index in range(1, count):
            ladder[index] = min(ladder[index], ladder[index - 1])
        return ladder

    def equities(self, table: Sequence[float]) -> list[float]:
        """Equity (buy-ins) of each player at our table; zero stacks are not allowed."""
        stacks = np.asarray(table, dtype=np.float64)
        if np.any(stacks <= 0):
            raise ValueError("busted players have no ICM equity")
        places = self._places(len(stacks))
        prizes = self.prizes[:places]
        result = []
        for index, stack in enumerate(stacks):
            t, dt, field = self._field_counts(float(stack), places)
            mates = np.delete(stacks, index)
            if len(mates):
                own = _poisson_binomial(-np.expm1(-np.outer(t, mates)), len(mates) + 1)
                counts = np.zeros_like(field)
                for k in range(min(len(mates) + 1, places)):
                    counts[:, k:] += field[:, : places - k] * own[:, k : k + 1]
            else:
                counts = field
            density = stack * np.exp(-stack * t) * dt
            result.append(float(density @ (counts @ prizes)))
        return result
