# Numerics: how accurate are the rolling statistics?

Measured on the 2-CPU build machine with `python scripts/numerics.py`, which reproduces every
number below (seeds fixed).

## The textbook formula fails; Welford's method doesn't

1,000 values near 10⁹ with a spread of about 1 (seed 7):

| Method | Variance | Relative error |
|---|---:|---:|
| Reference (exact summation, two passes) | 0.988507 | — |
| Welford's method (`RollingStats`) | 0.988507 | 9.9 × 10⁻⁹ |
| Textbook: (Σx² − (Σx)²/n) / (n − 1) | **−131.2** | meaningless, and negative |

The textbook formula subtracts two numbers near 10²⁴ whose difference is near 10³; double
precision keeps about 16 significant digits, so the difference is rounding noise. A test keeps
this example, so the reason for the design stays checkable.

## Drift over a million updates, with and without rebuilds

A window of 1,000 values sliding over 1,000,000 values; the statistics are compared with the
reference every 997 updates; the worst relative error is reported (seed 11):

| Values | Rebuild | Mean | Variance | Time |
|---|---|---:|---:|---:|
| Returns, mean 0, std 5 × 10⁻⁴ | never | 2.4 × 10⁻¹⁵ | 1.5 × 10⁻¹³ | 1.1 s |
| | every 1,000 removals | 3.1 × 10⁻¹⁶ | 7.9 × 10⁻¹⁵ | 1.3 s |
| Prices, 100 ± 0.05 | never | 9.9 × 10⁻¹⁴ | 3.3 × 10⁻¹¹ | 1.0 s |
| | every 1,000 removals | 5.3 × 10⁻¹⁵ | 8.6 × 10⁻¹³ | 1.1 s |
| Readings, 10⁹ ± 1 | never | 0 | 0 | 1.0 s |
| | every 1,000 removals | 0 | 0 | 1.1 s |

- **Rebuilding every 1,000 removals cuts the variance error 20 to 40 times** wherever Welford's
  updates are used, for about 10% more time. It is the default.
- **The error grows with mean² / variance**, the problem's condition number. For readings of
  10⁹ ± 1 that is about 10¹⁸, and before the residue guard (below) existed the same run measured
  1.2 × 10⁻⁵ without rebuilds and 2.8 × 10⁻⁷ with them: only about 9 of 16 digits survive a
  streaming update there. The zeros above are the guard recomputing exactly, not Welford's
  method getting better.

## The residue guard, and what it costs

Hypothesis found two cases where rounding residue mattered:
- **A removal can leave residue.** Removing a value can leave equal values with a variance of
  about 10⁻²⁸ instead of 0.
- **The naive reference had it too.** `fsum(values) / n` is not always the representable mean:
  three copies of 100.1 gave 100.09999999999998 and a variance of 3 × 10⁻²⁸.

Either would make an alert divide by a standard deviation of about 10⁻¹⁴, turning any small return
into a z-score of millions. Two fixes:
1. **Corrected two-pass:** the reference and the rebuild use the corrected two-pass method, so
   equal values give exactly 0.
2. **The guard:** the window keeps a bound on the rounding error its updates could have added. It
   recomputes exactly when the variance is positive but within that bound.

The guard costs nothing for centred data and a full pass for badly conditioned data. Measured
with the statistics read after every update, 50,000 updates into a full 1,000-value window:

| Values | Time per update | Exact recomputes beyond the scheduled ones |
|---|---:|---:|
| Returns, std 5 × 10⁻⁴ | 1.85 µs | 0 |
| Prices, 100 ± 0.05 | 1.88 µs | 0 |
| Readings, 10⁹ ± 1 | 104 µs | every read |

The monitor works on returns, so it never pays. For uncentred data, the guard chooses correct
over fast, which is the right default for a risk tool, and subtracting a reference level first
is the real cure.

## Test tolerance

|Δmean| ≤ 10⁻¹² × H and |Δvariance| ≤ 10⁻⁹ × variance + 10⁻¹² × H² (+ 10⁻³²⁰ for subnormal
results), where H is the largest magnitude that has been in the window. Removal error is on the
scale of what was removed, so H, not the current contents, is the honest scale. Equal values must
give a variance of exactly 0, tested on its own because that tolerance alone would allow residue.
