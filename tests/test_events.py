import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from riskmon.events import Event, FeedError, parse_event, read_feed, read_feed_file, write_feed

GOOD = {"symbol": "SYNA", "time": 1_700_000_000_123, "seq": 17, "price": 101.25}


def line(**changes):
    data = GOOD | changes
    return json.dumps({k: v for k, v in data.items() if v is not ...})


def test_a_good_line_becomes_an_event():
    assert list(read_feed([line()])) == [Event(time=1_700_000_000_123, seq=17, symbol="SYNA", price=101.25)]


def test_an_integer_price_is_accepted_as_a_float():
    event = next(read_feed([line(price=100)]))
    assert event.price == 100.0 and isinstance(event.price, float)


def test_events_sort_by_time_then_sequence():
    a = Event(time=5, seq=2, symbol="X", price=1.0)
    b = Event(time=5, seq=1, symbol="Y", price=1.0)
    c = Event(time=4, seq=9, symbol="Z", price=1.0)
    assert sorted([a, b, c]) == [c, b, a]


@pytest.mark.parametrize(("text", "message"), [
    (line(colour="red"), "unknown field 'colour'"),
    (line(price=...), "missing field 'price'"),
    (line(symbol=""), "symbol must be"),
    (line(symbol="has space"), "symbol must be"),
    (line(symbol="X" * 33), "symbol must be"),
    (line(symbol=7), "symbol must be"),
    (line(time=1.5), "time must be a whole number"),
    (line(time=True), "time must be a whole number"),
    (line(time=-1), "time must be at least 0"),
    (line(seq="3"), "seq must be a whole number"),
    (line(price=0), "price must be a finite number above 0"),
    (line(price=-2.5), "price must be a finite number above 0"),
    (line(price="1"), "price must be a number"),
    (line(price=False), "price must be a number"),
    ('{"symbol": "X", "time": 1, "seq": 0, "price": NaN}', "finite number above 0"),
    ('{"symbol": "X", "time": 1, "seq": 0, "price": Infinity}', "finite number above 0"),
    ("[1, 2, 3]", "must be a JSON object"),
    ("{not json", "not valid JSON"),
])
def test_bad_lines_are_refused_with_the_reason(text, message):
    with pytest.raises(FeedError, match=message):
        list(read_feed([text]))


def test_errors_name_the_line():
    with pytest.raises(FeedError, match=r"^feed, line 3: unknown field"):
        list(read_feed([line(), "", line(extra=1)]))


def test_blank_lines_are_skipped_and_reading_is_lazy():
    events = read_feed([line(seq=1), "   ", line(seq=2), "{bad"])
    assert [next(events).seq, next(events).seq] == [1, 2]  # the bad line isn't reached yet
    with pytest.raises(FeedError):
        next(events)


def test_a_huge_line_is_refused():
    with pytest.raises(FeedError, match="longer than 10,000 bytes"):
        list(read_feed([" " * 10_001]))


def test_files_are_read_and_their_problems_reported(tmp_path):
    path = tmp_path / "feed.jsonl"
    assert write_feed([Event(1, 0, "A", 2.0), Event(2, 1, "A", 3.0)], path) == 2
    assert [e.price for e in read_feed_file(path)] == [2.0, 3.0]
    (tmp_path / "latin.jsonl").write_bytes(b'{"symbol": "\xe9"}\n')
    with pytest.raises(FeedError, match="isn't UTF-8"):
        list(read_feed_file(tmp_path / "latin.jsonl"))
    with pytest.raises(FeedError, match="can't be read"):
        list(read_feed_file(tmp_path / "missing.jsonl"))


def test_parse_event_needs_an_object():
    with pytest.raises(ValueError, match="JSON object"):
        parse_event("SYNA")


events = st.builds(
    Event, time=st.integers(0, 2**62), seq=st.integers(0, 2**62),
    symbol=st.from_regex(r"[A-Za-z0-9._-]{1,32}", fullmatch=True),
    price=st.floats(min_value=1e-300, max_value=1e300, allow_nan=False, allow_infinity=False))


@given(st.lists(events, max_size=20))
def test_any_event_survives_a_round_trip(feed):
    assert list(read_feed(e.to_json() for e in feed)) == feed
