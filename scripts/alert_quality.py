"""Measure the alert rule on synthetic feeds: false alerts with no jumps, and detection by jump size.

    python scripts/alert_quality.py            # the numbers in docs/alerts.md
    python scripts/alert_quality.py --quick    # fewer events, for a fast look

1. **False alerts.** Feeds with no jumps, so every alert is false; the cooldown is off (each
   |z| is counted), so it can't hide any. The expected count is exact for normal returns: with a
   mean and sample standard deviation from n earlier returns, a new return's z is Student's t
   with n - 1 degrees of freedom times sqrt(1 + 1/n) (the prediction-interval result), so

       P(|z| >= t) = P(|T_{n-1}| >= t / sqrt(1 + 1/n))

   summed over every eligible return with its own n. The normal tail, erfc(t / sqrt(2)), is
   shown too: it is what you'd expect if the standard deviation were known, and it undercounts.
   Run twice: with the generator's late events (a dropped event makes the next return span two
   steps, sqrt(2) times wider) and without them.
2. **Detection by jump size.** The default rule (cooldown included) on feeds with jumps of
   3 to 8 standard deviations; recall, and why jumps were missed.

Everything is seeded, so a rerun gives the same counts; the timings vary by machine.
"""

from __future__ import annotations

import argparse
import math
import time

from riskmon.alerts import AlertRule, z_score
from riskmon.engine import Engine, Settings
from riskmon.generate import FeedSpec, generate
from riskmon.replay import replay

SYMBOLS = tuple(f"S{i:02d}" for i in range(10))


def _beta_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction for the incomplete beta function (modified Lentz's method)."""
    tiny, c, d = 1e-300, 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    result = d
    for m in range(1, 1000):
        for numerator in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
                          -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            result *= c * d
        if abs(c * d - 1.0) < 1e-15:
            return result
    raise ArithmeticError("the incomplete beta fraction did not converge")


def regularized_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def t_two_sided(t: float, df: int) -> float:
    """P(|T| >= t) for Student's t with df degrees of freedom."""
    return regularized_beta(df / 2.0, 0.5, df / (df + t * t))


def _t_two_sided_even(t: float, df: int) -> float:
    """The closed form for even df, a finite sum (an independent check on the general function):
    P(|T| >= t) = 1 - t / sqrt(df + t^2) * sum_{j < df/2} C(2j, j) / 4^j * (df / (df + t^2))^j."""
    q, term, total = df / (df + t * t), 1.0, 0.0
    for j in range(df // 2):
        total += term
        term *= q * (2 * j + 1) / (2 * j + 2)   # C(2j+2, j+1) / 4^(j+1) from C(2j, j) / 4^j
    return 1.0 - t / math.sqrt(df + t * t) * total


def _check_t_tail() -> None:
    """Against closed forms, over the range the replay uses (windows of up to ~1,000 returns).
    Not for huge df: there lgamma's large, nearly equal terms cancel and lose digits."""
    for t in (0.5, 1.0, 4.0, 30.0):
        assert math.isclose(t_two_sided(t, 1), 1 - 2 / math.pi * math.atan(t), rel_tol=1e-12), t
    for df in (2, 4, 30, 300, 1_000):
        for t in (1.0, 4.0, 5.0, 6.0):
            assert math.isclose(t_two_sided(t, df), _t_two_sided_even(t, df), rel_tol=1e-6), (t, df)


def z_values(events, min_count: int) -> list[tuple[float, int, bool]]:
    """(z, n, spans) for every return compared against a window of n >= min_count returns, where
    `spans` says the return covers a dropped event (its seq follows a gap in the released seqs)."""
    engine, out, previous = Engine(Settings()), [], {}
    for update in [u for e in events for u in engine.push(e)] + engine.flush():
        z = z_score(update)
        last = previous.get(update.symbol)
        previous[update.symbol] = update.seq
        if z is not None and update.before.count >= min_count:
            out.append((z, update.before.count, last is not None and update.seq - last > 1))
    return out


def false_alerts(events_per_symbol: int, seeds: range, late: bool) -> None:
    min_count = AlertRule().min_count
    thresholds = (4.0, 4.5, 5.0, 5.5, 6.0)
    fired = dict.fromkeys(thresholds, 0)
    spanning = dict.fromkeys(thresholds, 0)   # of those, returns that span a dropped event
    windows: dict[int, int] = {}   # window size n -> eligible returns compared against it
    eligible = 0
    for seed in seeds:
        spec = FeedSpec(symbols=SYMBOLS, seed=seed, events_per_symbol=events_per_symbol, jump_rate=0.0,
                        late_rate=0.002 if late else 0.0)
        events, _ = generate(spec)
        zs = z_values(events, min_count)
        eligible += len(zs)
        for _, n, _ in zs:
            windows[n] = windows.get(n, 0) + 1
        for t in thresholds:
            fired[t] += sum(1 for z, _, _ in zs if abs(z) >= t)
            spanning[t] += sum(1 for z, _, spans in zs if abs(z) >= t and spans)
    label = "with late events" if late else "without late events"
    print(f"\nFalse alerts, no jumps, {label}: {eligible:,} eligible returns "
          f"({len(seeds)} seeds x {len(SYMBOLS)} symbols x {events_per_symbol:,} events)")
    print(f"{'|z| >=':>8} {'alerts':>8} {'span a drop':>12} {'expected (t)':>13} {'normal tail':>12} "
          f"{'per million':>12}")
    for t in thresholds:
        exact = sum(k * t_two_sided(t / math.sqrt(1 + 1 / n), n - 1) for n, k in windows.items())
        normal = math.erfc(t / math.sqrt(2)) * eligible
        print(f"{t:>8} {fired[t]:>8,} {spanning[t]:>12,} {exact:>13.1f} {normal:>12.1f} "
              f"{fired[t] / eligible * 1e6:>12.2f}")


def detection(events_per_symbol: int, seeds: range) -> None:
    rule = AlertRule()
    print(f"\nDetection by jump size (default rule: |z| >= {rule.threshold}, min count {rule.min_count}, "
          f"cooldown {rule.cooldown_ms:,} ms; {len(seeds)} seeds x {len(SYMBOLS)} symbols x {events_per_symbol:,} events)")
    print(f"{'jump':>6} {'jumps':>6} {'found':>6} {'recall':>7} {'false':>6}  missed by reason")
    for sigmas in (3.0, 4.0, 5.0, 6.0, 8.0):
        jumps = found = false = 0
        reasons: dict[str, int] = {}
        for seed in seeds:
            spec = FeedSpec(symbols=SYMBOLS, seed=seed, events_per_symbol=events_per_symbol, jump_sigmas=sigmas)
            events, truth = generate(spec)
            evaluation = replay(events, Settings(), rule, truth.jumps).report(True)["evaluation"]
            jumps += evaluation["jumps"]
            found += evaluation["detected"]
            false += evaluation["false_alerts"]
            for reason, n in evaluation["missed_by_reason"].items():
                reasons[reason] = reasons.get(reason, 0) + n
        missed = ", ".join(f"{reason} {n}" for reason, n in sorted(reasons.items())) or "none"
        print(f"{sigmas:>6} {jumps:>6} {found:>6} {found / jumps:>7.1%} {false:>6}  {missed}")


def throughput(events_per_symbol: int) -> None:
    spec = FeedSpec(symbols=SYMBOLS, seed=1, events_per_symbol=events_per_symbol)
    events, _ = generate(spec)
    best = math.inf
    for _ in range(3):
        started = time.perf_counter()
        replay(events)
        best = min(best, time.perf_counter() - started)
    print(f"\nThroughput (engine and alerts, events in memory, best of 3): {len(events):,} events in "
          f"{best:.2f} s = {len(events) / best:,.0f} events/s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true")
    quick = parser.parse_args().quick
    _check_t_tail()
    n = 2_000 if quick else 20_000
    seeds = range(1, 3) if quick else range(1, 11)
    false_alerts(n, seeds, late=True)
    false_alerts(n, seeds, late=False)
    detection(3_600, range(1, 6) if quick else range(1, 31))
    throughput(n)


if __name__ == "__main__":
    main()
