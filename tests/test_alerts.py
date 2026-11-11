import math

import pytest

from riskmon.alerts import Alerter, AlertRule, z_score
from riskmon.engine import Update
from riskmon.window import Stats

RULE = AlertRule(threshold=5.0, min_count=30, cooldown_ms=30_000)


def upd(log_return, *, mean=0.0, std=1.0, count=100, time=1_000_000, seq=1, symbol="A"):
    """An update whose window, before the return, had this mean, standard deviation and count."""
    before = Stats(count, mean, None if std is None else std * std)
    return Update(symbol, time, seq, 100.0, log_return, before, before, released_at=time + 2_000)


def test_z_compares_the_return_with_the_window_before_it():
    assert z_score(upd(0.7, mean=0.1, std=0.2)) == pytest.approx(3.0)
    assert z_score(upd(-0.5, mean=0.1, std=0.2)) == pytest.approx(-3.0)


def test_there_is_no_z_without_a_return_or_a_spread():
    assert z_score(upd(None)) is None             # a symbol's first price
    assert z_score(upd(0.1, std=None)) is None    # fewer than two returns
    assert z_score(upd(0.1, std=0.0)) is None     # constant returns: nothing to compare against


def test_an_alert_fires_at_the_threshold_and_not_below():
    assert Alerter(RULE).observe(upd(5.0)) is not None
    assert Alerter(RULE).observe(upd(-5.0)) is not None
    assert Alerter(RULE).observe(upd(math.nextafter(5.0, 0))) is None


def test_an_alert_records_the_numbers_behind_it():
    alert = Alerter(RULE).observe(upd(1.3, mean=0.1, std=0.2, count=250, time=42, seq=7, symbol="B"))
    assert alert.to_dict() == {"symbol": "B", "time": 42, "seq": 7, "price": 100.0, "log_return": 1.3,
                               "mean": 0.1, "std": 0.2, "z": pytest.approx(6.0), "count": 250}


def test_the_window_must_hold_min_count_returns_first():
    assert Alerter(RULE).observe(upd(9.0, count=29)) is None
    assert Alerter(RULE).observe(upd(9.0, count=30)) is not None


def test_the_alert_uses_the_window_before_the_return_not_after():
    # The return itself would widen the window: judged against "after" it would look smaller.
    before = Stats(30, 0.0, 1.0)
    after = Stats(31, 6.0 / 31, 37.0 / 30 * 0.96)
    update = Update("A", 1, 1, 100.0, 6.0, before, after, released_at=2_001)
    assert Alerter(RULE).observe(update).z == 6.0


def test_the_cooldown_suppresses_and_counts_alerts_too_soon_after_one():
    alerter = Alerter(RULE)
    assert alerter.observe(upd(9.0, time=0)) is not None
    assert alerter.observe(upd(9.0, time=29_999)) is None
    assert alerter.observe(upd(9.0, time=30_000)) is not None
    assert (alerter.fired, alerter.suppressed) == (2, 1)


def test_a_suppressed_alert_does_not_extend_the_cooldown():
    alerter = Alerter(RULE)
    alerter.observe(upd(9.0, time=0))
    alerter.observe(upd(9.0, time=20_000))       # suppressed
    assert alerter.observe(upd(9.0, time=30_000)) is not None


def test_small_returns_in_a_cooldown_are_not_counted_as_suppressed():
    alerter = Alerter(RULE)
    alerter.observe(upd(9.0, time=0))
    alerter.observe(upd(1.0, time=1_000))
    assert alerter.suppressed == 0


def test_each_symbol_has_its_own_cooldown():
    alerter = Alerter(RULE)
    assert alerter.observe(upd(9.0, time=0, symbol="A")) is not None
    assert alerter.observe(upd(9.0, time=1, symbol="B")) is not None


def test_a_zero_cooldown_allows_every_alert():
    alerter = Alerter(AlertRule(cooldown_ms=0))
    assert all(alerter.observe(upd(9.0, time=0, seq=s)) is not None for s in range(3))


@pytest.mark.parametrize("change", [
    {"threshold": 0}, {"threshold": -1.0}, {"threshold": math.inf}, {"threshold": math.nan},
    {"min_count": 1}, {"min_count": 2.5}, {"min_count": True},
    {"cooldown_ms": -1}, {"cooldown_ms": 1.5}, {"cooldown_ms": False},
])
def test_bad_rules_are_refused(change):
    with pytest.raises(ValueError):
        Alerter(AlertRule(**change))
