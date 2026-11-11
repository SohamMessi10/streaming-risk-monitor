# Streaming Risk Monitor: design

## Problem

A risk or monitoring system watches a stream of prices and has to say, as each one arrives,
whether something unusual just happened. Three things make that harder than it looks:

1. **Events arrive out of order, late, or twice.** Feeds are delivered over networks; a price
   stamped 10:00:01 can arrive after one stamped 10:00:02, and retries resend events. Statistics
   computed in arrival order differ from the truth.
2. **Running statistics drift.** Updating a variance as values enter and leave a window is fast,
   but the textbook formula (sum of squares minus square of sum) loses all precision when values
   are large and close together, and even good update formulas accumulate rounding error over
   millions of updates.
3. **"Unusual" isn't "wrong".** A return five standard deviations from the recent mean is worth
   a look; it isn't proof of a problem, and an alert should say what it saw.

**Streaming Risk Monitor** replays a timestamped price feed, keeps rolling statistics per symbol
over an event-time window, flags unusual returns with an explanation, and proves its streaming
results equal a from-scratch batch computation within a stated tolerance.

**User:** engineers and analysts building or learning streaming analytics (market data, sensor
data). **Value:** correct event-time semantics and numerics that are tested, not assumed.

## Scope

**MVP**
- Read a feed (JSON Lines) of price events; a seeded synthetic feed generator that injects
  duplicates, out-of-order and late events, gaps and price jumps, labelled as synthetic.
- Per symbol: log returns over a sliding event-time window; count, mean, variance and standard
  deviation, updated incrementally as returns enter and leave.
- Event time with a watermark: events are reordered within an allowed lateness; later ones are
  dropped and counted; duplicates are dropped and counted.
- Alerts: a return whose z-score against the window exceeds a threshold, with a minimum number
  of observations and a cooldown; each alert records the values behind it.
- A batch reference that recomputes every output from scratch; tests that streaming equals batch.
- CLI: `riskmon generate`, `riskmon replay`, reports as JSON; a static HTML dashboard of a replay.

**Not in scope:** live market connections, trading, multiple processes, persistence across
restarts. The feed is synthetic or a file you supply.

## Event time

| Term | Meaning here |
|---|---|
| Event time | When the price was observed, from the event's own timestamp |
| Arrival order | The order events appear in the feed |
| Watermark | Per symbol: the latest event time seen, minus the allowed lateness |
| Release | Buffered events go out in (time, seq) order once their time is *strictly* before the watermark |
| Late event | One that sorts, by (time, seq), at or before something already released: it can't be placed correctly, so it is dropped and counted |
| Duplicate | Same symbol and sequence number as an event accepted within the last window of event time: dropped and counted (an older copy is behind what was released, and counted as late) |

**The guarantee:** an event delayed by at most the allowed lateness is never late. So for any
feed whose delays stay within it, the output equals the output on the same events sorted by
(time, seq), float for float (a property test checks it). Releasing *strictly* before the
watermark is what makes this hold. Releasing at the watermark itself could put out a same-time
event with a higher seq before a lower one, delayed by exactly the lateness, arrived.

**The cost is latency:** a result comes out more than `allowed_lateness` after its event (measured
on the default feed: at least 2,001 ms for a 2,000 ms lateness, median 2.7 s).

**A limitation:** a symbol's watermark moves only when that symbol has a new event, so after a gap
its last events wait for the next one (the default feed has 60-second gaps, giving lags up to
64.5 s). Production systems add an idle timeout that advances the watermark by wall-clock time;
here the end of the feed flushes everything.

## Statistics

- Returns are log returns between consecutive accepted prices of a symbol.
- The window holds returns whose event time is within the last `window` (event time), not the
  last N events, so gaps in the feed shrink the window instead of stretching it.
- Mean and variance are updated with Welford's method, including its removal step. Removal is
  less stable than addition, so the window recomputes its sums exactly from its contents every
  `rebuild_every` removals, bounding drift; the drift is measured, not assumed small.
- A reference implementation recomputes each output from the window's values with exact
  summation (`math.fsum`), and the tests require agreement within a documented tolerance,
  including for constant data, very large and very small values.

## Alerts

An alert fires when |z| = |return − mean| / std exceeds the threshold, with at least
`min_count` returns in the window and std above zero, and not within `cooldown` (event time) of
the symbol's previous alert. The z-score uses the window before the new return is added, so a
jump isn't hidden by its own effect on the statistics. Each alert records the return, mean, std,
z and window size, and is described as an observation to investigate, not a confirmed fault.

## Architecture

```
 feed.jsonl ─► reader (strict parsing, line numbers in errors)
                  │
                  ▼
           per-symbol reorder buffer ── watermark ──► late / duplicate counters
                  │ (event-time order)
                  ▼
           returns ─► sliding window (Welford + periodic rebuild) ─► alert rule
                  │                                                   │
                  ▼                                                   ▼
           state snapshots (JSON)                              alert log (JSON)
                  │
                  ▼
           static HTML dashboard (inline SVG, no scripts)
```

## Stack

Python 3.12–3.14, standard library only at runtime (`math.fsum`, `collections.deque`,
`heapq`); pytest and Hypothesis for tests; ruff for lint. NumPy isn't needed: every computation
is per event, and the reference uses exact summation.

## Testing

- Streaming output equals the batch reference on the same accepted events, for random feeds
  (Hypothesis), within tolerance.
- Any shuffle of arrival order within the allowed lateness gives identical output.
- Duplicates and late events are counted exactly; memory stays bounded by the window.
- Numerics: constant data gives variance exactly 0; values near 1e9 and near 1e-9; a measured
  drift experiment over a long stream, with and without rebuilds.
- Mutation testing, as in earlier projects.

## Milestones

Each milestone is a branch with several commits, reviewed and merged as a pull request.

0. Project setup and this design (on `main`).
1. Events: the feed format, a strict reader, the synthetic feed generator.
2. Rolling statistics: the sliding window, Welford with removal, the batch reference, numerics.
3. Event time: reorder buffer, watermark, late and duplicate handling, per-symbol engine.
4. Alerts and the `replay` command, with measured throughput and lag.
5. The HTML dashboard.
6. README, results, and the final audit.
