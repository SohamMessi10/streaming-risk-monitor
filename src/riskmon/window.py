"""Rolling statistics over a sliding event-time window.

`RollingStats` keeps the count, mean and variance of the values whose event time is within the
last `window_ms`, that is in (now - window_ms, now], updating them as values enter and leave
instead of recomputing from every value each time.

Why not the textbook formula, variance = (sum of squares - n * mean^2) / (n - 1)? It subtracts
two large, nearly equal numbers. For values near 1e9 that differ by about 1, both terms are near
1e24, double precision keeps about 16 digits, and the difference (about 1e2) is lost: the result
can even come out negative. Welford's method instead updates the mean and the sum of squared
deviations (m2) directly, which stays accurate.

Removing a value is Welford's update run backwards. It is less stable than adding: each
removal can leave a little rounding error in the mean and m2, and over millions of removals that
error can build up. So every `rebuild_every` removals, the window recomputes mean and m2 exactly
from the values it holds (`math.fsum`, two passes). That bounds the drift at the cost of one
pass over the window now and then; how much drift there is without it is measured in
docs/numerics.md, not assumed.

Rounding residue is the other risk. After a removal, values that are all equal can be left
with a variance of, say, 2e-28 instead of 0 (Hypothesis found this). Harmless on its own, but an
alert divides by the standard deviation: a residue of 1e-14 would turn any small return into a
z-score of millions. So the window keeps a bound on the rounding error its updates could have
introduced (`_noise`), and when the variance is positive but within that bound, `stats()`
recomputes exactly before answering. Exactly-equal values never trigger it: Welford's updates
leave them at exactly 0.

`reference_stats` is the same calculation from scratch, used as the reference in tests.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

DEFAULT_REBUILD_EVERY = 1_000
# Rounding error each add or remove can leave in m2, as a multiple of eps * (value^2 + mean^2):
# generous, since a bound that is too small would let residue through.
_NOISE_PER_UPDATE = 16 * 2.0 ** -52


@dataclass(frozen=True)
class Stats:
    count: int
    mean: float | None       # None with no values
    variance: float | None   # the sample variance (n - 1 denominator); None with fewer than 2 values

    @property
    def std(self) -> float | None:
        return None if self.variance is None else math.sqrt(self.variance)


def _mean_and_m2(values: list[float]) -> tuple[float, float]:
    """Mean and sum of squared deviations by the corrected two-pass method, with exact summation.

    fsum(values) / n is not always the representable mean: three copies of 100.1 sum to 300.3,
    which isn't a double, so the division gives 100.09999999999998 and the variance of three
    equal values comes out 3e-28. Adding back the mean deviation from that first estimate
    corrects it (Chan, Golub and LeVeque's corrected two-pass algorithm).
    """
    n = len(values)
    mean = math.fsum(values) / n
    mean += math.fsum(x - mean for x in values) / n
    return mean, math.fsum((x - mean) ** 2 for x in values)


def reference_stats(values: list[float]) -> Stats:
    """Count, mean and sample variance from scratch: the reference."""
    n = len(values)
    if n == 0:
        return Stats(0, None, None)
    mean, m2 = _mean_and_m2(values)
    return Stats(n, mean, m2 / (n - 1) if n > 1 else None)


class RollingStats:
    """Statistics of the values in the last `window_ms` of event time; values must arrive in
    event-time order (the event-time engine guarantees it)."""

    def __init__(self, window_ms: int, rebuild_every: int | None = DEFAULT_REBUILD_EVERY):
        if isinstance(window_ms, bool) or not isinstance(window_ms, int) or window_ms < 1:
            raise ValueError("the window must be a whole number of milliseconds, at least 1")
        if rebuild_every is not None and rebuild_every < 1:
            raise ValueError("rebuild_every must be at least 1, or None to never rebuild")
        self.window_ms = window_ms
        self.rebuild_every = rebuild_every
        self._values: deque[tuple[int, float]] = deque()
        self._mean = 0.0
        self._m2 = 0.0
        self._removals = 0
        self._noise = 0.0  # a bound on the rounding error in m2 since the last exact rebuild
        self._now: int | None = None
        self.rebuilds = 0

    def __len__(self) -> int:
        return len(self._values)

    @property
    def now(self) -> int | None:
        return self._now

    def values(self) -> list[float]:
        return [value for _, value in self._values]

    def stats(self) -> Stats:
        n = len(self._values)
        if n == 0:
            return Stats(0, None, None)
        if n > 1 and 0.0 < self._m2 <= self._noise:
            self.rebuild()  # the variance could be all rounding residue: recompute it exactly
        return Stats(n, self._mean, self._m2 / (n - 1) if n > 1 else None)

    def advance(self, now: int) -> None:
        """Move the window's end to `now` (event time), dropping values that fall out of it."""
        if self._now is not None and now < self._now:
            raise ValueError(f"event time went backwards ({now} after {self._now}); values must arrive in order")
        self._now = now
        cutoff = now - self.window_ms
        while self._values and self._values[0][0] <= cutoff:
            self._remove(self._values.popleft()[1])

    def add(self, time: int, value: float) -> None:
        """Add a value observed at `time`, after moving the window to it."""
        if not math.isfinite(value):
            raise ValueError(f"values must be finite, not {value!r}")
        self.advance(time)
        self._values.append((time, value))
        n = len(self._values)
        delta = value - self._mean
        self._mean += delta / n
        self._m2 += delta * (value - self._mean)
        self._noise += _NOISE_PER_UPDATE * (value * value + self._mean * self._mean)

    def _remove(self, value: float) -> None:
        n = len(self._values)  # the value has already left the deque
        if n == 0:
            self._mean, self._m2, self._noise = 0.0, 0.0, 0.0
        else:
            delta = value - self._mean
            self._mean -= delta / n
            self._m2 -= delta * (value - self._mean)
            self._noise += _NOISE_PER_UPDATE * (value * value + self._mean * self._mean)
            if self._m2 < 0.0:  # rounding can leave a tiny negative; a variance can't be
                self._m2 = 0.0
        self._removals += 1
        if self.rebuild_every is not None and self._removals % self.rebuild_every == 0:
            self.rebuild()

    def rebuild(self) -> None:
        """Recompute mean and m2 exactly from the values held, discarding accumulated rounding."""
        values = self.values()
        if not values:
            self._mean, self._m2 = 0.0, 0.0
        else:
            self._mean, self._m2 = _mean_and_m2(values)
        self._noise = 0.0  # exact now, up to the reference's own rounding
        self.rebuilds += 1
