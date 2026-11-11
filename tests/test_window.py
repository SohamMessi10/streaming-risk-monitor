import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from riskmon.window import RollingStats, Stats, reference_stats


def close(got: Stats, ref: Stats, history: float) -> bool:
    """Within the tolerance in docs/numerics.md. Removing a value leaves rounding error on the
    scale of that value, so the allowance is relative to `history`, the largest magnitude that
    has been in the window, not only to what it holds now."""
    if got.count != ref.count or (got.mean is None) != (ref.mean is None):
        return False
    if got.mean is None:
        return True
    if abs(got.mean - ref.mean) > 1e-12 * history:
        return False
    if (got.variance is None) != (ref.variance is None):
        return False
    # Below about 2.2e-308, doubles are subnormal and keep only a few significant digits, so
    # values near 1e-160 (whose squares are near 1e-319) also get a tiny absolute allowance.
    return got.variance is None or (
        abs(got.variance - ref.variance) <= 1e-9 * ref.variance + 1e-12 * history ** 2 + 1e-320)


# --- the reference ------------------------------------------------------------------------------


def test_the_reference_matches_values_worked_out_by_hand():
    assert reference_stats([]) == Stats(0, None, None)
    assert reference_stats([3.0]) == Stats(1, 3.0, None)
    assert reference_stats([1.0, 2.0, 3.0, 4.0]) == Stats(4, 2.5, 5 / 3)  # sum of squares 5, n - 1 = 3
    assert reference_stats([2.0, 4.0]).std == math.sqrt(2.0)


def test_the_reference_gives_equal_values_exactly_zero_variance():
    """fsum(values) / n alone gives 100.09999999999998 and a variance of 3e-28 here."""
    assert reference_stats([100.1, 100.1, 100.1]) == Stats(3, 100.1, 0.0)


# --- the window -------------------------------------------------------------------------------


def test_values_leave_when_they_fall_out_of_the_window():
    w = RollingStats(window_ms=10)
    for t, x in [(0, 1.0), (5, 2.0), (10, 3.0)]:
        w.add(t, x)
    assert w.values() == [2.0, 3.0]  # the window is (10 - 10, 10]: time 0 is out
    w.advance(14)
    assert w.values() == [2.0, 3.0]
    w.advance(15)
    assert w.values() == [3.0] and w.stats() == Stats(1, 3.0, None)
    w.advance(100)
    assert len(w) == 0 and w.stats() == Stats(0, None, None) and w.now == 100


def test_equal_times_are_fine_but_time_cannot_go_backwards():
    w = RollingStats(window_ms=10)
    w.add(5, 1.0)
    w.add(5, 3.0)
    assert w.stats() == Stats(2, 2.0, 2.0)
    with pytest.raises(ValueError, match="went backwards"):
        w.add(4, 1.0)
    with pytest.raises(ValueError, match="went backwards"):
        w.advance(4)


def test_an_emptied_window_starts_again_cleanly():
    w = RollingStats(window_ms=5, rebuild_every=None)
    for t, x in [(0, 1e6), (1, -1e6), (2, 7.0)]:
        w.add(t, x)
    w.advance(50)
    w.add(51, 4.0)
    w.add(52, 6.0)
    assert w.stats() == Stats(2, 5.0, 2.0)  # no leftover from the values that left


def test_constant_values_give_exactly_zero_variance():
    w = RollingStats(window_ms=100)
    for t in range(1_000):
        w.add(t, 101.37)
        if len(w) > 1:
            assert w.stats().variance == 0.0 and w.stats().mean == 101.37


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_refused(value):
    with pytest.raises(ValueError, match="finite"):
        RollingStats(window_ms=10).add(0, value)


@pytest.mark.parametrize(("window", "rebuild"), [(0, 10), (-5, 10), (1.5, 10), (True, 10), (10, 0)])
def test_bad_settings_are_refused(window, rebuild):
    with pytest.raises(ValueError):
        RollingStats(window_ms=window, rebuild_every=rebuild)


def test_rebuilds_happen_every_n_removals():
    w = RollingStats(window_ms=10, rebuild_every=25)
    for t in range(110):
        w.add(t, float(t % 7))
    assert w.rebuilds == (110 - 10) // 25


# --- streaming equals the reference -----------------------------------------------------------

scales = st.sampled_from([(0.0, 5e-4), (0.0, 1.0), (100.0, 0.05), (1e6, 1.0), (1e9, 1.0), (-3e4, 1e-3), (0.0, 1e6)])


@st.composite
def streams(draw):
    centre, spread = draw(scales)
    steps = draw(st.lists(st.tuples(st.integers(0, 40), st.floats(-6, 6)), min_size=1, max_size=300))
    return draw(st.integers(1, 100)), [(gap, centre + spread * z) for gap, z in steps], draw(st.sampled_from([None, 1, 7, 50]))


@given(streams())
def test_streaming_statistics_equal_the_reference_after_every_value(stream):
    window, steps, rebuild = stream
    w = RollingStats(window_ms=window, rebuild_every=rebuild)
    seen, now = [], 0
    for gap, value in steps:
        now += gap
        w.add(now, value)
        seen.append((now, value))
        expected = [x for t, x in seen if t > now - window]
        assert w.values() == expected
        history = max(abs(x) for _, x in seen)
        assert close(w.stats(), reference_stats(expected), history), (w.stats(), reference_stats(expected))
        if len(set(expected)) == 1 and len(expected) > 1:
            # what alerts rely on: equal values have no spread, not a rounding residue of one
            assert w.stats().variance == 0.0, w.stats()


def test_the_textbook_formula_goes_negative_where_welford_does_not():
    """The example in docs/numerics.md: the reason the window uses Welford's method."""
    rng = random.Random(7)
    values = [1e9 + rng.gauss(0, 1) for _ in range(1_000)]
    textbook = (sum(x * x for x in values) - sum(values) ** 2 / len(values)) / (len(values) - 1)
    w = RollingStats(window_ms=10**9)
    for t, x in enumerate(values):
        w.add(t, x)
    ref = reference_stats(values)
    assert textbook < 0 < ref.variance
    assert abs(w.stats().variance - ref.variance) / ref.variance < 1e-7


def test_values_left_equal_after_a_removal_have_exactly_zero_variance():
    """The case Hypothesis found: residue of 2e-28 would make any small return look like a
    z-score of millions."""
    w = RollingStats(window_ms=2, rebuild_every=None)
    for t, x in [(0, 1.169463931534942e-06), (0, 0.0), (0, 0.0), (1, 0.0), (1, 0.0), (2, 0.0)]:
        w.add(t, x)
    assert w.values() == [0.0, 0.0, 0.0] and w.stats().variance == 0.0


def test_rebuilding_bounds_the_drift():
    """Prices near 100 in a 1,000-value window, where the residue guard never fires: the
    periodic rebuilds keep the drift far smaller."""
    def worst(rebuild):
        rng = random.Random(11)
        w = RollingStats(window_ms=1_000, rebuild_every=rebuild)
        error = 0.0
        for t in range(300_000):
            w.add(t, 100 + rng.gauss(0, 0.05))
            if t % 997 == 0 and len(w) > 1:
                ref = reference_stats(w.values())
                error = max(error, abs(w.stats().variance - ref.variance) / ref.variance)
        return error
    assert worst(1_000) * 10 < worst(None)


def test_the_residue_guard_costs_nothing_for_centred_values():
    """Returns sit near zero, so reading the statistics after every update never triggers an
    exact recompute beyond the scheduled rebuilds (docs/numerics.md)."""
    rng = random.Random(3)
    w = RollingStats(window_ms=1_000)
    for t in range(21_000):
        w.add(t, rng.gauss(0, 5e-4))
        w.stats()
    assert w.rebuilds == (21_000 - 1_000) // 1_000
