# nowflation.mn

Weekly nowcast of Ulaanbaatar food prices, computed from the National Statistics Office of
Mongolia's own weekly price survey — published ahead of each monthly CPI print.

## How it works

- **Data**: NSO PXWeb open data (data.1212.mn) — table `DT_NSO_0600_001V4` (weekly UB food
  and fuel prices) and table `DT_NSO_0600_010V1` (official monthly CPI by group, base
  2023=100). NSO licenses this data CC BY 4.0.
- **Build**: `python build_nowflation.py` fetches both tables, recomputes every figure, and
  writes `index.html`. Nothing on the page is typed in by hand; a failed fetch falls back to
  the committed caches and the page stamps itself accordingly.
- **Scored calls**: `calls_ledger.json` is append-only. Every prediction of the official
  food-CPI print is dated and banded *before* the release and graded automatically from the
  same NSO table *after* it. A call is never edited once made — the git history of this
  repository is the tamper-evidence for those dates.
- **Rebuilds**: GitHub Actions, daily at 11:00 Ulaanbaatar (`.github/workflows/rebuild.yml`).
  Each run commits the refreshed page, caches and ledger, so every data vintage is archived
  in git.
- `anchors.json` holds two public facts from the latest official CPI release (the next
  release date and the report's UB beef average) used by the validation line.

## What this is not

This is a nowcast, not an official statistic. It tracks a narrow basket of Ulaanbaatar
retail prices, is published independently of the National Statistics Office, does not
replace the consumer price index, and makes no claim to reproduce it.

The name is a nod to [nowflation.com](https://nowflation.com), Steven Fiorillo's daily US
inflation gauge, which inspired this project — the two are otherwise unrelated.

Data: © National Statistics Office of Mongolia, CC BY 4.0, via data.1212.mn.
