# We tested our own forecast. It doesn't work.

**2026-07-22 · T4 resolved 7 months early · written before our own first call grades**

We ran nowflation's prediction method backwards over 42 historical official prints, walk-forward,
refitting every month, with no lookahead. Then five independent adversarial reviews attacked the
backtest itself. This is what we found, including where the first version of our own audit was
wrong.

## 1. The forecast does not beat doing nothing

Naive persistence — simply repeating the last published food-CPI print — beats our model.

| Configuration (live, base 2023=100) | MAE | vs persistence | wins | DM p |
|---|---|---|---|---|
| Shipped method, h=2 | 2.45 | **64% worse** | 18% | 0.0001 |
| Shipped method, full month | 2.37 | **58% worse** | 18% | 0.0013 |
| Naive persistence | 1.49 | — | — | — |
| Persistence + basket (nests persistence) | 1.51 | 1% worse | 47% | 0.92 |

The third row is the decisive one. `persistence + basket` *contains* naive persistence as a
special case (coefficient on the basket = 0), so if the weekly survey carried information the
regression would find it. It doesn't: the fitted basket weight is 0.004–0.05 and the result is a
tie. **The weekly basket adds nothing measurable on top of last month's number.**

### Correction to our own first result

Our initial run reported the method as *266% worse*. That figure was inflated roughly fourfold by
a configuration error we made: it trained on CPI base 2020=100, reaching back to 2022, while the
live page only ever trains on base 2023=100 (2024 onward). Three of the five reviews caught it
independently. The corrected figure is 25–64% worse — still losing, on every defensible training
window we tested (vintage-faithful 2023 base, 2020 base restricted to 2024+, and rolling 18/24/30
month windows), at every horizon.

## 2. It does not lead the CPI. It moves with it.

Our published copy said the nowcast runs "ahead of the official print." The correlation structure
says otherwise:

| | contemporaneous | leads 1mo | leads 2mo | leads 3mo |
|---|---|---|---|---|
| corr(basket, official food CPI) | **+0.916** | +0.867 | +0.762 | +0.627 |

Correlation is *highest at zero lag* and decays monotonically. The information is contemporaneous,
not leading. A weekly series that moves with a monthly one still arrives earlier on the calendar —
that timing advantage is real — but it is not forecasting, and we should not have implied it was.

## 3. Why the in-sample fit looked good

The page calibrated a slope of 0.310 with R²=0.84 and we believed it. Fitted honestly on past-only
data, that slope has a median of **+0.013 and is negative in 48% of monthly refits**.

The same correlation is +0.92 on 2024–2026 and +0.27 on 2022–2026. The strong relationship exists
only inside the dzud shock window, where meat prices and food CPI trended upward together. That is
co-movement during a common shock, not predictive structure — and an R² computed on the window
that contains the shock cannot tell the difference.

## 4. The band was not a real interval

The live call carries ±3.8pp, derived as 2× the *in-sample* RMSE of that spurious fit. Realised
out-of-sample coverage of that band is **38%**, against the ~95% a 2σ interval implies.

There is a second, opposite problem worth stating plainly: because our point estimate lands close
to persistence anyway, a band this wide around it will usually contain the answer. So a "HIT" on
this call would not be evidence the method works. We are flagging that **before** the call grades,
not after.

## 5. What we tested to try to rescue it

- **Direction** — does the basket call the sign of the next change better than chance? No.
- **Turning points** — a weak contemporaneous signal exists (in-sample ceiling ~10%), but no
  walk-forward specification converts it into out-of-sample skill.
- **Longer horizons** (2–3 months) — worse, not better.
- **Different targets** — headline CPI is a worse target than food, not a better one.
- **Alternative benchmarks** — we tested five; only one beat naive persistence, which makes our
  model look worse rather than better.

## 6. Honest limits of this finding

With 42 overlapping monthly year-on-year observations, the effective sample is roughly 2–5
independent inflation episodes. That cuts both ways, and we will not overclaim: this does not prove
the weekly survey can *never* forecast the CPI. What it establishes is narrower and sufficient —
**the method we shipped is demonstrably worse than doing nothing, and no specification we tested
demonstrates skill.** Absence of demonstrated skill is not proof of impossibility; it is, however,
a complete answer to whether we should have been selling a forecast. We should not.

## 7. What changes

1. **The open 2026-07 call is not edited.** The ledger is append-only; that is the whole point of
   it. It grades on schedule and this note is appended alongside it, dated before the release.
2. **No new calibrated calls** until a specification beats naive persistence out-of-sample. When
   calls resume, persistence is published beside every one as the benchmark to beat.
3. **The "ahead of the CPI" framing comes off the page.** What replaces it is the claim we can
   actually defend: weekly measurement, published on a weekly clock, against a monthly official
   series.
4. **The monitoring product is untouched** — and it was always the part with no model risk:
   weekly UB food and fuel prices, administered fuel steps (A-92 moved +9.7% in one survey step),
   all 21 aimags (in the week of 2026-07-20, the same cut of beef ranged 56% between the
   cheapest and dearest aimag — 70% three weeks later). These are
   measurements, not estimates. Nothing above weakens a single one of them.

The backtest, its cached inputs and every individual prediction are in this repository. Anyone can
re-run it and check whether we graded ourselves honestly.
