"""A bounded summary of a symbol's prices over time, for charts.

A replay can be millions of events, and a chart only needs a few hundred points, so the dashboard
can't keep every price. `Downsampler` keeps at most `max_points` buckets per symbol, in memory
that doesn't grow with the feed, without knowing the feed's length in advance:

- Bucket k covers event times [origin + k * width, origin + (k + 1) * width), where origin is the
  first time seen. Each bucket keeps its first time, the min, max and last price, the last
  rolling standard deviation, and the number of events.
- When there would be more than `max_points` buckets, the width doubles and neighbouring buckets
  merge in pairs (k // 2). Buckets are aligned to the origin, so merging gives exactly what
  bucketing every point at the new width would have given.

A missing bucket means no event in that interval: a chart breaks its line there instead of
drawing across a gap. Times must not go backwards (the event-time engine releases each symbol in
order).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_POINTS = 600
_START_WIDTH_MS = 1


@dataclass
class Bucket:
    index: int
    time: int                 # the first event time in the bucket
    low: float
    high: float
    last: float
    std: float | None         # the last rolling standard deviation seen in the bucket
    count: int

    def merge(self, later: Bucket) -> None:
        self.low, self.high = min(self.low, later.low), max(self.high, later.high)
        self.last = later.last
        if later.std is not None:
            self.std = later.std
        self.count += later.count

    def to_dict(self) -> dict:
        return {"k": self.index, "t": self.time, "low": self.low, "high": self.high, "last": self.last,
                "std": self.std, "n": self.count}


class Downsampler:
    def __init__(self, max_points: int = DEFAULT_POINTS):
        if isinstance(max_points, bool) or not isinstance(max_points, int) or max_points < 2:
            raise ValueError("max points must be a whole number, at least 2")
        self.max_points = max_points
        self.width = _START_WIDTH_MS
        self.origin: int | None = None
        self.buckets: list[Bucket] = []

    def add(self, time: int, price: float, std: float | None) -> None:
        if self.origin is None:
            self.origin = time
        if self.buckets and time < self.buckets[-1].time:
            raise ValueError(f"time went backwards ({time} after {self.buckets[-1].time})")
        index = (time - self.origin) // self.width
        last = self.buckets[-1] if self.buckets else None
        if last is not None and last.index == index:   # the common case: update in place
            if price < last.low:
                last.low = price
            elif price > last.high:
                last.high = price
            last.last = price
            if std is not None:
                last.std = std
            last.count += 1
            return
        self.buckets.append(Bucket(index, time, price, price, price, std, 1))
        while len(self.buckets) > self.max_points:
            self._coarsen()

    def _coarsen(self) -> None:
        self.width *= 2
        merged: list[Bucket] = []
        for bucket in self.buckets:
            bucket.index //= 2
            if merged and merged[-1].index == bucket.index:
                merged[-1].merge(bucket)
            else:
                merged.append(bucket)
        self.buckets = merged

    def to_dict(self) -> dict:
        """Bucket k of the points covers [origin + k * width_ms, origin + (k + 1) * width_ms)."""
        return {"origin": self.origin, "width_ms": self.width, "points": [b.to_dict() for b in self.buckets]}
