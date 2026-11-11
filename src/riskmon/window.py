"""Rolling statistics over a sliding event-time window: starting with the reference.

`reference_stats` computes count, mean and sample variance from scratch with exact summation.
The streaming window, next, is tested against it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


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
