# Progress

## Status

**Milestone 1 of 6 complete: events and the synthetic feed.** Next: rolling statistics (the
sliding window, Welford's method with removal, the batch reference). The plan is in
[`docs/design.md`](docs/design.md).

Work happens on a branch per milestone, merged by pull request.

## Milestones

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
```

## Concepts to review

- Event time versus arrival order, and why integer timestamps.
- Geometric random walk: why log returns, and why sigma is per step.
- Why a truth file makes synthetic data useful for evaluation.
