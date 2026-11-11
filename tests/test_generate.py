import json
import math
import statistics

import pytest

from riskmon.events import read_feed_file
from riskmon.generate import FeedSpec, generate, truth_path, write

SMALL = FeedSpec(events_per_symbol=2_000)


def test_the_same_spec_gives_the_same_feed_byte_for_byte(tmp_path):
    write(SMALL, tmp_path / "a.jsonl")
    write(SMALL, tmp_path / "b.jsonl")
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
    assert (tmp_path / "a.truth.json").read_text() == (tmp_path / "b.truth.json").read_text()


def test_another_seed_gives_another_feed():
    assert generate(SMALL)[0] != generate(FeedSpec(events_per_symbol=2_000, seed=2))[0]


def test_adding_a_symbol_leaves_the_others_unchanged():
    one = [e for e in generate(FeedSpec(symbols=("SYNA",), events_per_symbol=500))[0]]
    two = [e for e in generate(FeedSpec(symbols=("SYNA", "SYNB"), events_per_symbol=500))[0] if e.symbol == "SYNA"]
    assert one == two


def test_each_symbol_has_its_own_random_stream():
    events, _ = generate(FeedSpec(events_per_symbol=200, delay_rate=0, late_rate=0, duplicate_rate=0))
    prices = {s: [e.price for e in events if e.symbol == s] for s in ("SYNA", "SYNB")}
    assert prices["SYNA"][0] == prices["SYNB"][0] == 100.0 and prices["SYNA"] != prices["SYNB"]


def test_a_duplicate_arrives_after_its_original_even_when_delayed():
    spec = FeedSpec(symbols=("SYNA",), events_per_symbol=400, duplicate_rate=1.0, delay_rate=1.0, late_rate=0)
    events, truth = generate(spec)
    delay = {d["seq"]: d["delay_ms"] for d in truth.delayed}
    first_seen = list(dict.fromkeys(e.seq for e in events))  # each event's first appearance, in feed order
    time = {e.seq: e.time for e in events}
    # Every event appears first at its own arrival time (time + delay), never earlier through its
    # duplicate: the copies are identical, so only the order can show it.
    assert first_seen == sorted(first_seen, key=lambda seq: (time[seq] + delay[seq], seq))
    assert len(events) == 2 * len(first_seen)


def test_the_truth_file_counts_what_is_in_the_feed():
    events, truth = generate(SMALL)
    assert truth.synthetic and truth.lines == len(events)
    assert truth.events == len({(e.symbol, e.seq) for e in events}) == 2 * 2_000
    assert truth.lines - truth.events == len(truth.duplicates) > 0
    assert len(truth.jumps) > 0 and len(truth.delayed) > 0


def test_event_times_increase_per_symbol_and_arrival_is_time_plus_delay():
    events, truth = generate(SMALL)
    delay = {(d["symbol"], d["seq"]): d["delay_ms"] for d in truth.delayed}
    for symbol in SMALL.symbols:
        own = sorted({e.seq: e for e in events if e.symbol == symbol}.values(), key=lambda e: e.seq)
        assert all(a.time < b.time for a, b in zip(own, own[1:], strict=False))
    arrival = [e.time + delay.get((e.symbol, e.seq), 0) for e in events]
    first_copies = []
    seen = set()
    for event, at in zip(events, arrival, strict=True):
        if (event.symbol, event.seq) not in seen:
            seen.add((event.symbol, event.seq))
            first_copies.append(at)
    assert first_copies == sorted(first_copies)  # first copies arrive in order of time + delay
    assert any(a.time > b.time for a, b in zip(events, events[1:], strict=False))  # so some arrive out of order


def test_returns_have_the_requested_spread_and_jumps_are_where_the_truth_says():
    spec = FeedSpec(symbols=("SYNA",), events_per_symbol=20_000, jump_rate=0.001, gap_rate=0)
    events, truth = generate(spec)
    prices = {e.seq: e.price for e in events}
    returns = {seq: math.log(prices[seq] / prices[seq - 1]) for seq in range(1, len(prices))}
    jumps = {j["seq"]: j["sigmas"] for j in truth.jumps}
    ordinary = [r for seq, r in returns.items() if seq not in jumps]
    assert statistics.stdev(ordinary) == pytest.approx(spec.sigma, rel=0.03)
    for seq, sigmas in jumps.items():  # prices are rounded to 6 places, so allow for that
        assert returns[seq] / spec.sigma == pytest.approx(sigmas, abs=0.05)


def test_gaps_leave_a_hole_of_the_stated_length():
    events, truth = generate(FeedSpec(symbols=("SYNA",), events_per_symbol=3_000, gap_rate=0.01, delay_rate=0,
                                      late_rate=0, duplicate_rate=0))
    assert truth.gaps
    times = {e.seq: e.time for e in events}
    for gap in truth.gaps:
        assert times[gap["after_seq"] + 1] - times[gap["after_seq"]] >= gap["ms"]


def test_late_events_carry_the_late_delay():
    _, truth = generate(FeedSpec(events_per_symbol=5_000, late_rate=0.01))
    late = [d for d in truth.delayed if d["delay_ms"] == 10_000]
    assert len(late) > 20


def test_files_are_written_with_their_truth(tmp_path):
    truth = write(SMALL, tmp_path / "sub" / "feed.jsonl")
    assert len(list(read_feed_file(tmp_path / "sub" / "feed.jsonl"))) == truth.lines
    saved = json.loads(truth_path(tmp_path / "sub" / "feed.jsonl").read_text())
    assert saved["synthetic"] is True and saved["spec"]["seed"] == 1 and saved["lines"] == truth.lines


@pytest.mark.parametrize("change", [
    {"symbols": ()}, {"symbols": ("A", "A")}, {"symbols": ("bad symbol",)}, {"events_per_symbol": 0},
    {"interval_ms": 0}, {"sigma": 0}, {"sigma": 1.5}, {"start_price": -1.0}, {"jump_rate": 1.5},
    {"late_rate": -0.1}, {"jump_sigmas": 0}, {"gap_ms": -5},
])
def test_impossible_specs_are_refused(change):
    with pytest.raises(ValueError):
        generate(FeedSpec(**change))
