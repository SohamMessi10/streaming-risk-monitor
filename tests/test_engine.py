import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from riskmon.engine import Engine, Settings
from riskmon.events import Event
from riskmon.generate import FeedSpec, generate
from riskmon.window import Stats, reference_stats


def run(events, settings=None):
    engine = Engine(settings or Settings())
    updates = []
    for event in events:
        updates += engine.push(event)
    return engine, updates + engine.flush()


def ev(time, seq, price=100.0, symbol="A"):
    return Event(time=time, seq=seq, symbol=symbol, price=price)


def batch(events, settings):
    """The reference: per symbol, sort by (time, seq), returns from consecutive prices, and
    statistics recomputed from scratch over (t - window, t] before and after each return."""
    out = []
    for symbol in dict.fromkeys(e.symbol for e in events):
        own = sorted((e for e in events if e.symbol == symbol), key=lambda e: (e.time, e.seq))
        returns = []  # (time, value)
        previous = None
        for e in own:
            in_window = [r for t, r in returns if t > e.time - settings.window_ms]
            before = reference_stats(in_window)
            value = None if previous is None else math.log(e.price / previous)
            if value is not None:
                returns.append((e.time, value))
                in_window.append(value)
            out.append((symbol, e.time, e.seq, value, before, reference_stats(in_window)))
            previous = e.price
    return out


def stats_close(got: Stats, ref: Stats) -> bool:
    if got.count != ref.count or (got.mean is None) != (ref.mean is None):
        return False
    if got.mean is None:
        return True
    if not math.isclose(got.mean, ref.mean, rel_tol=1e-9, abs_tol=1e-15):
        return False
    return (got.variance is None) == (ref.variance is None) and (
        got.variance is None or math.isclose(got.variance, ref.variance, rel_tol=1e-8, abs_tol=1e-24))


# --- in order --------------------------------------------------------------------------------


def test_the_first_price_has_no_return_and_the_rest_have_log_returns():
    _, updates = run([ev(0, 0, 100.0), ev(1000, 1, 110.0), ev(2000, 2, 99.0)])
    assert [u.log_return for u in updates] == [None, math.log(1.1), math.log(99 / 110)]
    assert updates[0].before == updates[0].after == Stats(0, None, None)
    assert updates[1].before == Stats(0, None, None) and updates[1].after == Stats(1, math.log(1.1), None)
    assert updates[2].before == updates[1].after and updates[2].after.count == 2


def test_events_wait_until_strictly_before_the_watermark():
    engine = Engine(Settings(lateness_ms=1_000))
    assert engine.push(ev(0, 0)) == []
    assert engine.push(ev(1_000, 1)) == []           # watermark 0: time 0 is not strictly before it
    released = engine.push(ev(1_001, 2))             # watermark 1: now it is
    assert [(u.time, u.released_at) for u in released] == [(0, 1_001)]
    assert engine.buffered() == 2
    assert [u.seq for u in engine.flush()] == [1, 2] and engine.flush() == []


def test_statistics_match_a_batch_recomputation():
    feed, _ = generate(FeedSpec(events_per_symbol=600, delay_rate=0, late_rate=0, duplicate_rate=0))
    settings = Settings(window_ms=60_000, lateness_ms=0, rebuild_every=50)
    _, updates = run(feed, settings)
    expected = batch(feed, settings)
    keys = [(u.symbol, u.time, u.seq) for u in updates]
    assert len(keys) == len(set(keys)) == len(expected) and set(keys) == {(s, t, q) for s, t, q, *_ in expected}
    by_key = {(s, t, q): (r, b, a) for s, t, q, r, b, a in expected}
    for u in updates:
        value, before, after = by_key[(u.symbol, u.time, u.seq)]
        assert (u.log_return is None) == (value is None) and (value is None or math.isclose(u.log_return, value))
        assert stats_close(u.before, before) and stats_close(u.after, after), (u, before, after)


# --- out of order -------------------------------------------------------------------------------


@st.composite
def delayed_feeds(draw):
    """Events in time order for two symbols, each delayed by at most `lateness`, in arrival order."""
    lateness = draw(st.integers(0, 3_000))
    events, arrivals = [], []
    for symbol in ("A", "B"):
        time = 0
        steps = draw(st.lists(st.tuples(st.integers(0, 2_000), st.floats(1, 1_000), st.integers(0, lateness)),
                              min_size=1, max_size=40))
        for seq, (gap, price, delay) in enumerate(steps):
            time += gap
            event = Event(time=time, seq=seq, symbol=symbol, price=price)
            events.append(event)
            arrivals.append((time + delay, draw(st.integers(0, 10**6)), event))
    arrivals.sort(key=lambda a: (a[0], a[1]))
    return lateness, events, [e for _, _, e in arrivals]


@given(delayed_feeds(), st.integers(1, 5_000))
def test_any_arrival_order_within_the_lateness_gives_identical_results(feed, window):
    lateness, in_order, arrived = feed
    settings = Settings(window_ms=window, lateness_ms=lateness, rebuild_every=None)
    engine, updates = run(arrived, settings)
    _, reference = run(sorted(in_order, key=lambda e: (e.time, e.seq)), settings)
    assert engine.counts.late == 0
    per_symbol = lambda us: {s: [u for u in us if u.symbol == s] for s in ("A", "B")}  # noqa: E731
    got, want = per_symbol(updates), per_symbol(reference)
    for symbol in ("A", "B"):  # identical, float for float, apart from when each was released
        assert [(u.time, u.seq, u.log_return, u.before, u.after) for u in got[symbol]] == \
               [(u.time, u.seq, u.log_return, u.before, u.after) for u in want[symbol]]


@given(delayed_feeds())
def test_symbols_never_affect_each_other(feed):
    lateness, _, arrived = feed
    settings = Settings(lateness_ms=lateness)
    _, together = run(arrived, settings)
    for symbol in ("A", "B"):
        _, alone = run([e for e in arrived if e.symbol == symbol], settings)
        assert [u for u in together if u.symbol == symbol] == alone


def test_an_event_overtaken_by_a_release_is_late_and_dropped():
    engine = Engine(Settings(lateness_ms=1_000))
    engine.push(ev(10, 5))
    assert [u.seq for u in engine.push(ev(5_000, 7))] == [5]            # watermark 4,000 releases time 10
    assert engine.push(ev(5, 1)) == [] and engine.counts.late == 1       # behind what was released: late
    assert [u.seq for u in engine.push(ev(3_000, 6))] == [6]            # after it and before the watermark:
                                                                         # accepted and released at once
    assert [u.seq for u in engine.flush()] == [7]
    assert (engine.counts.accepted, engine.counts.late, engine.counts.released) == (3, 1, 3)


def test_lateness_compares_time_and_seq_not_time_alone():
    """Seq 3 at time 10 arrives after seq 5 at time 10 was released: putting it out now would
    put it after seq 5, so it is late, though its time equals what was released."""
    engine = Engine(Settings(lateness_ms=1_000))
    engine.push(ev(10, 5))
    engine.push(ev(5_000, 6))                        # releases (10, 5)
    assert engine.push(ev(10, 3)) == [] and engine.counts.late == 1
    assert engine.push(ev(10, 7)) != [] and engine.counts.late == 1  # same time, higher seq: in order


def test_a_same_time_event_with_a_lower_seq_is_never_overtaken_within_the_lateness():
    """Why release is strictly before the watermark: seq 1 at time 1,000 arrives once the
    watermark has reached 1,000 exactly, and must still be in time."""
    engine = Engine(Settings(lateness_ms=1_000))
    engine.push(ev(1_000, 2))
    engine.push(ev(2_000, 3))                        # watermark 1,000
    engine.push(ev(1_000, 1))                        # delayed by exactly the lateness
    updates = engine.flush()
    assert engine.counts.late == 0 and [u.seq for u in updates] == [1, 2, 3]


def test_event_time_lag_is_more_than_the_lateness_until_the_end():
    feed, _ = generate(FeedSpec(events_per_symbol=500))
    engine = Engine(Settings(lateness_ms=2_000))
    for event in feed:
        for update in engine.push(event):
            assert update.released_at - update.time > 2_000


@pytest.mark.parametrize("change", [{"window_ms": 0}, {"window_ms": 1.5}, {"lateness_ms": -1},
                                    {"lateness_ms": True}, {"rebuild_every": 0}])
def test_bad_settings_are_refused(change):
    with pytest.raises(ValueError):
        Engine(Settings(**change))
