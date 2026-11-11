"""Replay a feed through the event-time engine and the alert rule, and report what happened.

With a truth file (synthetic feeds have one), alerts are scored against the injected jumps:

- A jump is **covered** by the released return that spans it: the return of symbol S from its
  previous released seq p to seq s covers a jump at seq j when p < j <= s. Usually s = j; if the
  jump's own event was dropped as late, the next released return carries the move instead.
- A covered jump is **detected** if that return fired an alert. Otherwise it is **missed**, with
  the reason: the window held too few returns, the cooldown suppressed it, its |z| was below the
  threshold, or the window had no spread. A jump no released return spans (its event and all
  later ones of the symbol were dropped) is missed as "not released".
- An alert that covers no jump is a **false alert**.

Recall is detected / jumps and precision is (alerts that cover a jump) / alerts. On synthetic
data with large injected jumps both can be high; that says the rule finds what was injected,
not how it would do on real prices, and the report says so.
"""

from __future__ import annotations

import bisect
import statistics
import time as clock
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from riskmon.alerts import Alert, Alerter, AlertRule, z_score
from riskmon.engine import Engine, Settings, Update
from riskmon.events import Event


@dataclass
class _Scoring:
    """Per symbol: the injected jump seqs, sorted, and what became of each."""

    seqs: list[int]
    sigmas: dict[int, float]
    outcome: dict[int, dict] = field(default_factory=dict)


@dataclass
class Result:
    settings: Settings
    rule: AlertRule
    engine: Engine
    alerter: Alerter
    alerts: list[Alert]
    lags: list[int]                  # event-time lag of each update released before the end-of-feed flush
    flushed: int                     # updates released by the flush
    seconds: float
    jumps: dict[str, _Scoring] | None
    false_alerts: list[Alert]

    def report(self, synthetic: bool | None = None) -> dict:
        counts = asdict(self.engine.counts)
        per_symbol = {}
        for symbol in self.engine.symbols():
            entry = asdict(self.engine.symbol_counts(symbol))
            entry["alerts"] = sum(1 for a in self.alerts if a.symbol == symbol)
            per_symbol[symbol] = entry
        report = {
            "synthetic": synthetic,
            "settings": {**asdict(self.settings), **asdict(self.rule)},
            "counts": counts,
            "symbols": per_symbol,
            "alerts": {"fired": self.alerter.fired, "suppressed_by_cooldown": self.alerter.suppressed,
                       "list": [a.to_dict() for a in self.alerts]},
            "lag_ms": _lag_summary(self.lags, self.flushed),
            "evaluation": self._evaluation(),
            "timing": {"seconds": round(self.seconds, 4),
                       "events_per_second": round(counts["received"] / self.seconds) if self.seconds > 0 else None,
                       "note": "wall-clock on the machine that ran it, reading and parsing included"},
        }
        return report

    def _evaluation(self) -> dict | None:
        if self.jumps is None:
            return None
        outcomes = []
        for symbol, scoring in self.jumps.items():
            for seq in scoring.seqs:
                entry = scoring.outcome.get(seq, {"result": "missed", "reason": "not released"})
                outcomes.append({"symbol": symbol, "seq": seq, "sigmas": scoring.sigmas[seq], **entry})
        jumps = len(outcomes)
        detected = sum(1 for o in outcomes if o["result"] == "detected")
        hits = len(self.alerts) - len(self.false_alerts)
        reasons: dict[str, int] = {}
        for o in outcomes:
            if o["result"] == "missed":
                reasons[o["reason"]] = reasons.get(o["reason"], 0) + 1
        return {
            "jumps": jumps,
            "detected": detected,
            "missed": jumps - detected,
            "missed_by_reason": dict(sorted(reasons.items())),
            "alerts_on_a_jump": hits,
            "false_alerts": len(self.false_alerts),
            "recall": round(detected / jumps, 4) if jumps else None,
            "precision": round(hits / len(self.alerts), 4) if self.alerts else None,
            "outcomes": outcomes,
            "false_alert_list": [a.to_dict() for a in self.false_alerts],
            "note": "scored against injected synthetic jumps: it shows the rule finds what was injected, "
                    "not how it would do on real prices",
        }


def _lag_summary(lags: list[int], flushed: int) -> dict:
    if not lags:
        return {"updates": 0, "flushed_at_end": flushed}
    ordered = sorted(lags)
    n = len(ordered)
    p99 = ordered[(99 * n + 99) // 100 - 1]   # nearest rank: the ceil(0.99 n)-th smallest
    return {"updates": n, "flushed_at_end": flushed, "min": ordered[0], "median": statistics.median(ordered),
            "p99": p99, "max": ordered[-1]}


def replay(events: Iterable[Event], settings: Settings | None = None, rule: AlertRule | None = None,
           jumps: list[dict] | None = None) -> Result:
    """Run the feed (in arrival order) through the engine and the alert rule."""
    settings = settings or Settings()
    rule = rule or AlertRule()
    engine = Engine(settings)
    alerter = Alerter(rule)
    scoring = _scoring(jumps) if jumps is not None else None
    previous_seq: dict[str, int] = {}
    alerts: list[Alert] = []
    false_alerts: list[Alert] = []
    lags: list[int] = []

    def handle(update: Update) -> None:
        suppressed_before = alerter.suppressed
        alert = alerter.observe(update)
        if alert is not None:
            alerts.append(alert)
        covered: list[int] = []
        start = previous_seq.get(update.symbol)
        previous_seq[update.symbol] = update.seq
        if scoring is not None:
            own = scoring.get(update.symbol)
            if own is not None and start is not None:
                lo = bisect.bisect_right(own.seqs, start)
                hi = bisect.bisect_right(own.seqs, update.seq)
                covered = own.seqs[lo:hi]
                for seq in covered:
                    own.outcome[seq] = _outcome(update, alert, alerter.suppressed > suppressed_before, rule)
            if alert is not None and not covered:
                false_alerts.append(alert)

    started = clock.perf_counter()
    for event in events:
        for update in engine.push(event):
            lags.append(update.released_at - update.time)
            handle(update)
    final = engine.flush()
    for update in final:
        handle(update)
    seconds = clock.perf_counter() - started
    return Result(settings, rule, engine, alerter, alerts, lags, len(final), seconds, scoring, false_alerts)


def _scoring(jumps: list[dict]) -> dict[str, _Scoring]:
    out: dict[str, _Scoring] = {}
    for jump in jumps:
        entry = out.setdefault(jump["symbol"], _Scoring([], {}))
        entry.seqs.append(jump["seq"])
        entry.sigmas[jump["seq"]] = jump["sigmas"]
    for entry in out.values():
        entry.seqs.sort()
    return out


def _outcome(update: Update, alert: Alert | None, suppressed: bool, rule: AlertRule) -> dict:
    z = z_score(update)
    entry = {"covered_by_seq": update.seq, "z": None if z is None else round(z, 3)}
    if alert is not None:
        return {"result": "detected", **entry}
    if update.before.count < rule.min_count:
        reason = "too few returns"
    elif z is None:
        reason = "no spread"
    elif suppressed:
        reason = "cooldown"
    else:
        reason = "below threshold"
    return {"result": "missed", "reason": reason, **entry}
