# Progress

## Status

**Milestone 5 of 6 complete: the HTML dashboard.** Next: README, results and the final audit. The plan
is in [`docs/design.md`](docs/design.md). The measurements are in
[`docs/numerics.md`](docs/numerics.md) (statistics) and [`docs/alerts.md`](docs/alerts.md)
(alerts).

## Milestones

### Milestone 5: the dashboard

- `riskmon replay FEED --dashboard out.html`, or `riskmon dashboard REPORT out.html` from a
  saved report, writes one self-contained HTML file. It uses inline SVG, no scripts, and a
  Content-Security-Policy forbidding scripts and network loads.
  - **Headline numbers:** counts, alerts, jumps detected, and lag.
  - **Per symbol:** a price chart with each interval's low–high band, alerts as triangles pointing
    the way the price moved, and missed jumps marked along the top; under it, the rolling standard
    deviation. They are two charts, never two y-axes.
  - **Tables** of alerts (hit or false), missed jumps with reasons, and per-symbol counts.
  - **Hover titles** give the numbers behind every mark.
- `series.py`: at most 600 points per symbol, in memory that doesn't grow with the feed.
  - Time buckets aligned to the first event double in width and merge in pairs when there are too
    many. A property test checks this equals bucketing every price at the final width.
  - A missing bucket breaks the line, since there were no events there.
- **Checked in a browser** (Playwright, Chromium) at 1280 px in light and dark mode and at 390 px
  (phone): no console errors, no failed requests, and no sideways page scroll.
  - At first the phone layout scrolled each chart inside its card, with half the chart hidden.
    Each chart is now drawn at two widths and a media query picks one.
- **Chart colours** (series blue, critical red) pass the palette validator in both modes: contrast
  of at least 3:1 and colour-blind separation ΔE of at least 23.8. Alerts carry a shape and a
  table row as well as colour.
- **Measured:**
  - The default feed's dashboard is 130 KB; a 200,000-event, 10-symbol replay's is 727 KB.
  - Keeping the series first cost about 1 µs per event (161k → 139k events/s in memory).
    Updating the current bucket in place, instead of merging a new one, brought it to 156k.
    `riskmon replay` from the 200,000-line file runs at about 70k events/s.
- **Fixed while building:**
  - The price axis ended short of round values, so only two ticks showed. The axis now starts and
    ends on ticks.
  - Symbols were listed in the order their first price came out; they are now sorted by name.
  - The note under the missed jumps started in lower case, and "1 missed jumps" is now singular.
  - **Four of my test expectations were wrong:** a "no URLs" check that matched `http-equiv`; two
    regexes that missed the left-aligned time column; and 15-minute ticks where six 10-minute
    ticks cover an hour.

**Tests (182):**
- *The downsampler:* Hypothesis properties against direct bucketing and the smallest width that
  fits; bounded memory over 200,000 prices; time going backwards refused.
- *The page:* well-formed HTML with no scripts, URLs or event attributes; hostile feed names and
  symbols escaped; every alert drawn on both widths and listed in the table; triangle direction;
  lines breaking at gaps; the axis ending on ticks; the false-alert label; an empty replay; a
  single price; long tables cut with a note; symbol order.
- *The commands:* the same page from a live replay and a saved report; bad reports refused;
  unwritable outputs.

**Mutation-checked:** 20 deliberate bugs in the downsampler and dashboard. 13 were caught at
once and 7 survived:
- 6 were test gaps: line breaks at gaps, triangle direction, the false-alert label, plurals, the
  axis domain, and the settings line. Each now has a test.
- 1 was equivalent: a reset that the bucket-index check already made redundant, so I removed it.

### Milestone 4: alerts and replay

- `alerts.py`: z-scores against the window *before* each return.
  - An alert fires at |z| ≥ 5, with at least 30 returns in the window, and 30 s of event time
    after the symbol's last fired alert.
  - A return blocked only by the cooldown is counted as suppressed.
  - Each alert records the return, mean, std, z and window size.
- `replay.py` and `riskmon replay FEED [--report out.json]` give counts, alerts, event-time lag
  and throughput.
- **Scoring against the truth file.** Each injected jump is matched to the released return that
  spans it, even when the jump's own event was dropped as late. It is then detected, or missed
  with a reason: too few returns, cooldown, below threshold, no spread, or not released. Alerts
  on no jump are false alerts.
- **Default feed:**
  - 19 of 20 jumps detected and 0 false alerts.
  - The miss is the cooldown: two jumps on SYNB 21 s apart.
  - Lag: median 2,716 ms, p99 6,939 ms.
- **Measured by `scripts/alert_quality.py`** (written up in `docs/alerts.md`; synthetic data, so
  it shows the rule finds what was injected, not how it would do on real prices):
  - **False alerts against exact theory.** With a standard deviation estimated from n returns, z
    is Student's t with n − 1 degrees of freedom times √(1 + 1/n). The expected count from that
    matches the observed one: on about 2 million returns with no jumps, 178 / 20 / 1 alerts at
    |z| ≥ 4 / 4.5 / 5, against 168.0 / 20.9 / 2.2 expected. The normal tail undercounts by a
    quarter.
  - **A finding:** a dropped late event makes the next return span two steps, √2 times wider.
    Those returns are only 0.19% of all returns but produce 3 of the 5 false alerts at |z| ≥ 5.
    Without them, the counts match theory again.
  - **Detection by jump size** (2,106 jumps each): 0% at 3σ and 4σ, 34.5% at 5σ, 93.4% at 6σ,
    94.0% at 8σ. Above 6σ the misses are the cooldown (about 5%) and jumps in a symbol's first 30
    returns (0.7%).
  - **Throughput**, single-threaded on a 2-vCPU cloud container with Python 3.13:
    - 161k events/s for the engine and alerts on events in memory;
    - 215k lines/s for strict parsing alone;
    - 78k events/s for `riskmon replay` from a 200,000-line file.
- **Fixed while building:**
  - **p99 was off by one.** It took the ⌊0.99 n⌋-th index, giving the maximum for 100 values; it
    is now the nearest rank.
  - **Two of my tests had wrong expectations:** release is strict, so 3 events, not 2, wait for
    the flush; and alerts from two arrival orders must be compared per symbol.
  - **The t-tail function's own check was wrong at first.** I compared it with the normal tail at
    10⁷ degrees of freedom, where the true difference (1.7 × 10⁻⁵) exceeded my tolerance. At
    10¹⁰, `lgamma` cancellation took over. It is now checked against exact closed forms over the
    range the replay uses.

**Tests (143):**
- The alert rule's every boundary: threshold, min count, cooldown, a suppressed alert not
  extending the cooldown, per-symbol cooldowns, and the window before versus after.
- Scoring: a late jump covered by the next return, every miss reason, precision and recall,
  per-symbol scoring, alerts independent of arrival order within the lateness, and the default
  feed pinned.
- The `replay` command: settings, truth files (found, named, refused, malformed, not
  synthetic), bad feeds and unwritable reports.

**Mutation-checked:** 22 deliberate bugs in the alerts, replay and command. 19 were caught at
once. The 3 survivors were test gaps:
- p99, which hid the off-by-one above;
- precision, where my first new test had equal jump and alert counts, so the wrong formula gave
  the same answer;
- a truth file not marked synthetic.

Each now has a test, and all 22 are caught.

### Milestone 3: event time

- `engine.py`: per symbol, a buffer ordered by (time, seq) and a watermark (the latest event time
  minus `lateness_ms`).
  - Events are released *strictly* before the watermark, then turned into log returns and
    rolling statistics. Each update records the window before and after its return.
  - Late events (behind what was released, by time and seq) and duplicates are dropped and
    counted, overall and per symbol.
  - Sequence numbers are remembered for one window, so memory stays bounded.
- **Measured on the default synthetic feed**, against its truth file:
  - all 7,276 lines received;
  - 76 duplicates dropped (76 injected);
  - 9 late (exactly the 9 events delayed beyond the 2 s lateness);
  - 7,191 released.
  - Event-time lag is at least 2,001 ms, with a median of 2.7 s. The maximum of 64.5 s comes from
    60-second gaps: a watermark moves only with new events, and the design notes the idle-timeout
    remedy.
- **Fixed while building:**
  - The first duplicate memory forgot a sequence number as soon as its event was released. That
    would have counted a quick duplicate as *late*, and it walked a queue in arrival order while
    comparing event times. It is now a heap with one window of retention.
  - A test assertion of mine ended in `or len(...) == len(...)`, a loophole that would pass any
    output of the right length. It now compares exact keys.

**Tests (92):**
- *The key property* (Hypothesis): for any arrival order with delays within the lateness, the
  output is identical, float for float, to the sorted run, and nothing is late.
- *Batch agreement:* statistics match a from-scratch batch recomputation.
- *Independence:* symbols never affect each other.
- *Edge rules:* release strictly before the watermark; lateness by (time, seq), not time alone;
  duplicates before and after release; bounded memory over 50,000 events.
- *Real data:* the truth-file counts.

**Mutation-checked:** 15 deliberate bugs in the engine. 14 were caught at once. The 15th, judging
lateness by time alone, needed a test for a same-time event with a lower seq. All 15 are now
caught.


### Milestone 2: rolling statistics

- `window.py`: `RollingStats` keeps count, mean and sample variance over the last `window_ms` of
  event time, with Welford's method for adding and removing, and an exact rebuild every 1,000
  removals. `reference_stats` recomputes from scratch (corrected two-pass, `math.fsum`).
- Measured (`scripts/numerics.py`, written up in `docs/numerics.md`):
  - On 1,000 values near 10⁹, the textbook formula gives a variance of **−131.2** where the true
    value is 0.989.
  - Over a million updates, rebuilds cut Welford's variance drift 20 to 40 times, for about 10%
    more time.
- **Found by Hypothesis, and fixed:**
  - **Rounding residue on equal values.** After a removal, equal values reported a variance of
    2 × 10⁻²⁸ instead of 0, and the reference itself gave 3 × 10⁻²⁸ for three copies of 100.1,
    because `fsum / n` isn't the representable mean.
  - **Why it mattered:** either would make a later alert divide by a standard deviation near
    10⁻¹⁴ and see z-scores in the millions.
  - **The fix:** the corrected two-pass method, plus a residue guard that recomputes exactly
    when the variance is within the window's bound on rounding error.
  - **Its cost:** for returns, 1.85 µs per update and nothing extra; for values near 10⁹, an
    exact pass on every read (104 µs) — correct over fast.
- Test tolerance: relative to H, the largest magnitude that has been in the window, since removal
  error is on the scale of what was removed. Subnormal results get a tiny absolute allowance.
  Equal values must give exactly 0.

**Tests (75):** streaming equals the reference after every value (Hypothesis, seven scales from
returns to 10⁹, with and without rebuilds; checked with 1,000 cases per run six times). Also the
window edges, time going backwards, an emptied window, constant data, non-finite values, the
textbook-formula failure, drift bounded by rebuilds, and the guard's cost.

**Mutation-checked:** 14 deliberate bugs in the window. 12 were caught. The 2 survivors are
conservative parts of the noise bound and are equivalent in practice:
- adding noise on removal, which the same value's addition already covers 16 times over;
- resetting the bound at a rebuild, whose absence only costs recomputes far beyond any test's
  horizon.


### Milestone 1: events and the synthetic feed

- `events.py`: the feed format (JSON Lines: symbol, time in integer milliseconds, seq, price)
  and a strict, lazy reader.
  - An unknown or missing field, a wrong type, a non-positive or non-finite price, invalid JSON,
    or a line over 10,000 bytes each stops the read, naming the line.
  - Integer milliseconds keep window edges exact.
- `generate.py`: a seeded synthetic feed.
  - A geometric random walk per symbol, each symbol with its own random stream.
  - Injected problems: jumps of 8 standard deviations, duplicates, delayed (out-of-order)
    events, late events and gaps.
  - The same spec gives the same file byte for byte.
  - A truth file lists everything injected, so later results can be checked against it. It is
    marked synthetic.
- `riskmon generate OUT [--symbols] [--seed] [--events]`. Exit codes: 0 done, 1 a file can't be
  written, 2 bad usage.
- The default feed, measured: 7,276 lines for 7,200 events, with 76 duplicates, 20 jumps, 377
  delayed events and 8 gaps.

**Tests (55):**
- Every rule of the reader, and a Hypothesis round trip of any valid event.
- The generator: reproducibility, independent symbols, and returns with the requested spread
  (within 3% over 20,000 steps).
- Jumps where the truth file says, gaps of the stated length, and duplicates arriving after
  their originals.

**Mutation-checked:** 16 deliberate bugs. 12 were caught at once and 1 changed nothing (my
mistake). The other 3 were test gaps, each now with a test:
- events built positionally hid a change of field order;
- a shared random stream passed a test that only compared one symbol;
- a duplicate arriving before its delayed original is visible only in the feed's order, since
  the two copies are identical.

## Commands verified

```
python -m pip install -e ".[dev]"
python -m ruff check src tests scripts
python -m pytest
riskmon generate out/feed.jsonl
riskmon replay out/feed.jsonl --report out/report.json --dashboard out/dashboard.html
riskmon dashboard out/report.json out/dashboard.html
python scripts/numerics.py
python scripts/alert_quality.py
```

## Concepts to review

- Event time versus arrival order, and why integer timestamps.
- Geometric random walk: why log returns, and why sigma is per step.
- Why a truth file makes synthetic data useful for evaluation.
- Catastrophic cancellation; Welford's method and why removal is less stable; condition number
  (mean² / variance).
- The corrected two-pass algorithm; subnormal numbers.
- Watermarks, allowed lateness, and the latency they cost; why release is strictly before the
  watermark; idle timeouts.
- z-scores against an estimated standard deviation: Student's t and the prediction interval;
  precision versus recall; what a cooldown trades; nearest-rank percentiles.
- Downsampling a stream into bounded memory (doubling bucket widths); why a dashboard needs no
  JavaScript; Content-Security-Policy; escaping untrusted text into HTML.
