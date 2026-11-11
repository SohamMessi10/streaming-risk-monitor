import math

from riskmon.window import Stats, reference_stats

# --- the reference ------------------------------------------------------------------------------


def test_the_reference_matches_values_worked_out_by_hand():
    assert reference_stats([]) == Stats(0, None, None)
    assert reference_stats([3.0]) == Stats(1, 3.0, None)
    assert reference_stats([1.0, 2.0, 3.0, 4.0]) == Stats(4, 2.5, 5 / 3)  # sum of squares 5, n - 1 = 3
    assert reference_stats([2.0, 4.0]).std == math.sqrt(2.0)


def test_the_reference_gives_equal_values_exactly_zero_variance():
    """fsum(values) / n alone gives 100.09999999999998 and a variance of 3e-28 here."""
    assert reference_stats([100.1, 100.1, 100.1]) == Stats(3, 100.1, 0.0)
