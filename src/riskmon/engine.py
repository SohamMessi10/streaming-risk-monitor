"""The event-time engine: reorders each symbol's events, drops late and duplicate ones, and turns
the rest into returns and rolling statistics.

For each symbol:

    watermark = (latest event time seen) - lateness_ms

Accepted events wait in a buffer and are released in (time, seq) order once their time is
strictly before the watermark. An event is **late** if something after it in (time, seq) order
has already been released: it can no longer be placed correctly, so it is dropped and counted.
An event delayed by at most `lateness_ms` is never late. Releasing only *strictly* before the
watermark is what makes that true: releasing at the watermark itself could release a same-time
event with a higher seq before one with a lower seq arrived.

A **duplicate** repeats a (symbol, seq) already accepted: dropped and counted. Sequence numbers
are remembered for `window_ms` of event time behind the symbol's latest event; a copy arriving
after that is behind what was released anyway, and is counted as late.

Each released event's log return (from the symbol's previous released price) goes into the
symbol's RollingStats window. Each Update records the window's statistics just *before* the
return was added, which is what an alert compares the return against, and just after.

Memory per symbol is bounded: the buffer holds the events of about the last `lateness_ms`, the
window the returns of the last `window_ms`, and the remembered sequence numbers about the last
`window_ms`.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from riskmon.events import Event
from riskmon.window import DEFAULT_REBUILD_EVERY, RollingStats, Stats


@dataclass(frozen=True)
class Settings:
    window_ms: int = 300_000          # statistics over the last 5 minutes of event time
    lateness_ms: int = 2_000          # how long to wait for out-of-order events
    rebuild_every: int | None = DEFAULT_REBUILD_EVERY

    def check(self) -> None:
        for name in ("window_ms", "lateness_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "window_ms" else 0):
                raise ValueError(f"{name.replace('_', ' ')} must be a whole number of milliseconds"
                                 + (", at least 1" if name == "window_ms" else ", at least 0"))
        if self.rebuild_every is not None and self.rebuild_every < 1:
            raise ValueError("rebuild_every must be at least 1, or None")


@dataclass(frozen=True)
class Update:
    """One released event and what it did to its symbol's statistics."""

    symbol: str
    time: int
    seq: int
    price: float
    log_return: float | None      # None for a symbol's first price
    before: Stats                 # the window just before this return was added
    after: Stats                  # and just after
    released_at: int              # the symbol's latest event time when this was released
                                  # (event-time lag = released_at - time; at least lateness_ms
                                  # until the end of the feed, when everything is flushed)


@dataclass
class Counts:
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    late: int = 0
    released: int = 0


@dataclass
class _Symbol:
    window: RollingStats
    pending: list[tuple[int, int, float]] = field(default_factory=list)   # heap of (time, seq, price)
    max_time: int | None = None
    last_released: tuple[int, int] | None = None                           # (time, seq)
    last_price: float | None = None
    seen: set[int] = field(default_factory=set)                            # seqs remembered for duplicates
    seen_order: list[tuple[int, int]] = field(default_factory=list)        # heap of (time, seq), to forget them
    counts: Counts = field(default_factory=Counts)


class Engine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.settings.check()
        self._symbols: dict[str, _Symbol] = {}
        self.counts = Counts()

    def _state(self, symbol: str) -> _Symbol:
        state = self._symbols.get(symbol)
        if state is None:
            state = _Symbol(window=RollingStats(self.settings.window_ms, rebuild_every=self.settings.rebuild_every))
            self._symbols[symbol] = state
        return state

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def symbol_counts(self, symbol: str) -> Counts:
        return self._symbols[symbol].counts

    def buffered(self) -> int:
        """Events accepted but not yet released, across all symbols."""
        return sum(len(s.pending) for s in self._symbols.values())

    def remembered(self) -> int:
        """Sequence numbers held for duplicate detection, across all symbols."""
        return sum(len(s.seen) for s in self._symbols.values())

    def push(self, event: Event) -> list[Update]:
        """Take one event in arrival order; return the updates it releases (possibly none)."""
        state = self._state(event.symbol)
        for counts in (self.counts, state.counts):
            counts.received += 1
        if event.seq in state.seen:
            self._count(state, "duplicates")
            return []
        if state.last_released is not None and (event.time, event.seq) <= state.last_released:
            self._count(state, "late")
            return []
        self._count(state, "accepted")
        state.seen.add(event.seq)
        heapq.heappush(state.seen_order, (event.time, event.seq))
        heapq.heappush(state.pending, (event.time, event.seq, event.price))
        if state.max_time is None or event.time > state.max_time:
            state.max_time = event.time
        return self._release(event.symbol, state, state.max_time - self.settings.lateness_ms)

    def flush(self) -> list[Update]:
        """The end of the feed: release everything still buffered, symbol by symbol."""
        updates = []
        for symbol, state in self._symbols.items():
            updates += self._release(symbol, state, None)
        return updates

    def _count(self, state: _Symbol, name: str) -> None:
        for counts in (self.counts, state.counts):
            setattr(counts, name, getattr(counts, name) + 1)

    def _release(self, symbol: str, state: _Symbol, watermark: int | None) -> list[Update]:
        updates = []
        while state.pending and (watermark is None or state.pending[0][0] < watermark):
            time, seq, price = heapq.heappop(state.pending)
            state.last_released = (time, seq)
            log_return = math.log(price / state.last_price) if state.last_price is not None else None
            state.last_price = price
            state.window.advance(time)
            before = state.window.stats()
            if log_return is not None:
                state.window.add(time, log_return)
            after = state.window.stats()
            self._count(state, "released")
            updates.append(Update(symbol, time, seq, price, log_return, before, after,
                                  released_at=state.max_time))
        self._forget(state)
        return updates

    def _forget(self, state: _Symbol) -> None:
        """Forget seqs more than `window_ms` of event time behind the symbol's latest event."""
        if state.max_time is None:
            return
        horizon = state.max_time - self.settings.window_ms
        while state.seen_order and state.seen_order[0][0] < horizon:
            state.seen.discard(heapq.heappop(state.seen_order)[1])
