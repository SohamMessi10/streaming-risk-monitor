# Alerts: the rule and how well it works

Every number here comes from [`scripts/alert_quality.py`](../scripts/alert_quality.py) on
seeded synthetic feeds, so a rerun gives the same counts (timings vary by machine). The feeds are
synthetic, so these results show the rule finds what was injected and fires falsely about as
often as theory says. They don't show how it would do on real prices, whose returns have fatter
tails than the normal ones generated here.

## The rule

For each released return r, against the window *before* r is added:

    z = (r - mean) / std

An alert fires when |z| ≥ 5, the window holds at least 30 returns, and the symbol's last fired
alert is at least 30 s of event time earlier. A return blocked only by the cooldown is counted as
suppressed. The defaults were chosen before any of the measurements below, and weren't tuned
on them.

## False alerts: measured against exact theory

The test runs feeds with no jumps, so every alert is false: 10 seeds × 10 symbols × 20,000 events,
about 2 million eligible returns. The cooldown is off, so every |z| counts.

**What to expect.** The obvious estimate is the normal tail, erfc(t/√2). It is wrong here because
the standard deviation is *estimated*. With a mean and a sample standard deviation from n earlier
normal returns, a new return's z is exactly Student's t with n − 1 degrees of freedom, times
√(1 + 1/n). This is the prediction-interval result. So the expected count sums
P(|T₍ₙ₋₁₎| ≥ t / √(1 + 1/n)) over every eligible return, each with its own window size n.

The script computes the t tail from the regularized incomplete beta function. Before using it, it
checks the function against independent closed forms:

- the Cauchy distribution (1 degree of freedom);
- a finite sum that is exact for even degrees of freedom, at 2 to 1,000.

The two agree to about 10⁻⁷. One catch: at enormous degrees of freedom (around 10¹⁰), the
`lgamma` terms cancel and lose digits. The replay never comes close, since its windows hold a few
hundred returns.

**Without late events** (no dropped events):

| \|z\| ≥ | alerts | expected (t) | normal tail |
|---|---|---|---|
| 4.0 | 178 | 168.0 | 126.5 |
| 4.5 | 20 | 20.9 | 13.6 |
| 5.0 | 1 | 2.2 | 1.1 |
| 5.5 | 0 | 0.2 | 0.1 |

Observed and expected agree within counting noise (√168 ≈ 13). The normal tail undercounts by a
quarter: the cost of estimating the standard deviation.

**With the generator's late events** (0.2% of events arrive 10 s late and are dropped):

| \|z\| ≥ | alerts | of which span a dropped event | the rest | expected (t) |
|---|---|---|---|---|
| 4.0 | 191 | 19 | 172 | 167.8 |
| 4.5 | 25 | 7 | 18 | 20.9 |
| 5.0 | 5 | 3 | 2 | 2.2 |
| 5.5 | 2 | 2 | 0 | 0.2 |

**A finding.** When an event is dropped as late, the next return runs from the price before it
to the price after it. That return spans two random-walk steps, so it is √2 times as wide. Such
returns are only 0.19% of the eligible ones (3,874 of 1,993,010), yet they produce 3 of the 5
false alerts at |z| ≥ 5 and both at 5.5. Without them, the counts match theory again.

**Possible fixes**, not built here:
- flag an alert whose return spans a dropped event;
- scale each return by the square root of the time it covers.

## Detection: how big a jump the rule can see

Feeds with injected jumps: 30 seeds × 10 symbols × 3,600 events, 2,106 jumps per size. This uses
the default rule, cooldown included.

| jump | detected | recall | false alerts | missed: below threshold | cooldown | too few returns |
|---|---|---|---|---|---|---|
| 3σ | 0 | 0.0% | 0 | 2,091 | 0 | 15 |
| 4σ | 1 | 0.0% | 0 | 2,090 | 0 | 15 |
| 5σ | 727 | 34.5% | 0 | 1,350 | 14 | 15 |
| 6σ | 1,968 | 93.4% | 0 | 19 | 104 | 15 |
| 8σ | 1,980 | 94.0% | 0 | 1 | 110 | 15 |

- **A jump the size of the threshold is caught about a third of the time (34.5%).** A 5σ jump
  only just reaches the threshold before the window's own noise, which can push it either side.
  Two things push it below more often than above:
  - the estimated standard deviation varies, and the t tail is wider than the normal;
  - earlier jumps still in the 5-minute window widen it further.

  From 6σ on, nearly every jump clears it.
- **The cooldown is the main cost at large sizes: about 5% of jumps.** At this jump rate, two
  jumps on one symbol land within 30 s about that often, and the second is suppressed. That is the
  trade the cooldown makes: in a real volatile burst it stops a flood of alerts, and here it
  hides a second, separate jump. The report counts both the suppressed alerts and the jumps they
  hid.
- **Jumps in a symbol's first 30 returns are never alerted (0.7%)**, by design: a standard
  deviation from fewer returns is too unreliable.
- **No false alerts in any detection run.** That is plausible, not proof. The five sizes use the
  same seeds, and a jump replaces a step without changing the random draws, so all five runs share
  the same noise: one sample of about 1.07 million eligible returns. The calibration predicts about
  1.2 false alerts at |z| ≥ 5 for that many returns, and fewer here, because jumps widen the window.
  Seeing none has a probability of about 30%.

## How alerts are scored

The truth file lists each injected jump by symbol and seq. A jump is **covered** by the released
return that spans it: the return from the symbol's previous released seq p to seq s covers jump j
when p < j ≤ s. Usually s = j. If the jump's own event was dropped as late, the next return
carries the move, and an alert there still counts as detecting it.

- A covered jump without an alert is **missed**, with its reason.
- A jump no released return covers is missed as "not released".
- An alert that covers no jump is a **false alert**.
- Recall is detected / jumps; precision is (alerts covering a jump) / alerts.
