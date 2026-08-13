r"""
backtest.py — does the weekly nowcast actually beat doing nothing?

This is test T4 from the kill-scorecard, pulled forward from Feb-2027 to today by running
the scored-calls method backwards over every official food-CPI print we have data for.

DELIBERATELY INDEPENDENT of build_nowflation.py: no shared imports, its own fetch, its own
year-on-year and basket arithmetic. A bug shared by the page and its own audit would be
invisible; two implementations that agree are evidence, one implementation checking itself
is not.

The honest-backtest rules, all of which make the test HARDER on us:

1. NO LOOKAHEAD ON THE TARGET. Predicting month M may only use official prints already
   RELEASED at prediction time. NSO releases month M around the 10th of month M+1, so a
   prediction made early in month M cannot see the M-1 print — it must fall back to M-2.
2. NO LOOKAHEAD ON THE MODEL. The regression for month M is fitted only on (predictor,
   official) pairs whose official value had been released by prediction time — an expanding
   window, refitted every month, never the full sample. Each TRAINING row is itself
   constructed under the same as-of rules as the prediction row.
3. NO LOOKAHEAD ON THE PREDICTOR. At horizon h, only the first h survey weeks of month M
   are used, plus a publication lag (default 7 days) before a survey week counts as known.
4. THE BENCHMARK GETS THE SAME INFORMATION. Persistence = the latest food-CPI print released
   at that same moment. Beating it is the entire product claim.

Specifications tested:
  as_shipped         — fit on FULL-month predictor means, applied to a PARTIAL month. This is
                       what build_nowflation.py currently does for the live call.
  horizon_matched    — fit on predictor means computed at the SAME horizon h being predicted.
  persistence_plus   — food[M] = a + c*food[last released print] + b*meat[M,h]. Nests naive
                       persistence (c=1, b=0), so this is the fair test of whether the weekly
                       survey adds ANY information on top of simply repeating the last print.

Run: python backtest.py [--no-fetch] [--lag-days N]
Writes backtest_results.json (every prediction, auditable) and prints the verdict.
"""
from __future__ import annotations

import json
import math
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).parent.resolve()
W_CACHE = BASE / "backtest_weekly_cache.json"
O_CACHE = BASE / "backtest_official_cache.json"
OUT = BASE / "backtest_results.json"

ROOT = ("https://data.1212.mn/api/v1/mn/NSO/" + urllib.parse.quote("Economy, environment")
        + "/" + urllib.parse.quote("Consumer Price Index") + "/")
WEEKLY_URL = ROOT + "DT_NSO_0600_001V4.px"
OFFICIAL_URL = ROOT + "DT_NSO_0600_010V1.px"

MEAT_CODES = {"9": "mutton", "10": "beef_bone_in", "11": "beef_boneless", "13": "goat"}
BOUNDS = {"mutton": (8000, 90000), "beef_bone_in": (8000, 90000),
          "beef_boneless": (10000, 110000), "goat": (5000, 80000)}
# Base years available in DT_NSO_0600_010V1. '1' (2020=100) spans the whole weekly-data era
# and is the primary sample; '2' (2023=100) is today's published base, used as a robustness
# check on the overlap. YoY percent changes differ only slightly between them.
BASES = {"1": "2020=100", "2": "2023=100"}
PRIMARY_BASE = "1"
FOOD_GROUP, HEADLINE_GROUP = "1", "0"

PUB_LAG_DAYS = 7      # a survey week is not "known" until this many days after it
RELEASE_DAY = 10      # NSO publishes month M on ~the 10th of M+1
MIN_TRAIN = 12        # months of fitted history before the model may speak
HORIZONS = [1, 2, 3, "full"]
SPECS = ("as_shipped", "horizon_matched", "persistence_plus")


# ----------------------------------------------------------------- fetch

def norm_week(label: str) -> str | None:
    """Defensive: NSO's aimag table has emitted an unpadded '2026-8-10'. This table is clean,
    but an unpadded month would break every string comparison below, so normalise on ingest.
    A no-op on already-ISO labels, so published backtest numbers are unaffected."""
    try:
        y, m, d = (int(x) for x in str(label).strip().split("-"))
        return date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


def _post(url: str, body: dict) -> dict:
    r = requests.post(url, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_weekly() -> dict:
    meta = requests.get(WEEKLY_URL, timeout=60).json()
    tv = next(v for v in meta["variables"] if v["code"] == "Хугацаа")
    labels = dict(zip(tv["values"], tv["valueTexts"]))
    data = _post(WEEKLY_URL, {"query": [
        {"code": "Бүтээгдэхүүн", "selection": {"filter": "item", "values": list(MEAT_CODES)}},
        {"code": "Хугацаа", "selection": {"filter": "item", "values": tv["values"]}},
    ], "response": {"format": "json"}})["data"]
    out: dict = {}
    for row in data:
        name = MEAT_CODES.get(row["key"][0])
        try:
            val = float(row["values"][0])
        except (TypeError, ValueError):
            continue
        wk = norm_week(labels.get(row["key"][1], row["key"][1]))
        if name and wk:
            lo, hi = BOUNDS[name]
            if lo <= val <= hi:
                out.setdefault(name, {})[wk] = val
    return out


def fetch_official() -> dict:
    """{base_code: {month: {'food': x, 'headline': y}}} for every base year."""
    meta = requests.get(OFFICIAL_URL, timeout=60).json()
    tv = next(v for v in meta["variables"] if v["code"] == "Сар")
    months = tv["values"][:120]
    labels = dict(zip(tv["values"], tv["valueTexts"]))
    out: dict = {}
    for base in BASES:
        data = _post(OFFICIAL_URL, {"query": [
            {"code": "Суурь он", "selection": {"filter": "item", "values": [base]}},
            {"code": "Бүлэг", "selection": {"filter": "item",
                                            "values": [HEADLINE_GROUP, FOOD_GROUP]}},
            {"code": "Сар", "selection": {"filter": "item", "values": months}},
        ], "response": {"format": "json"}})["data"]
        for row in data:
            grp = "food" if row["key"][1] == FOOD_GROUP else "headline"
            mon = labels.get(row["key"][2], row["key"][2])
            try:
                val = float(row["values"][0])
            except (TypeError, ValueError):
                continue
            out.setdefault(base, {}).setdefault(mon, {})[grp] = val
    return out


def cached(path: Path, fetcher, allow_fetch: bool):
    if allow_fetch:
        try:
            d_ = fetcher()
            if d_:
                path.write_text(json.dumps(d_, ensure_ascii=False, indent=1), encoding="utf-8")
                return d_
        except Exception as e:
            print(f"backtest: fetch failed ({e}) — using cache", file=sys.stderr)
    if not path.exists():
        sys.exit(f"backtest: no data and no cache at {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------- series maths

def d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def yoy(series: dict, week: str) -> float | None:
    try:
        target = d(week).replace(year=d(week).year - 1).isoformat()
    except ValueError:
        target = f"{int(week[:4]) - 1}-02-28"
    base = next((w for w in reversed(sorted(series)) if w <= target), None)
    if base is None or not series.get(base):
        return None
    return 100 * (series[week] / series[base] - 1)


def basket_yoy(weekly: dict, week: str) -> float | None:
    vals = [yoy(weekly[k], week) for k in weekly if week in weekly[k]]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def release_date(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y + 1, 1, RELEASE_DAY) if m == 12 else date(y, m + 1, RELEASE_DAY)


def predictor(month: str, horizon, byw: dict) -> tuple[float | None, list]:
    weeks = [w for w in sorted(byw) if w[:7] == month]
    use = weeks if horizon == "full" else weeks[:horizon]
    if not use or (horizon != "full" and len(use) < horizon):
        return None, []
    return sum(byw[w] for w in use) / len(use), use


def ols1(pairs: list) -> tuple[float, float] | None:
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    var = sum((p[0] - mx) ** 2 for p in pairs)
    if var == 0:
        return None
    b = sum((p[0] - mx) * (p[1] - my) for p in pairs) / var
    return my - b * mx, b


def ols2(rows: list) -> tuple[float, float, float] | None:
    """y = a + c*x1 + b*x2 via the 2x2 normal equations. rows = [(x1, x2, y), ...]."""
    n = len(rows)
    if n < 5:
        return None
    m1 = sum(r[0] for r in rows) / n
    m2 = sum(r[1] for r in rows) / n
    my = sum(r[2] for r in rows) / n
    s11 = sum((r[0] - m1) ** 2 for r in rows)
    s22 = sum((r[1] - m2) ** 2 for r in rows)
    s12 = sum((r[0] - m1) * (r[1] - m2) for r in rows)
    s1y = sum((r[0] - m1) * (r[2] - my) for r in rows)
    s2y = sum((r[1] - m2) * (r[2] - my) for r in rows)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-9:
        return None
    c = (s1y * s22 - s2y * s12) / det
    b = (s2y * s11 - s1y * s12) / det
    return my - c * m1 - b * m2, c, b


# ----------------------------------------------------------------- statistics

def phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def dm_test(e1: list, e2: list, lag: int = 3) -> tuple[float, float]:
    """Diebold-Mariano on absolute-error differentials, Newey-West corrected.
    Negative statistic = the first forecast has the smaller errors."""
    dd = [abs(a) - abs(b) for a, b in zip(e1, e2)]
    n = len(dd)
    if n < 8:
        return float("nan"), float("nan")
    m = sum(dd) / n
    var = sum((x - m) ** 2 for x in dd) / n
    for L in range(1, min(lag, n - 1) + 1):
        g = sum((dd[t] - m) * (dd[t - L] - m) for t in range(L, n)) / n
        var += 2 * (1 - L / (lag + 1)) * g
    if var <= 0:
        return float("nan"), float("nan")
    stat = m / math.sqrt(var / n)
    return stat, 2 * (1 - phi(abs(stat)))


def sign_test(wins: int, n: int) -> float:
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, k) for k in range(min(wins, n - wins) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def summarize(rows: list) -> dict:
    errs = [r["predicted"] - r["actual"] for r in rows]
    pers = [r["persistence"] - r["actual"] for r in rows]
    n = len(rows)
    wins = sum(1 for a, b in zip(errs, pers) if abs(a) < abs(b))
    stat, p = dm_test(errs, pers)
    mae = sum(abs(e) for e in errs) / n
    mae_p = sum(abs(e) for e in pers) / n
    return {
        "n": n, "period": [rows[0]["target"], rows[-1]["target"]],
        "mae": round(mae, 3), "rmse": round((sum(e * e for e in errs) / n) ** 0.5, 3),
        "bias": round(sum(errs) / n, 3),
        "mae_persistence": round(mae_p, 3),
        "rmse_persistence": round((sum(e * e for e in pers) / n) ** 0.5, 3),
        "beats_persistence_pct": round(100 * wins / n, 1),
        "mae_improvement_pct": round(100 * (1 - mae / mae_p), 1) if mae_p else None,
        "dm_stat": None if math.isnan(stat) else round(stat, 3),
        "dm_p": None if math.isnan(p) else round(p, 4),
        "sign_p": round(sign_test(wins, n), 4),
    }


# ----------------------------------------------------------------- the walk

def as_of_row(month: str, horizon, byw: dict, official: dict, months: list,
              lag_days: int) -> dict | None:
    """Everything knowable at the moment we would have predicted `month` at horizon h."""
    x, weeks_used = predictor(month, horizon, byw)
    if x is None:
        return None
    pred_on = d(max(weeks_used)) + timedelta(days=lag_days)
    known = [m for m in months if release_date(m) <= pred_on and m < month]
    if not known:
        return None
    return {"x": x, "weeks_used": weeks_used, "pred_on": pred_on,
            "last_known": max(known), "known": known,
            "persistence": official[max(known)]["food"]}


def run(weekly: dict, official: dict, lag_days: int) -> dict:
    byw = {}
    for k in weekly:
        for w in weekly[k]:
            if w not in byw:
                v = basket_yoy(weekly, w)
                if v is not None:
                    byw[w] = v

    months = sorted(m for m in official if "food" in official[m])
    results = {str(h): {s: [] for s in SPECS} for h in HORIZONS}

    for horizon in HORIZONS:
        for target in months:
            row = as_of_row(target, horizon, byw, official, months, lag_days)
            if row is None or len(row["known"]) < MIN_TRAIN:
                continue

            # Training rows are rebuilt under the SAME as-of rules — no shortcuts.
            train_full, train_h, train_pp = [], [], []
            for m in row["known"]:
                tr = as_of_row(m, horizon, byw, official, months, lag_days)
                if tr is None:
                    continue
                y = official[m]["food"]
                xf, _ = predictor(m, "full", byw)
                if xf is not None:
                    train_full.append((xf, y))
                train_h.append((tr["x"], y))
                train_pp.append((tr["persistence"], tr["x"], y))

            for spec in SPECS:
                pred = None
                coef = None
                if spec == "as_shipped" and len(train_full) >= MIN_TRAIN:
                    fit = ols1(train_full)
                    if fit:
                        pred, coef = fit[0] + fit[1] * row["x"], [round(c, 4) for c in fit]
                elif spec == "horizon_matched" and len(train_h) >= MIN_TRAIN:
                    fit = ols1(train_h)
                    if fit:
                        pred, coef = fit[0] + fit[1] * row["x"], [round(c, 4) for c in fit]
                elif spec == "persistence_plus" and len(train_pp) >= MIN_TRAIN:
                    fit = ols2(train_pp)
                    if fit:
                        a, c, b = fit
                        pred = a + c * row["persistence"] + b * row["x"]
                        coef = [round(a, 4), round(c, 4), round(b, 4)]
                if pred is None:
                    continue
                results[str(horizon)][spec].append({
                    "target": target, "predicted": round(pred, 3),
                    "actual": official[target]["food"],
                    "persistence": row["persistence"],
                    "predictor": round(row["x"], 3),
                    "weeks_used": row["weeks_used"],
                    "predicted_on": row["pred_on"].isoformat(),
                    "last_known_print": row["last_known"],
                    "train_n": len(train_pp), "coef": coef,
                })
    return results


def report_table(results: dict, label: str, store: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"{'horizon':<8} {'spec':<18} {'n':>3} {'MAE':>6} {'persist':>8} "
          f"{'improve':>9} {'win%':>6} {'DM p':>8} {'sign p':>7}")
    print("-" * 80)
    for h in HORIZONS:
        for spec in SPECS:
            rows = results[str(h)][spec]
            if len(rows) < 8:
                continue
            s = summarize(rows)
            store[f"{label}|h{h}|{spec}"] = s
            imp = s["mae_improvement_pct"]
            print(f"{str(h):<8} {spec:<18} {s['n']:>3} {s['mae']:>6.2f} "
                  f"{s['mae_persistence']:>8.2f} {imp:>8.1f}% {s['beats_persistence_pct']:>5.0f}% "
                  f"{str(s['dm_p']):>8} {s['sign_p']:>7.3f}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    allow = "--no-fetch" not in sys.argv
    lag = PUB_LAG_DAYS
    if "--lag-days" in sys.argv:
        lag = int(sys.argv[sys.argv.index("--lag-days") + 1])

    weekly = cached(W_CACHE, fetch_weekly, allow)
    official_all = cached(O_CACHE, fetch_official, allow)
    wk = sorted({w for k in weekly for w in weekly[k]})
    print(f"backtest: {len(wk)} survey weeks {wk[0]}..{wk[-1]} · publication lag {lag}d · "
          f"min train {MIN_TRAIN}mo")

    summary: dict = {}
    all_res: dict = {}
    for base, name in BASES.items():
        off = official_all.get(base) or {}
        mo = sorted(m for m in off if "food" in off[m])
        if len(mo) < MIN_TRAIN + 8:
            print(f"\n=== base {name}: only {len(mo)} prints — skipped ===")
            continue
        print(f"\nbase {name}: {len(mo)} official food prints {mo[0]}..{mo[-1]}"
              f"{'  [PRIMARY]' if base == PRIMARY_BASE else '  [robustness check]'}")
        res = run(weekly, off, lag)
        all_res[name] = res
        report_table(res, name, summary)

    OUT.write_text(json.dumps({
        "generated": date.today().isoformat(), "publication_lag_days": lag,
        "min_train_months": MIN_TRAIN, "release_day": RELEASE_DAY,
        "primary_base": BASES[PRIMARY_BASE], "summary": summary, "predictions": all_res,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nbacktest: wrote {OUT.name} — every prediction auditable")


if __name__ == "__main__":
    main()
