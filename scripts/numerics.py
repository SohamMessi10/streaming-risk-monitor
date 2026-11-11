"""Reproduce docs/numerics.md: the textbook formula's failure, and drift with and without rebuilds.

    python scripts/numerics.py            # a million updates per row, about 6 seconds
    python scripts/numerics.py 100000     # fewer, faster
"""

import math
import random
import sys
import time

from riskmon.window import RollingStats, reference_stats


def textbook_variance(values: list[float]) -> float:
    """(Σx² − (Σx)²/n) / (n − 1): fine on paper, catastrophic in floating point."""
    n = len(values)
    return (sum(x * x for x in values) - sum(values) ** 2 / n) / (n - 1)


def drift(make, n_events: int, window: int, rebuild_every: int | None) -> tuple[float, float, float]:
    """Worst relative error of mean and variance against the reference, and the time taken."""
    rng = random.Random(11)
    stats = RollingStats(window, rebuild_every=rebuild_every)
    worst_mean = worst_variance = 0.0
    started = time.perf_counter()
    for i in range(n_events):
        stats.add(i, make(rng))
        if i % 997 == 0 and len(stats) > 1:
            ref, got = reference_stats(stats.values()), stats.stats()
            worst_mean = max(worst_mean, abs(got.mean - ref.mean) / max(abs(ref.mean), math.sqrt(ref.variance)))
            worst_variance = max(worst_variance, abs(got.variance - ref.variance) / ref.variance)
    return worst_mean, worst_variance, time.perf_counter() - started


def main() -> None:
    n_events = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    rng = random.Random(7)
    values = [1e9 + rng.gauss(0, 1) for _ in range(1_000)]
    stats = RollingStats(10**9)
    for i, x in enumerate(values):
        stats.add(i, x)
    ref = reference_stats(values).variance
    print(f"1,000 values near 1e9 (spread 1): reference {ref:.6f}, Welford {stats.stats().variance:.6f}, "
          f"textbook {textbook_variance(values):.1f}")
    rows = [("returns, std 5e-4", lambda r: r.gauss(0, 5e-4)), ("prices, 100 +- 0.05", lambda r: 100 + r.gauss(0, 0.05)),
            ("readings, 1e9 +- 1", lambda r: 1e9 + r.gauss(0, 1))]
    for name, make in rows:
        for rebuild in (None, 1_000):
            mean, variance, took = drift(make, n_events, 1_000, rebuild)
            print(f"{name:<20} rebuild {str(rebuild):<5} worst relative error: mean {mean:.1e}, "
                  f"variance {variance:.1e} ({took:.1f} s)")


if __name__ == "__main__":
    main()
