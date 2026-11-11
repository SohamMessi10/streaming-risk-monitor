"""A static HTML dashboard of a replay report: one self-contained file, inline SVG, no scripts.

`render(report)` returns the page. It needs nothing but the report: no network, no JavaScript
(a Content-Security-Policy forbids both), so it opens anywhere and can be attached or archived.

The page has, in order:

- the headline numbers;
- per symbol, a price chart and, under it, the rolling standard deviation. They are two charts,
  not one with two y-axes. Alerts are marked on the price (a triangle pointing the way the price
  moved) and missed jumps along the top, each with a hover title giving the numbers;
- tables of the alerts, the missed jumps and the per-symbol counts, so no value is shown only by
  colour or position.

Every piece of text that came from the feed or the command line is escaped. The feed path and
the symbols are shown, and a symbol is validated but a path isn't.
"""

from __future__ import annotations

import html
import math
from datetime import UTC, datetime

from riskmon import __version__

WIDE, NARROW = 880, 400            # chart widths (SVG units): desktop, and phones under 600 px
PRICE_HEIGHT, STD_HEIGHT = 260, 150
LEFT, RIGHT, TOP, BOTTOM = 64, 16, 22, 26
MAX_TABLE_ROWS = 200

_TIME_STEPS_MS = [1_000 * s for s in (1, 2, 5, 10, 15, 30)] + [60_000 * m for m in (1, 2, 5, 10, 15, 30)] + \
    [3_600_000 * h for h in (1, 2, 3, 6, 12, 24, 48, 168)]

_CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series: #2a78d6; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series: #3987e5; --critical: #d03b3b;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series: #3987e5; --critical: #d03b3b;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; overflow-wrap: anywhere; }
h2 { font-size: 18px; margin: 32px 0 8px; }
h3 { font-size: 15px; margin: 16px 0 4px; color: var(--ink-2); font-weight: 600; }
p { margin: 4px 0; }
.sub, .note { color: var(--ink-2); }
.note { font-size: 13px; }
.badge { display: inline-block; font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--ink-2); vertical-align: middle; margin-left: 6px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin: 16px 0; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.tile .label { font-size: 13px; color: var(--ink-2); }
.tile .value { font-size: 22px; font-weight: 600; }
.tile .detail { font-size: 12px; color: var(--muted); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px 12px; }
.scroll { overflow-x: auto; }
svg.chart { display: block; width: 100%; height: auto; }
svg.narrow { display: none; }
@media (max-width: 600px) { svg.wide { display: none; } svg.narrow { display: block; } }
svg text { font-size: 12px; fill: var(--muted); font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 13px; color: var(--ink-2); margin: 4px 0 2px; }
.legend svg { vertical-align: -2px; margin-right: 4px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th.text, td.text { text-align: left; }
th { color: var(--ink-2); font-weight: 600; }
footer { margin-top: 40px; font-size: 13px; color: var(--muted); }
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _utc(ms: int, seconds: bool = True) -> str:
    moment = datetime.fromtimestamp(ms / 1000, UTC)
    return moment.strftime("%H:%M:%S" if seconds else "%H:%M")


def _utc_full(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _num(value, digits: int = 0) -> str:
    if value is None:
        return "–"
    return f"{value:,.{digits}f}"


def nice_ticks(low: float, high: float, target: int = 5) -> list[float]:
    """Round tick values from at or below `low` to at or above `high`, so the axis starts and ends
    on a tick: steps of 1, 2, 2.5 or 5 times a power of ten."""
    span = high - low
    if not (math.isfinite(span) and span > 0):
        return [low]
    raw = span / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first, last = math.floor(low / step + 1e-9), math.ceil(high / step - 1e-9)
    return [round(k * step, 12) for k in range(first, last + 1)]


def time_ticks(start: int, end: int, target: int = 6) -> tuple[list[int], int]:
    """Tick times on round UTC boundaries, and the step between them."""
    span = max(1, end - start)
    step = next((s for s in _TIME_STEPS_MS if s * target >= span), _TIME_STEPS_MS[-1])
    first = -(-start // step) * step
    return list(range(first, end + 1, step)), step


class _Scale:
    def __init__(self, lo: float, hi: float, out_lo: float, out_hi: float):
        if hi <= lo:
            pad = abs(lo) * 0.01 or 1.0
            lo, hi = lo - pad, hi + pad
        self.lo, self.hi, self.out_lo, self.out_hi = lo, hi, out_lo, out_hi

    def __call__(self, value: float) -> float:
        return self.out_lo + (value - self.lo) / (self.hi - self.lo) * (self.out_hi - self.out_lo)


def _runs(points: list[dict], key: str) -> list[list[dict]]:
    """Points split where a bucket is missing (no events there) or the value is None. Skipping a
    point leaves a gap in the bucket indices, so one test (k follows the previous k) covers both."""
    runs: list[list[dict]] = []
    previous = None
    for point in points:
        if point[key] is None:
            continue
        if previous is None or point["k"] != previous["k"] + 1:
            runs.append([])
        runs[-1].append(point)
        previous = point
    return runs


def _path(coords: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)


def _axes(x: _Scale, y: _Scale, width: int, height: int, y_ticks: list[float], y_label, t0: int,
          t1: int) -> list[str]:
    parts = []
    for value in y_ticks:
        py = y(value)
        parts.append(f'<line x1="{LEFT}" x2="{width - RIGHT}" y1="{py:.1f}" y2="{py:.1f}" stroke="var(--grid)" '
                     f'stroke-width="1"/>')
        parts.append(f'<text x="{LEFT - 8}" y="{py + 4:.1f}" text-anchor="end">{_e(y_label(value))}</text>')
    ticks, step = time_ticks(t0, t1, 6 if width >= WIDE else 3)
    base = height - BOTTOM
    parts.append(f'<line x1="{LEFT}" x2="{width - RIGHT}" y1="{base}" y2="{base}" stroke="var(--axis)" '
                 f'stroke-width="1"/>')
    for tick in ticks:
        px = x(tick)
        parts.append(f'<line x1="{px:.1f}" x2="{px:.1f}" y1="{base}" y2="{base + 4}" stroke="var(--axis)"/>')
        parts.append(f'<text x="{px:.1f}" y="{base + 17}" text-anchor="middle">{_utc(tick, step < 60_000)}</text>')
    return parts


def _price_chart(symbol: str, series: dict, alerts: list[dict], missed: list[dict], width: int) -> str:
    points = series["points"]
    t0 = min([p["t"] for p in points] + [a["time"] for a in alerts])
    t1 = max([p["t"] for p in points] + [a["time"] for a in alerts])
    if t1 == t0:
        t1 = t0 + 1_000
    low = min(p["low"] for p in points)
    high = max(p["high"] for p in points)
    pad = (high - low) * 0.06 or abs(high) * 0.01 or 1.0
    ticks = nice_ticks(low - pad, high + pad, 5 if width >= WIDE else 4)
    x = _Scale(t0, t1, LEFT, width - RIGHT)
    y = _Scale(ticks[0], ticks[-1], PRICE_HEIGHT - BOTTOM, TOP)
    digits = max(0, min(6, 2 - math.floor(math.log10(max(1e-12, (ticks[1] - ticks[0]) if len(ticks) > 1 else 1)))))
    parts = _axes(x, y, width, PRICE_HEIGHT, ticks, lambda v: f"{v:,.{digits}f}", t0, t1)
    for run in _runs(points, "last"):
        upper = [(x(p["t"]), y(p["high"])) for p in run]
        lower = [(x(p["t"]), y(p["low"])) for p in reversed(run)]
        parts.append(f'<path d="{_path(upper + lower)} Z" fill="var(--series)" fill-opacity="0.16" stroke="none"/>')
        parts.append(f'<path d="{_path([(x(p["t"]), y(p["last"])) for p in run])}" fill="none" '
                     f'stroke="var(--series)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for jump in missed:
        if jump.get("time") is None:
            continue
        px = x(jump["time"])
        reason = jump.get("reason", "missed")
        z = jump.get("z")
        title = (f"Missed jump: {symbol} seq {jump['seq']} at {_utc(jump['time'])} UTC, "
                 f"{jump['sigmas']:+g} sigma, {reason}" + (f" (z = {z:+.2f})" if z is not None else ""))
        parts.append(f'<g><title>{_e(title)}</title>'
                     f'<line x1="{px:.1f}" x2="{px:.1f}" y1="{TOP}" y2="{PRICE_HEIGHT - BOTTOM}" '
                     f'stroke="var(--ink-2)" stroke-width="1" stroke-dasharray="3 3"/>'
                     f'<circle cx="{px:.1f}" cy="{TOP - 8}" r="5" fill="var(--surface)" stroke="var(--ink-2)" '
                     f'stroke-width="2"/>'
                     f'<circle cx="{px:.1f}" cy="{TOP - 8}" r="11" fill="transparent"/></g>')
    for alert in alerts:
        px, py = x(alert["time"]), y(alert["price"])
        up = alert["log_return"] > 0
        tip = (f"{px:.1f},{py - 9:.1f} {px - 6:.1f},{py + 2:.1f} {px + 6:.1f},{py + 2:.1f}" if up else
               f"{px:.1f},{py + 9:.1f} {px - 6:.1f},{py - 2:.1f} {px + 6:.1f},{py - 2:.1f}")
        title = (f"Alert: {symbol} seq {alert['seq']} at {_utc(alert['time'])} UTC, price {alert['price']:,}, "
                 f"return {alert['log_return'] * 1e4:+.1f} bps, z = {alert['z']:+.2f} "
                 f"(window of {alert['count']} returns, std {alert['std'] * 1e4:.2f} bps)")
        parts.append(f'<g><title>{_e(title)}</title>'
                     f'<polygon points="{tip}" fill="var(--critical)" stroke="var(--surface)" stroke-width="2" '
                     f'stroke-linejoin="round"/>'
                     f'<circle cx="{px:.1f}" cy="{py:.1f}" r="12" fill="transparent"/></g>')
    label = f"Price of {symbol} over event time, with {len(alerts)} alerts and {len(missed)} missed jumps marked"
    return (f'<svg class="chart {_size(width)}" viewBox="0 -6 {width} {PRICE_HEIGHT + 6}" role="img" '
            f'aria-label="{_e(label)}"><title>{_e(label)}</title>{"".join(parts)}</svg>')


def _size(width: int) -> str:
    return "wide" if width >= WIDE else "narrow"


def _std_chart(symbol: str, series: dict, t0: int, t1: int, width: int) -> str:
    points = [p for p in series["points"] if p["std"] is not None]
    if not points:
        return '<p class="note">Not enough returns for a standard deviation.</p>'
    high = max(p["std"] for p in points) * 1e4
    ticks = nice_ticks(0.0, high or 1.0, target=3)
    t1 = t1 if t1 > t0 else t0 + 1_000
    x = _Scale(t0, t1, LEFT, width - RIGHT)
    y = _Scale(0.0, ticks[-1], STD_HEIGHT - BOTTOM, TOP)
    parts = _axes(x, y, width, STD_HEIGHT, ticks, lambda v: f"{v:g}", t0, t1)
    for run in _runs(series["points"], "std"):
        parts.append(f'<path d="{_path([(x(p["t"]), y(p["std"] * 1e4)) for p in run])}" fill="none" '
                     f'stroke="var(--series)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    label = f"Rolling standard deviation of {symbol}'s returns, in basis points, over event time"
    return (f'<svg class="chart {_size(width)}" viewBox="0 0 {width} {STD_HEIGHT}" role="img" '
            f'aria-label="{_e(label)}"><title>{_e(label)}</title>{"".join(parts)}</svg>')


def _legend(missed_any: bool) -> str:
    line = ('<span><svg width="22" height="12" aria-hidden="true"><rect x="0" y="1" width="22" height="10" '
            'fill="var(--series)" fill-opacity="0.16"/><line x1="0" x2="22" y1="6" y2="6" stroke="var(--series)" '
            'stroke-width="2"/></svg>Last price, with each interval\'s low–high band</span>')
    alert = ('<span><svg width="14" height="12" aria-hidden="true"><polygon points="7,0 1,11 13,11" '
             'fill="var(--critical)"/></svg>Alert (points the way the price moved)</span>')
    miss = ('<span><svg width="14" height="12" aria-hidden="true"><circle cx="7" cy="6" r="4.5" '
            'fill="var(--surface)" stroke="var(--ink-2)" stroke-width="2"/></svg>Missed injected jump</span>'
            if missed_any else "")
    return f'<div class="legend">{line}{alert}{miss}</div>'


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _tile(label: str, value: str, detail: str = "") -> str:
    return (f'<div class="tile"><div class="label">{_e(label)}</div><div class="value">{_e(value)}</div>'
            + (f'<div class="detail">{_e(detail)}</div>' if detail else "") + "</div>")


def _table(headers: list[str], rows: list[list[str]], caption: str, text_columns: tuple[int, ...] = (0,)) -> str:
    """A table; columns in `text_columns` are left-aligned, the rest (numbers) right-aligned."""
    def cell(tag: str, i: int, value: str) -> str:
        style = ' class="text"' if i in text_columns else ""
        scope = ' scope="col"' if tag == "th" else ""
        return f"<{tag}{scope}{style}>{_e(value)}</{tag}>"
    head = "".join(cell("th", i, h) for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(cell("td", i, c) for i, c in enumerate(row)) + "</tr>" for row in rows)
    return (f'<div class="card scroll"><table><caption class="note" style="text-align:left">{_e(caption)}</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


def render(report: dict) -> str:
    counts, alerts_info, lag = report["counts"], report["alerts"], report["lag_ms"]
    evaluation, settings = report.get("evaluation"), report["settings"]
    feed = report.get("feed") or "a feed"
    alerts = alerts_info["list"]
    false_keys = {(a["symbol"], a["seq"]) for a in (evaluation or {}).get("false_alert_list", [])}
    missed = [o for o in (evaluation or {}).get("outcomes", []) if o["result"] == "missed"]
    series = report.get("series", {})
    synthetic = report.get("synthetic")

    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
           f"<title>Replay dashboard: {_e(feed)}</title><style>{_CSS}</style></head><body><main>"]
    badge = ('<span class="badge">Synthetic data</span>' if synthetic else
             '<span class="badge">Not marked synthetic</span>' if synthetic is False else "")
    out.append(f"<h1>Replay of {_e(feed)}{badge}</h1>")
    times = [p["t"] for s in series.values() for p in s["points"]]
    if times:
        out.append(f'<p class="sub">Event time {_e(_utc_full(min(times)))} to {_e(_utc_full(max(times)))}.</p>')
    out.append(f'<p class="note">Window {settings["window_ms"] / 1000:g} s · allowed lateness '
               f'{settings["lateness_ms"] / 1000:g} s · alert at |z| ≥ {settings["threshold"]:g} after '
               f'{settings["min_count"]} returns, then {settings["cooldown_ms"] / 1000:g} s quiet</p>')

    tiles = [_tile("Lines received", _num(counts["received"])),
             _tile("Released", _num(counts["released"]), "in event-time order"),
             _tile("Duplicates dropped", _num(counts["duplicates"])),
             _tile("Late, dropped", _num(counts["late"]), "behind what was released"),
             _tile("Alerts", _num(alerts_info["fired"]), f"{_num(alerts_info['suppressed_by_cooldown'])} more "
                   "suppressed by the cooldown")]
    if evaluation is not None:
        tiles.append(_tile("Injected jumps detected", f"{evaluation['detected']} of {evaluation['jumps']}",
                           f"{evaluation['false_alerts']} false alerts"))
    if lag["updates"]:
        tiles.append(_tile("Event-time lag, median", f"{lag['median'] / 1000:,.1f} s",
                           f"p99 {lag['p99'] / 1000:,.1f} s · max {lag['max'] / 1000:,.1f} s"))
    out.append(f'<section class="tiles" aria-label="Headline numbers">{"".join(tiles)}</section>')

    if not series:
        out.append('<p class="note">No events were released, so there is nothing to chart.</p>')
    for symbol, data in sorted(series.items()):
        own_alerts = [a for a in alerts if a["symbol"] == symbol]
        own_missed = [m for m in missed if m["symbol"] == symbol]
        sym_counts = report["symbols"].get(symbol, {})
        points = data["points"]
        t0 = min([p["t"] for p in points] + [a["time"] for a in own_alerts])
        t1 = max([p["t"] for p in points] + [a["time"] for a in own_alerts])
        out.append(f"<h2>{_e(symbol)}</h2>")
        out.append(f'<p class="note">{_num(sym_counts.get("released"))} prices released · '
                   f'{_plural(len(own_alerts), "alert")} · {_plural(len(own_missed), "missed jump")} · '
                   f'each point summarises '
                   f'{data["width_ms"] / 1000:,.3g} s of event time; gaps in the line had no events</p>')
        out.append('<div class="card">' + _legend(bool(own_missed)) + "<h3>Price</h3>"
                   + "".join(_price_chart(symbol, data, own_alerts, own_missed, w) for w in (WIDE, NARROW))
                   + "<h3>Rolling standard deviation of returns (bps)</h3>"
                   + "".join(_std_chart(symbol, data, t0, t1, w) for w in (WIDE, NARROW)) + "</div>")

    out.append("<h2>Alerts</h2>")
    if alerts:
        rows = [[_utc(a["time"]), a["symbol"], str(a["seq"]), f"{a['log_return'] * 1e4:+.1f}", f"{a['z']:+.2f}",
                 str(a["count"]), "–" if evaluation is None else ("false alert" if (a["symbol"], a["seq"]) in false_keys
                                                                  else "injected jump")]
                for a in alerts[:MAX_TABLE_ROWS]]
        more = f"; the first {MAX_TABLE_ROWS} of {len(alerts):,} (all are in the JSON report)" if len(alerts) > \
            MAX_TABLE_ROWS else ""
        out.append(_table(["Time (UTC)", "Symbol", "Seq", "Return (bps)", "z", "Window", "Against the truth"],
                          rows, f"Each alert, with the window it was compared against{more}.", (0, 1, 6)))
    else:
        out.append('<p class="note">No alerts fired.</p>')

    if evaluation is not None:
        out.append("<h2>Missed jumps</h2>")
        if missed:
            rows = [[m["symbol"], str(m["seq"]), "–" if m.get("time") is None else _utc(m["time"]),
                     f"{m['sigmas']:+g}", m["reason"], "–" if m.get("z") is None else f"{m['z']:+.2f}"]
                    for m in missed[:MAX_TABLE_ROWS]]
            out.append(_table(["Symbol", "Seq", "Time (UTC)", "Size (σ)", "Why missed", "z"], rows,
                              "Injected jumps no alert caught, and why.", (0, 4)))
        else:
            out.append(f'<p class="note">None: all {evaluation["jumps"]} injected jumps were detected.</p>')
        note = evaluation["note"]
        out.append(f'<p class="note">{_e(note[:1].upper() + note[1:])}.</p>')

    out.append("<h2>Per symbol</h2>")
    rows = [[symbol, _num(c["received"]), _num(c["accepted"]), _num(c["duplicates"]), _num(c["late"]),
             _num(c["released"]), _num(c["alerts"])] for symbol, c in sorted(report["symbols"].items())]
    out.append(_table(["Symbol", "Received", "Accepted", "Duplicates", "Late", "Released", "Alerts"], rows,
                      "Counts per symbol."))
    timing = report.get("timing", {})
    speed = (f" Replayed at {timing['events_per_second']:,} events/s on the machine that ran it."
             if timing.get("events_per_second") else "")
    out.append(f"<footer>Made by riskmon {_e(__version__)}.{_e(speed)} An alert is an observation to "
               "investigate, not a confirmed fault.</footer></main></body></html>")
    return "\n".join(out) + "\n"
