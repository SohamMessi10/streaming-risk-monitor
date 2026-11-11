"""Alerts: a return far from its window's recent behaviour.

For each released return, against the window's statistics *before* it was added:

    z = (return - mean) / std

An alert fires when |z| >= `threshold`, the window held at least `min_count` returns, its
standard deviation is above zero, and the symbol's previous alert (one that fired, not one that
was suppressed) is at least `cooldown_ms` of event time earlier. A return that passes every test
except the cooldown is counted as suppressed, so a storm of alerts is visible without flooding
the output.

Why the window before the return: a large return added first would raise the window's standard
deviation and partly hide itself.

An alert is an observation to investigate, not a confirmed fault: a real price can move this
much, and on synthetic data the rule can be scored against what was injected (see replay.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from riskmon.engine import Update


@dataclass(frozen=True)
class AlertRule:
    threshold: float = 5.0        # |z| at or above this fires
    min_count: int = 30           # returns the window must hold first
    cooldown_ms: int = 30_000     # event time after a fired alert during which the symbol stays quiet

    def check(self) -> None:
        if not (math.isfinite(self.threshold) and self.threshold > 0):
            raise ValueError("the threshold must be a positive number")
        if isinstance(self.min_count, bool) or not isinstance(self.min_count, int) or self.min_count < 2:
            raise ValueError("min count must be a whole number, at least 2 (a standard deviation needs two)")
        if isinstance(self.cooldown_ms, bool) or not isinstance(self.cooldown_ms, int) or self.cooldown_ms < 0:
            raise ValueError("cooldown must be a whole number of milliseconds, at least 0")


@dataclass(frozen=True)
class Alert:
    """What fired, and the numbers behind it."""

    symbol: str
    time: int
    seq: int
    price: float
    log_return: float
    mean: float           # of the window before this return
    std: float
    z: float
    count: int            # returns in that window

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "time": self.time, "seq": self.seq, "price": self.price,
                "log_return": self.log_return, "mean": self.mean, "std": self.std, "z": self.z,
                "count": self.count}


def z_score(update: Update) -> float | None:
    """The return's z-score against the window before it, or None when there is none to compare."""
    before = update.before
    if update.log_return is None or before.variance is None or before.variance <= 0.0:
        return None
    return (update.log_return - before.mean) / before.std


@dataclass
class Alerter:
    rule: AlertRule = field(default_factory=AlertRule)
    fired: int = 0
    suppressed: int = 0                                    # would have fired, but in a cooldown
    _last: dict[str, int] = field(default_factory=dict)    # symbol -> time of its last fired alert

    def __post_init__(self) -> None:
        self.rule.check()

    def observe(self, update: Update) -> Alert | None:
        """The alert this update fires, if any. Updates must come in each symbol's event-time order."""
        if update.before.count < self.rule.min_count:
            return None
        z = z_score(update)
        if z is None or abs(z) < self.rule.threshold:
            return None
        last = self._last.get(update.symbol)
        if last is not None and update.time - last < self.rule.cooldown_ms:
            self.suppressed += 1
            return None
        self._last[update.symbol] = update.time
        self.fired += 1
        return Alert(update.symbol, update.time, update.seq, update.price, update.log_return,
                     update.before.mean, update.before.std, z, update.before.count)
