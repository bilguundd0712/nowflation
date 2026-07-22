"""audit_leak.py — adversarial audit of backtest.py: hunt residual lookahead that FLATTERS
the nowcast, then rerun under stricter, more realistic rules."""
import json, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backtest as B

BASE = Path(__file__).parent
weekly = json.loads((BASE / "backtest_weekly_cache.json").read_text(encoding="utf-8"))
official_all = json.loads((BASE / "backtest_official_cache.json").read_text(encoding="utf-8"))

for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8")
    except Exception: pass


def build_byw():
    byw = {}
    for k in weekly:
        for w in weekly[k]:
            if w not in byw:
                v = B.basket_yoy(weekly, w)
                if v is not None:
                    byw[w] = v
    return byw


BYW = build_byw()

# ---------------------------------------------------------------- CHECK 1: leak audit
print("=" * 78)
print("CHECK 1 — per-row lookahead audit of the shipped backtest (base 2020=100)")
print("=" * 78)
res = json.loads((BASE / "backtest_results.json").read_text(encoding="utf-8"))["predictions"]
off = official_all["1"]
months = sorted(m for m in off if "food" in off[m])

viol = {"target_after_predon": 0, "train_month_ge_target": 0, "train_release_after_predon": 0,
        "train_full_week_after_predon": 0, "target_week_after_predon": 0,
        "persistence_not_latest_released": 0}
n_rows = 0
worst_full = []
for h, specs in res["2020=100"].items():
    for spec, rows in specs.items():
        for r in rows:
            n_rows += 1
            pred_on = B.d(r["predicted_on"])
            tgt = r["target"]
            # target print must NOT be released at prediction time
            if B.release_date(tgt) <= pred_on:
                viol["target_after_predon"] += 1
            # weeks used must be observable
            for w in r["weeks_used"]:
                if B.d(w) + timedelta(days=7) > pred_on:
                    viol["target_week_after_predon"] += 1
            # persistence must equal the latest print released by pred_on
            known = [m for m in months if B.release_date(m) <= pred_on and m < tgt]
            if not known or off[max(known)]["food"] != r["persistence"]:
                viol["persistence_not_latest_released"] += 1
            # rebuild the training set exactly as run() does and test every row
            for m in known:
                if m >= tgt:
                    viol["train_month_ge_target"] += 1
                if B.release_date(m) > pred_on:
                    viol["train_release_after_predon"] += 1
                if spec == "as_shipped":
                    xw = [w for w in sorted(BYW) if w[:7] == m]
                    if xw:
                        last_obs = B.d(max(xw)) + timedelta(days=7)
                        if last_obs > pred_on:
                            viol["train_full_week_after_predon"] += 1
                            worst_full.append((tgt, h, m, max(xw), (last_obs - pred_on).days))
print(f"rows audited: {n_rows}")
for k, v in viol.items():
    print(f"  {k:38s} {v}")
if worst_full:
    worst_full.sort(key=lambda t: -t[4])
    print(f"  worst train_full overreach: {worst_full[0]} (days past pred_on)")

# ---------------------------------------------------------------- CHECK 2: stricter reruns
print()
print("=" * 78)
print("CHECK 2 — stricter reruns (primary base 2020=100, MAE vs persistence MAE)")
print("=" * 78)


def summarize_run(off, lag, min_train, strict_train_obs=False, release_day=10):
    """Re-implementation with an optional STRICT rule: a training month's predictor may only
    use survey weeks observable (week + lag) at the prediction date."""
    old_rd, old_mt = B.RELEASE_DAY, B.MIN_TRAIN
    B.RELEASE_DAY, B.MIN_TRAIN = release_day, min_train
    months = sorted(m for m in off if "food" in off[m])
    out = {}
    for horizon in B.HORIZONS:
        acc = {s: [] for s in B.SPECS}
        for target in months:
            row = B.as_of_row(target, horizon, BYW, off, months, lag)
            if row is None or len(row["known"]) < min_train:
                continue
            pred_on = row["pred_on"]
            tf, th, tp = [], [], []
            for m in row["known"]:
                tr = B.as_of_row(m, horizon, BYW, off, months, lag)
                if tr is None:
                    continue
                y = off[m]["food"]
                mw = [w for w in sorted(BYW) if w[:7] == m]
                if strict_train_obs:
                    mw = [w for w in mw if B.d(w) + timedelta(days=lag) <= pred_on]
                if mw:
                    tf.append((sum(BYW[w] for w in mw) / len(mw), y))
                th.append((tr["x"], y))
                tp.append((tr["persistence"], tr["x"], y))
            for spec in B.SPECS:
                pred = None
                if spec == "as_shipped" and len(tf) >= min_train:
                    f = B.ols1(tf)
                    if f: pred = f[0] + f[1] * row["x"]
                elif spec == "horizon_matched" and len(th) >= min_train:
                    f = B.ols1(th)
                    if f: pred = f[0] + f[1] * row["x"]
                elif spec == "persistence_plus" and len(tp) >= min_train:
                    f = B.ols2(tp)
                    if f: pred = f[0] + f[1] * row["persistence"] + f[2] * row["x"]
                if pred is None:
                    continue
                acc[spec].append({"target": target, "predicted": pred,
                                  "actual": off[target]["food"],
                                  "persistence": row["persistence"]})
        out[horizon] = {s: (B.summarize(v) if len(v) >= 8 else None) for s, v in acc.items()}
    B.RELEASE_DAY, B.MIN_TRAIN = old_rd, old_mt
    return out


scenarios = [
    ("shipped        lag=7  min=12", dict(lag=7,  min_train=12)),
    ("lag=14         min=12", dict(lag=14, min_train=12)),
    ("lag=21         min=12", dict(lag=21, min_train=12)),
    ("lag=7          min=24", dict(lag=7,  min_train=24)),
    ("lag=14         min=24", dict(lag=14, min_train=24)),
    ("release_day=5  lag=7  min=12", dict(lag=7, min_train=12, release_day=5)),
    ("STRICT trainobs lag=7 min=12", dict(lag=7,  min_train=12, strict_train_obs=True)),
    ("STRICT trainobs lag=21 min=24", dict(lag=21, min_train=24, strict_train_obs=True)),
]
hdr = f"{'scenario':<30}"
print(f"{hdr}{'h':>5} {'spec':<18} {'n':>3} {'MAE':>6} {'pers':>6} {'impr%':>7} {'win%':>5} {'DMp':>7}")
print("-" * 96)
for name, kw in scenarios:
    r = summarize_run(official_all["1"], **kw)
    for h in B.HORIZONS:
        for spec in B.SPECS:
            s = r[h][spec]
            if not s:
                continue
            print(f"{name:<30}{str(h):>5} {spec:<18} {s['n']:>3} {s['mae']:>6.2f} "
                  f"{s['mae_persistence']:>6.2f} {str(s['mae_improvement_pct']):>7} "
                  f"{s['beats_persistence_pct']:>4.0f}% {str(s['dm_p']):>7}")
    print()
