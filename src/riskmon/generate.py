"""A synthetic price feed with known problems injected, and a truth file listing them.

Prices follow a geometric random walk: each step multiplies the price by exp(sigma * Z) for a
standard normal Z, so log returns are normal with standard deviation `sigma`. Then:

    jumps       a return of `jump_sigmas` standard deviations, either sign: what alerts should find
    duplicates  an event sent a second time, shortly after the first
    delays      an event that arrives up to `max_delay_ms` after it happened: out of order
    late        an event that arrives `late_delay_ms` after it happened: later than a replay
                with a smaller allowed lateness will wait for
    gaps        a stretch with no events at all

Arrival order is event time plus delay. Everything is seeded: the same parameters give the same
feed byte for byte, and each symbol has its own random stream, so adding a symbol doesn't change
the others. The output is synthetic and says so; it is not market data.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from riskmon.events import SYMBOL, Event, write_feed


@dataclass(frozen=True)
class FeedSpec:
    symbols: tuple[str, ...] = ("SYNA", "SYNB")
    seed: int = 1
    start_ms: int = 1_767_225_600_000        # 2026-01-01T00:00:00Z
    events_per_symbol: int = 3_600
    interval_ms: int = 1_000                 # mean spacing between a symbol's events
    start_price: float = 100.0
    sigma: float = 0.0005                    # standard deviation of a log return (5 basis points)
    jump_rate: float = 0.002
    jump_sigmas: float = 8.0
    duplicate_rate: float = 0.01
    delay_rate: float = 0.05
    max_delay_ms: int = 1_500
    late_rate: float = 0.002
    late_delay_ms: int = 10_000
    gap_rate: float = 0.001
    gap_ms: int = 60_000

    def check(self) -> None:
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("give at least one symbol, each once")
        for symbol in self.symbols:
            if not SYMBOL.fullmatch(symbol):
                raise ValueError(f"symbol {symbol!r} must be 1-32 letters, digits, '.', '_' or '-'")
        if self.events_per_symbol < 1 or self.events_per_symbol > 10_000_000:
            raise ValueError("events per symbol must be between 1 and 10,000,000")
        if self.interval_ms < 1 or self.start_ms < 0:
            raise ValueError("the interval must be at least 1 ms and the start at least 0")
        if not (self.start_price > 0 and math.isfinite(self.start_price)) or not (0 < self.sigma < 1):
            raise ValueError("the start price must be positive and sigma between 0 and 1")
        for name in ("jump_rate", "duplicate_rate", "delay_rate", "late_rate", "gap_rate"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name.replace('_', ' ')} must be between 0 and 1")
        if self.jump_sigmas <= 0 or min(self.max_delay_ms, self.late_delay_ms, self.gap_ms) < 0:
            raise ValueError("jump size must be positive and delays and gaps at least 0")


@dataclass
class Truth:
    """What was injected, so results can be checked against it."""

    spec: dict
    synthetic: bool = True
    events: int = 0          # distinct events (each duplicate counted once)
    lines: int = 0           # lines in the feed, duplicates included
    jumps: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    delayed: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)


def generate(spec: FeedSpec) -> tuple[list[Event], Truth]:
    """The feed in arrival order, and what was injected into it."""
    spec.check()
    truth = Truth(spec=asdict(spec))
    arrivals: list[tuple[int, int, Event]] = []   # (arrival time, order made, event)
    made = 0
    for symbol in spec.symbols:
        rng = random.Random(f"{spec.seed}:{symbol}")
        time, price = spec.start_ms, spec.start_price
        for seq in range(spec.events_per_symbol):
            if seq and rng.random() < spec.gap_rate:
                truth.gaps.append({"symbol": symbol, "after_seq": seq - 1, "from": time, "ms": spec.gap_ms})
                time += spec.gap_ms
            if seq:
                time += max(1, round(rng.expovariate(1 / spec.interval_ms)))
                step = rng.gauss(0.0, spec.sigma)
                if rng.random() < spec.jump_rate:
                    sign = 1 if rng.random() < 0.5 else -1
                    step = sign * spec.jump_sigmas * spec.sigma
                    truth.jumps.append({"symbol": symbol, "seq": seq, "time": time, "sigmas": sign * spec.jump_sigmas})
                price *= math.exp(step)
            event = Event(time=time, seq=seq, symbol=symbol, price=round(price, 6))
            delay = 0
            if rng.random() < spec.late_rate:
                delay = spec.late_delay_ms
            elif rng.random() < spec.delay_rate:
                delay = rng.randint(1, max(1, spec.max_delay_ms))
            if delay:
                truth.delayed.append({"symbol": symbol, "seq": seq, "time": time, "delay_ms": delay})
            arrivals.append((time + delay, made, event))
            made += 1
            if rng.random() < spec.duplicate_rate:
                again = time + delay + rng.randint(0, max(1, spec.max_delay_ms))
                truth.duplicates.append({"symbol": symbol, "seq": seq, "time": time})
                arrivals.append((again, made, event))
                made += 1
            truth.events += 1
    arrivals.sort(key=lambda a: (a[0], a[1]))
    truth.lines = len(arrivals)
    return [event for _, _, event in arrivals], truth


def write(spec: FeedSpec, feed_path: Path) -> Truth:
    """Write the feed and, next to it, `<name>.truth.json`."""
    events, truth = generate(spec)
    feed_path = Path(feed_path)
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    write_feed(events, feed_path)
    truth_path(feed_path).write_text(json.dumps(asdict(truth), indent=1) + "\n", encoding="utf-8")
    return truth


def truth_path(feed_path: Path) -> Path:
    return Path(feed_path).with_name(Path(feed_path).stem + ".truth.json")
