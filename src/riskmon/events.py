"""Price events and the feed format.

A feed is JSON Lines, one event per line:

    {"symbol": "SYNA", "time": 1700000000123, "seq": 17, "price": 101.25}

    symbol  1-32 characters: letters, digits, ".", "_", "-"
    time    event time: integer milliseconds since the Unix epoch (when the price was observed)
    seq     the symbol's sequence number, an integer >= 0; a repeated (symbol, seq) is a duplicate
    price   a finite number > 0

Integer milliseconds keep ordering and window edges exact: no float rounding decides whether an
event is inside a window. Parsing is strict: an unknown field, a missing one, a wrong type or a
bad value stops the read with the line number, because a feed with typos produces quietly wrong
statistics.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SYMBOL = re.compile(r"[A-Za-z0-9._-]{1,32}")
FIELDS = ("symbol", "time", "seq", "price")
MAX_LINE_BYTES = 10_000


class FeedError(ValueError):
    """The feed can't be read: the message says where and why."""


@dataclass(frozen=True, order=True)
class Event:
    time: int      # event time, ms since the epoch; first, so events sort by time, then seq
    seq: int
    symbol: str
    price: float

    def to_json(self) -> str:
        return json.dumps({"symbol": self.symbol, "time": self.time, "seq": self.seq, "price": self.price})


def _integer(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number, not {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be at least 0, not {value}")
    return value


def parse_event(data: object) -> Event:
    """An Event from one decoded JSON value, or ValueError saying what is wrong with it."""
    if not isinstance(data, dict):
        raise ValueError("each line must be a JSON object")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown field {unknown[0]!r} (the fields are {', '.join(FIELDS)})")
    missing = [name for name in FIELDS if name not in data]
    if missing:
        raise ValueError(f"missing field {missing[0]!r}")
    symbol = data["symbol"]
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError(f"symbol must be 1-32 letters, digits, '.', '_' or '-', not {symbol!r}")
    price = data["price"]
    if isinstance(price, bool) or not isinstance(price, int | float):
        raise ValueError(f"price must be a number, not {price!r}")
    price = float(price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"price must be a finite number above 0, not {data['price']!r}")
    return Event(time=_integer(data["time"], "time"), seq=_integer(data["seq"], "seq"), symbol=symbol, price=price)


def read_feed(lines: Iterable[str], source: str = "feed") -> Iterator[Event]:
    """Events from JSON Lines, in arrival order. Blank lines are skipped."""
    for number, line in enumerate(lines, start=1):
        if len(line.encode("utf-8", "surrogatepass")) > MAX_LINE_BYTES:
            raise FeedError(f"{source}, line {number}: longer than {MAX_LINE_BYTES:,} bytes")
        if not line.strip():
            continue
        try:
            data = json.loads(line, parse_constant=lambda name: float(name))
        except ValueError as exc:
            raise FeedError(f"{source}, line {number}: not valid JSON ({exc})") from None
        try:
            yield parse_event(data)
        except ValueError as exc:
            raise FeedError(f"{source}, line {number}: {exc}") from None


def read_feed_file(path: Path) -> Iterator[Event]:
    """Events from a feed file, read lazily: a large feed is never held in memory."""
    try:
        with open(path, encoding="utf-8") as handle:
            yield from read_feed(handle, str(path))
    except UnicodeDecodeError:
        raise FeedError(f"{path} isn't UTF-8 text") from None
    except OSError as exc:
        raise FeedError(f"{path} can't be read ({exc.strerror})") from None


def write_feed(events: Iterable[Event], path: Path) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(event.to_json() + "\n")
            count += 1
    return count
