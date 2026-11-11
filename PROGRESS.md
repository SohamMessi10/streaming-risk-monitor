# Progress

## Status

**Milestone 2 of 6 complete: rolling statistics.** Next: event time (the reorder buffer,
watermark, and late and duplicate events). The plan is in [`docs/design.md`](docs/design.md); the
accuracy measurements are in [`docs/numerics.md`](docs/numerics.md).

Work happens on a branch per milestone, merged by pull request.

## Milestones

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
python -m ruff check src tests
python -m pytest
riskmon generate out/feed.jsonl
python scripts/numerics.py
```

## Concepts to review

- Event time versus arrival order, and why integer timestamps.
- Geometric random walk: why log returns, and why sigma is per step.
- Why a truth file makes synthetic data useful for evaluation.
- Catastrophic cancellation; Welford's method and why removal is less stable; condition number
  (mean² / variance).
- The corrected two-pass algorithm; subnormal numbers.
