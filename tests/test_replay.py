import json
import math
import random

import pytest

from riskmon.alerts import AlertRule
from riskmon.engine import Settings
from riskmon.events import Event
from riskmon.generate import FeedSpec, generate
from riskmon.replay import _lag_summary, replay

QUIET = Settings(window_ms=10**9, lateness_ms=2_000)
RULE = AlertRule(threshold=5.0, min_count=30, cooldown_ms=30_000)


def walk(n=200, symbol="A", seed=3, jumps=(), start_time=0, sigma=0.001):
    """Events 1 s apart: a random walk with a jump of 10 sigma at each seq in `jumps`."""
    rng = random.Random(seed)
    price, out = 100.0, []
    for seq in range(n):
        if seq:
            price *= math.exp(10 * sigma if seq in jumps else rng.gauss(0, sigma))
        out.append(Event(time=start_time + seq * 1_000, seq=seq, symbol=symbol, price=price))
    return out


def truth(*seqs, symbol="A"):
    return [{"symbol": symbol, "seq": s, "time": 0, "sigmas": 10.0} for s in seqs]


def evaluation(result):
    return result.report(True)["evaluation"]


def outcome(result, seq):
    return next(o for o in evaluation(result)["outcomes"] if o["seq"] == seq)


def test_a_jump_is_detected_by_the_return_at_its_seq():
    result = replay(walk(jumps={100}), QUIET, RULE, truth(100))
    assert [a.seq for a in result.alerts] == [100]
    assert outcome(result, 100) == {"symbol": "A", "seq": 100, "time": 0, "sigmas": 10.0, "result": "detected",
                                    "covered_by_seq": 100, "z": pytest.approx(result.alerts[0].z, abs=1e-3)}
    ev = evaluation(result)
    assert (ev["detected"], ev["false_alerts"], ev["recall"], ev["precision"]) == (1, 0, 1.0, 1.0)


def test_a_jump_whose_event_was_late_is_covered_by_the_next_return():
    events = walk(jumps={100})
    jump = events.pop(100)
    events.insert(110, jump)    # arrives 10 events (10 s) late: behind the 2 s lateness, dropped
    result = replay(events, QUIET, RULE, truth(100))
    assert result.engine.counts.late == 1
    assert [a.seq for a in result.alerts] == [101]
    assert outcome(result, 100)["result"] == "detected"
    assert outcome(result, 100)["covered_by_seq"] == 101
    assert evaluation(result)["false_alerts"] == 0


def test_a_jump_never_released_is_missed_as_not_released():
    events = walk(n=120)
    late = Event(time=50_000, seq=500, symbol="A", price=200.0)  # behind what was released
    result = replay(events + [late], QUIET, RULE, truth(500))
    assert outcome(result, 500) == {"symbol": "A", "seq": 500, "time": 0, "sigmas": 10.0, "result": "missed",
                                    "reason": "not released"}


def test_missed_jumps_say_why():
    early = replay(walk(jumps={10}), QUIET, RULE, truth(10))
    assert outcome(early, 10)["reason"] == "too few returns"

    small = replay(walk(jumps={100}, seed=4), QUIET, AlertRule(threshold=50.0), truth(100))
    assert outcome(small, 100)["reason"] == "below threshold"
    assert abs(outcome(small, 100)["z"]) > 5

    flat = [Event(time=t * 1_000, seq=t, symbol="A", price=100.0) for t in range(60)]
    assert outcome(replay(flat, QUIET, RULE, truth(40)), 40)["reason"] == "no spread"

    close = replay(walk(jumps={100, 110}), QUIET, RULE, truth(100, 110))
    assert outcome(close, 100)["result"] == "detected"
    assert outcome(close, 110)["reason"] == "cooldown"
    assert evaluation(close)["missed_by_reason"] == {"cooldown": 1}


def test_an_alert_on_no_jump_is_a_false_alert():
    result = replay(walk(jumps={100}), QUIET, RULE, truth())   # the truth lists no jumps
    ev = evaluation(result)
    assert (ev["jumps"], ev["false_alerts"], ev["precision"], ev["recall"]) == (0, 1, 0.0, None)
    assert ev["false_alert_list"][0]["seq"] == 100


def test_jumps_are_scored_per_symbol():
    events = sorted(walk(jumps={100}, symbol="A") + walk(jumps={150}, symbol="B", seed=9),
                    key=lambda e: (e.time, e.symbol))
    result = replay(events, QUIET, RULE, truth(100, symbol="A") + truth(150, symbol="B"))
    ev = evaluation(result)
    assert (ev["detected"], ev["false_alerts"]) == (2, 0)
    assert result.report()["symbols"]["A"]["alerts"] == result.report()["symbols"]["B"]["alerts"] == 1


def test_precision_and_recall_count_hits_and_false_alerts():
    # Three jumps listed: one in the feed and detected, two never released. And an alert on a jump
    # the truth doesn't list: a false alert. Recall 1/3, precision 1/2 (different on purpose).
    result = replay(walk(jumps={100, 150}), QUIET, RULE, truth(100, 998, 999))
    ev = evaluation(result)
    assert (ev["jumps"], ev["detected"], ev["alerts_on_a_jump"], ev["false_alerts"]) == (3, 1, 1, 1)
    assert (ev["recall"], ev["precision"]) == (0.3333, 0.5)


def test_p99_is_the_nearest_rank():
    assert _lag_summary(list(range(1, 101)), 0)["p99"] == 99
    assert _lag_summary(list(range(1_000, 0, -1)), 0)["p99"] == 990
    assert _lag_summary([7], 0)["p99"] == 7
    assert _lag_summary([1, 2], 0)["p99"] == 2


def test_without_a_truth_file_there_is_no_evaluation():
    assert replay(walk(jumps={100}), QUIET, RULE).report()["evaluation"] is None


def test_alerts_do_not_depend_on_arrival_order_within_the_lateness():
    spec = FeedSpec(events_per_symbol=1_500, late_rate=0.0, duplicate_rate=0.0, max_delay_ms=1_500,
                    delay_rate=0.3, jump_rate=0.005, seed=11)
    events, injected = generate(spec)
    shuffled = replay(events, Settings(), RULE, injected.jumps)
    ordered = replay(sorted(events, key=lambda e: (e.symbol, e.time, e.seq)), Settings(), RULE, injected.jumps)
    assert shuffled.engine.counts.late == 0
    def key(alert):
        return alert.symbol, alert.seq

    assert [a.to_dict() for a in sorted(shuffled.alerts, key=key)] == [a.to_dict() for a in sorted(ordered.alerts, key=key)]
    assert shuffled.alerts


def test_the_default_feed_as_measured():
    """A regression pin on the numbers in PROGRESS.md (synthetic feed, default settings)."""
    events, injected = generate(FeedSpec())
    result = replay(events, jumps=injected.jumps)
    report = result.report(True)
    ev = report["evaluation"]
    assert (ev["jumps"], ev["detected"], ev["false_alerts"], ev["missed_by_reason"]) == (20, 19, 0, {"cooldown": 1})
    assert report["alerts"]["fired"] == 19 and report["alerts"]["suppressed_by_cooldown"] == 1
    assert report["counts"] == {"received": 7_276, "accepted": 7_191, "duplicates": 76, "late": 9, "released": 7_191}
    lag = report["lag_ms"]
    assert lag["updates"] + lag["flushed_at_end"] == 7_191
    assert (lag["min"], lag["median"], lag["p99"], lag["max"]) == (2_001, 2_716, 6_939, 64_542)


def test_every_jump_is_accounted_for_and_the_report_is_json():
    events, injected = generate(FeedSpec(seed=5, jump_rate=0.01))
    report = replay(events, jumps=injected.jumps).report(True)
    ev = report["evaluation"]
    assert len(ev["outcomes"]) == ev["jumps"] == len(injected.jumps)
    assert ev["detected"] + sum(ev["missed_by_reason"].values()) == ev["jumps"]
    assert ev["alerts_on_a_jump"] + ev["false_alerts"] == report["alerts"]["fired"]
    assert json.loads(json.dumps(report)) == report
    assert report["timing"]["events_per_second"] > 0


def test_lag_leaves_out_the_end_of_feed_flush():
    result = replay(walk(n=50), QUIET, RULE)
    lag = result.report()["lag_ms"]
    # Events 1 s apart, 2 s lateness, release strictly before the watermark: each event goes out
    # when the one 3 s later arrives, and the last 3 wait for the end of the feed.
    assert lag["flushed_at_end"] == 3
    assert lag["updates"] == 47 and lag["min"] == lag["max"] == 3_000


def test_an_empty_feed_gives_an_empty_report():
    report = replay([], QUIET, RULE, []).report(True)
    assert report["counts"]["received"] == 0 and report["alerts"]["fired"] == 0
    assert report["lag_ms"] == {"updates": 0, "flushed_at_end": 0}
    assert report["evaluation"]["recall"] is None and report["evaluation"]["precision"] is None
