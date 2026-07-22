"""audit_band.py — is the shipped +/-3.8pp band calibrated? Is the live call an extrapolation?"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import backtest as B

BASE = Path(__file__).parent
for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8")
    except Exception: pass

res = json.loads((BASE / "backtest_results.json").read_text(encoding="utf-8"))

print("=" * 78)
print("A — empirical coverage of the shipped +/-3.8pp band, out of sample")
print("=" * 78)
for base in res["predictions"]:
    for h in ("1", "2", "3", "full"):
        for spec in ("as_shipped", "persistence_plus"):
            rows = res["predictions"][base][h][spec]
            if len(rows) < 8:
                continue
            errs = [abs(r["predicted"] - r["actual"]) for r in rows]
            cov = 100 * sum(1 for e in errs if e <= 3.8) / len(errs)
            perr = [abs(r["persistence"] - r["actual"]) for r in rows]
            pcov = 100 * sum(1 for e in perr if e <= 3.8) / len(perr)
            # band that WOULD have delivered 95% coverage
            need = sorted(errs)[int(0.95 * (len(errs) - 1))]
            print(f"{base:<9} h={h:<5} {spec:<17} n={len(rows):>3}  cov(+/-3.8)={cov:5.1f}%  "
                  f"band for 95% = +/-{need:4.1f}pp   (persistence cov {pcov:5.1f}%)")
    print()

print("=" * 78)
print("B — is the live call (meat basket 63.3%) inside the calibration range?")
print("=" * 78)
weekly = json.loads((BASE / "backtest_weekly_cache.json").read_text(encoding="utf-8"))
byw = {}
for k in weekly:
    for w in weekly[k]:
        if w not in byw:
            v = B.basket_yoy(weekly, w)
            if v is not None:
                byw[w] = v
off2 = json.loads((BASE / "backtest_official_cache.json").read_text(encoding="utf-8"))["2"]
pairs = []
for m in sorted(off2):
    if "food" not in off2[m]:
        continue
    x, wk = B.predictor(m, "full", byw)
    if x is not None and len(wk) >= 3:
        pairs.append((m, x, off2[m]["food"]))
xs = [p[1] for p in pairs]
print(f"calibration months: {len(pairs)}  {pairs[0][0]}..{pairs[-1][0]}")
print(f"meat-basket YoY range in calibration: {min(xs):.1f}% .. {max(xs):.1f}%")
jul = [w for w in sorted(byw) if w[:7] == "2026-07"]
livex = sum(byw[w] for w in jul) / len(jul)
print(f"LIVE July predictor: weeks {jul} -> basket YoY {livex:.1f}%")
print(f"  -> {'INSIDE' if min(xs) <= livex <= max(xs) else 'OUTSIDE'} the fitted range; "
      f"{livex - max(xs):+.1f}pp beyond the max fitted x")
f = B.ols1([(p[1], p[2]) for p in pairs])
resid = [p[2] - (f[0] + f[1] * p[1]) for p in pairs]
rmse = (sum(r * r for r in resid) / len(resid)) ** 0.5
print(f"  in-sample fit: food = {f[0]:+.2f} + {f[1]:.3f}*meat, in-sample RMSE {rmse:.2f}pp "
      f"-> shipped band 2*RMSE = {max(1.0, round(2*rmse,1)):.1f}pp")
print(f"  in-sample RMSE {rmse:.2f}pp vs out-of-sample MAE ~2.4-4.9pp (backtest) "
      f"-> band understates true error by {4.9/(2*rmse):.1f}x at the primary sample")

print()
print("=" * 78)
print("C — which target months got dropped from the h=1 sample?")
print("=" * 78)
off1 = json.loads((BASE / "backtest_official_cache.json").read_text(encoding="utf-8"))["1"]
got = {h: {r["target"] for r in res["predictions"]["2020=100"][h]["as_shipped"]}
       for h in ("1", "2", "3", "full")}
allm = got["full"] | got["1"] | got["2"] | got["3"]
for m in sorted(allm):
    missing = [h for h in ("1", "2", "3", "full") if m not in got[h]]
    if missing:
        print(f"  {m} missing at horizons {missing}  (actual food YoY {off1[m]['food']})")
print(f"  sample spans {min(allm)}..{max(allm)}")
