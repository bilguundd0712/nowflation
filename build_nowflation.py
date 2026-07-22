r"""
build_nowflation.py — renders nowflation.mn from the live NSO weekly price survey.

The public face of the `ub_meat_nowcast_yoy` signal: a single static page showing the UB
weekly food nowcast that leads each monthly CPI print by ~3-4 weeks. Pulls the same PXWeb
table as mn_nowcast.py (DT_NSO_0600_001V4, data.1212.mn), recomputes YoY / 4-week momentum
with the SAME method as the board, and emits a self-contained index.html.

Design system: "Editorial Brutalism" (Stitch, sovereign_ledger/DESIGN.md) — Inter for numbers,
Source Serif 4 italic for the verdict, JetBrains Mono for data and provenance, 0 radius
everywhere, 1px borders as spacers, no gradients or shadows.

Source of truth is NSO, NOT the board: the page is correct even when mn_nowcast.py has not
run since the last NSO publish. stele_thresholds.json is read for the official CPI anchors
only (cpi_yoy, food_cpi_yoy) and is optional — the page degrades honestly without it.

Safety contract (same shape as the feeds): bounds-gated per item; ANY fetch failure falls back
to the on-disk series cache and the page STAMPS ITSELF STALE rather than showing a fresh face
on old data. No number on the page is authored by hand — every figure traces to the series.

THE SCORED CALLS LEDGER (calls_ledger.json, beside this file): each build may append a dated,
banded prediction of the NEXT official food-CPI YoY print (national food group, table
DT_NSO_0600_010V1, base 2023=100) and grades every open call automatically once the official
month appears in that same table. The ledger is APPEND-ONLY: a call is never edited after it
is made; grading only fills in the official figure, the error and the verdict. This is the
page's one non-rebuildable asset — a public track record.

Run: python build_nowflation.py   [--no-fetch]   (writes index.html beside this file)
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).parent.resolve()
CACHE = BASE / "series_cache.json"
OFFICIAL_CACHE = BASE / "official_cpi_cache.json"
LEDGER = BASE / "calls_ledger.json"
OUT = BASE / "index.html"
BOARD = BASE.parent / "stele_thresholds.json"
# Public-fact snapshot for CI: next print date + the CPI report's beef average. The private
# STELE board never leaves the owner's machine; this file ships in the repo instead.
# Regenerate with --emit-anchors on a machine that has the board.
ANCHORS = BASE / "anchors.json"

URL = ("https://data.1212.mn/api/v1/mn/NSO/" + urllib.parse.quote("Economy, environment")
       + "/" + urllib.parse.quote("Consumer Price Index") + "/DT_NSO_0600_001V4.px")
# Official monthly CPI change by group, YoY. Base '2' = 2023=100 reproduces the official
# print exactly (verified 2026-07-22: 2026-06 headline 12.0 / food 24.8). Group '0' =
# headline, '1' = food & non-alcoholic beverages — the series the ledger predicts and
# grades against.
OFFICIAL_URL = ("https://data.1212.mn/api/v1/mn/NSO/" + urllib.parse.quote("Economy, environment")
                + "/" + urllib.parse.quote("Consumer Price Index") + "/DT_NSO_0600_010V1.px")
OFFICIAL_BASE, OFFICIAL_GROUPS = "2", {"0": "headline", "1": "food", "7": "transport"}
CHART_WEEKS = 56  # display window of the price chart; the fetch reaches further for calibration

ITEMS = {"3": "flour_I", "9": "mutton", "10": "beef_bone_in", "11": "beef_boneless",
         "13": "goat", "29": "a80", "30": "a92", "31": "diesel"}
MEAT = ["beef_bone_in", "beef_boneless", "mutton", "goat"]
FUEL = ["a92", "diesel", "a80"]
PRICE_BOUNDS = {"flour_I": (1000, 8000), "mutton": (8000, 90000), "beef_bone_in": (8000, 90000),
                "beef_boneless": (10000, 110000), "goat": (5000, 80000),
                "a80": (1200, 6000), "a92": (1500, 6000), "diesel": (1500, 8000)}
# A survey-to-survey move at or above this threshold gets called out in the fuel section —
# administered fuel prices move in steps, and the step IS the news.
FUEL_STEP_CALLOUT_PCT = 3.0

# The board's own gates for ub_meat_nowcast_yoy — reused so the page and the board never disagree.
AMBER_ABOVE, RED_ABOVE = 25.0, 40.0

# Display order and bilingual labels. Food table (per kg) and fuel section (per litre).
DISPLAY = [
    ("beef_boneless", "Beef, boneless", "Үхрийн мах, ясгүй", "KG"),
    ("beef_bone_in", "Beef, bone-in", "Үхрийн мах, ястай", "KG"),
    ("mutton", "Mutton", "Хонины мах", "KG"),
    ("goat", "Goat", "Ямааны мах", "KG"),
    ("flour_I", "Flour, grade I", "Гурил, I зэрэг", "KG"),
]
FUEL_DISPLAY = [
    ("a92", "Petrol A-92", "АИ-92 автобензин", "L"),
    ("diesel", "Diesel", "Дизелийн түлш", "L"),
    ("a80", "Petrol A-80", "АИ-80 автобензин", "L"),
]

C = {  # Sovereign Ledger palette
    "bg": "#0A0A0A", "surface": "#141414", "surface_hi": "#1c1b1b",
    "text": "#F5F5F5", "dim": "#888888", "line": "#262626",
    "teal": "#4fe8e2", "teal_deep": "#1fccc6", "red": "#ffb3ad", "red_bg": "#a40217",
    "amber": "#ffc8a1",
}


# ---------------------------------------------------------------- data

def fetch_series(weeks: int = 185) -> dict:
    meta = requests.get(URL, timeout=40).json()
    tv = next(v for v in meta["variables"] if v["code"] == "Хугацаа")
    idx = tv["values"][:weeks]
    dates = dict(zip(tv["values"], tv["valueTexts"]))
    body = {"query": [
        {"code": "Бүтээгдэхүүн", "selection": {"filter": "item", "values": list(ITEMS)}},
        {"code": "Хугацаа", "selection": {"filter": "item", "values": idx}},
    ], "response": {"format": "json"}}
    r = requests.post(URL, json=body, timeout=60)
    r.raise_for_status()
    series: dict = {}
    for row in r.json()["data"]:
        name = ITEMS.get(row["key"][0])
        try:
            val = float(row["values"][0])
        except (TypeError, ValueError):
            continue
        if name:
            series.setdefault(name, {})[dates.get(row["key"][1], row["key"][1])] = val
    return series


def load_series(allow_fetch: bool = True) -> tuple[dict, bool]:
    """Returns (series, live). live=False means we are serving the cache and must say so."""
    if allow_fetch:
        try:
            s = fetch_series()
            if s.get("beef_boneless"):
                CACHE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
                return s, True
            print("build: fetch returned no beef series — falling back to cache", file=sys.stderr)
        except Exception as e:  # network, PXWeb shape change, timeout
            print(f"build: fetch failed ({e}) — falling back to cache", file=sys.stderr)
    if not CACHE.exists():
        sys.exit("build: no live data and no cache — refusing to render a page with no source")
    return json.loads(CACHE.read_text(encoding="utf-8")), False


def fetch_official(months: int = 42) -> dict:
    """Official monthly CPI YoY by group: {'2026-06': {'headline': 12.0, 'food': 24.8}, ...}."""
    meta = requests.get(OFFICIAL_URL, timeout=40).json()
    tv = next(v for v in meta["variables"] if v["code"] == "Сар")
    idx = tv["values"][:months]
    labels = dict(zip(tv["values"], tv["valueTexts"]))
    body = {"query": [
        {"code": "Суурь он", "selection": {"filter": "item", "values": [OFFICIAL_BASE]}},
        {"code": "Бүлэг", "selection": {"filter": "item", "values": list(OFFICIAL_GROUPS)}},
        {"code": "Сар", "selection": {"filter": "item", "values": idx}},
    ], "response": {"format": "json"}}
    r = requests.post(OFFICIAL_URL, json=body, timeout=60)
    r.raise_for_status()
    out: dict = {}
    for row in r.json()["data"]:
        grp = OFFICIAL_GROUPS.get(row["key"][1])
        mon = labels.get(row["key"][2], row["key"][2])
        try:
            val = float(row["values"][0])
        except (TypeError, ValueError):  # NSO uses '..' for unavailable cells
            continue
        if grp:
            out.setdefault(mon, {})[grp] = val
    return out


def load_official(allow_fetch: bool = True) -> tuple[dict, bool]:
    """Same live/cache contract as the weekly series; empty dict if neither exists."""
    if allow_fetch:
        try:
            o = fetch_official()
            if o and all(k in o[max(o)] for k in ("headline", "food")):
                OFFICIAL_CACHE.write_text(json.dumps(o, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
                return o, True
            print("build: official CPI fetch incomplete — falling back to cache", file=sys.stderr)
        except Exception as e:
            print(f"build: official CPI fetch failed ({e}) — falling back to cache",
                  file=sys.stderr)
    if OFFICIAL_CACHE.exists():
        return json.loads(OFFICIAL_CACHE.read_text(encoding="utf-8")), False
    return {}, False


def yoy_of(s: dict, at: str) -> float | None:
    """Same method as mn_nowcast.py: nearest observed week on/before the same date last year."""
    try:
        target = (datetime.strptime(at, "%Y-%m-%d").date().replace(
            year=datetime.strptime(at, "%Y-%m-%d").year - 1)).isoformat()
    except ValueError:  # 29 Feb has no counterpart in a non-leap year
        target = f"{int(at[:4]) - 1}-02-28"
    base = next((d for d in reversed(sorted(s)) if d <= target), None)
    if base is None or s.get(base) in (None, 0):
        return None
    return round(100 * (s[at] / s[base] - 1), 1)


def m4_of(s: dict, at: str) -> float | None:
    ds = sorted(s)
    i = ds.index(at)
    if i < 4:
        return None
    return round(100 * (s[at] / s[ds[i - 4]] - 1), 1)


def momentum_track(series: dict, n: int = 26) -> list[tuple[str, float]]:
    """Rolling 4-week momentum of the meat composite, week by week — the decay curve.

    Per-item 4wk % change averaged across the meat basket, exactly as the board averages it.
    This is the series that shows the impulse fading while the YoY level stays high.
    """
    weeks = sorted(series["beef_boneless"])
    out = []
    for w in weeks[-n:]:
        vals = [m4_of(series[k], w) for k in MEAT if w in series.get(k, {})]
        vals = [v for v in vals if v is not None]
        if vals:
            out.append((w, round(sum(vals) / len(vals), 1)))
    return out


def basket_week_yoy(series: dict, w: str) -> float | None:
    vals = [yoy_of(series[k], w) for k in MEAT if w in series.get(k, {})]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def basket_month_stats(series: dict, month: str) -> tuple[float | None, int]:
    """Mean weekly basket YoY across the surveyed weeks inside a calendar month."""
    ws = [w for w in sorted(series["beef_boneless"]) if w[:7] == month]
    vals = [basket_week_yoy(series, w) for w in ws]
    vals = [v for v in vals if v is not None]
    return (round(sum(vals) / len(vals), 2) if vals else None), len(vals)


def month_next(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y + mo // 12}-{mo % 12 + 1:02d}"


def month_end(m: str) -> str:
    nxt = month_next(m)
    return (date(int(nxt[:4]), int(nxt[5:7]), 1) - timedelta(days=1)).isoformat()


def calibrate(series: dict, official: dict) -> dict | None:
    """OLS of official food YoY on our monthly-mean meat-basket YoY, over every month where
    both exist. Returns None below 6 pairs — no call is made on thin calibration."""
    pairs = []
    for mon in sorted(official):
        if "food" not in official[mon]:
            continue
        x, n = basket_month_stats(series, mon)
        if x is not None and n >= 3:
            pairs.append((x, official[mon]["food"]))
    if len(pairs) < 6:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    n = len(pairs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in pairs) / var
    a = my - b * mx
    res = [y - (a + b * x) for x, y in pairs]
    rmse = (sum(r * r for r in res) / n) ** 0.5
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(r * r for r in res) / ss_tot if ss_tot else 0.0
    return {"a": a, "b": b, "rmse": rmse, "r2": r2, "n": n,
            "from": min(m for m in official if "food" in official[m]
                        and basket_month_stats(series, m)[0] is not None)}


def update_ledger(series: dict, official: dict, cal: dict | None,
                  next_print: str | None, today: date) -> list:
    """Grade open calls against the official table, then append at most one new call.

    APPEND-ONLY CONTRACT: an existing call's predicted/band/made_on are never touched.
    Grading fills official_value/error/hit/graded_on on open calls whose target month has
    an official figure. A new call is appended only when the target month has at least one
    more surveyed week than the latest existing call for that target — one call per vintage,
    every vintage kept and graded.
    """
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []

    for call in ledger:
        tm = call["target_month"]
        if call["status"] == "open" and tm in official and "food" in official[tm]:
            call["official_value"] = official[tm]["food"]
            call["error"] = round(official[tm]["food"] - call["predicted"], 1)
            call["hit"] = abs(call["error"]) <= call["band"]
            call["graded_on"] = today.isoformat()
            call["status"] = "graded"

    if official and cal:
        latest = max(m for m in official if "food" in official[m])
        target = month_next(latest)
        wobs = [w for w in sorted(series["beef_boneless"]) if w[:7] == target]
        ours, n_ours = basket_month_stats(series, target)
        prior = [c for c in ledger if c["target_month"] == target]
        max_seen = max((len(c["weeks_observed"]) for c in prior), default=0)
        if ours is not None and n_ours > max_seen:
            band = max(1.0, round(2 * cal["rmse"], 1))
            ledger.append({
                "id": f"{target}#w{n_ours}",
                "made_on": today.isoformat(),
                "target_month": target,
                "metric": ("official food CPI YoY, national (Хүнсний бараа, ундаа, ус), "
                           "NSO DT_NSO_0600_010V1, base 2023=100"),
                "expected_release": next_print,
                "weeks_observed": wobs,
                "our_basket_yoy_partial": ours,
                "predicted": round(cal["a"] + cal["b"] * ours, 1),
                "band": band,
                "method": (f"OLS food-YoY ~ meat-basket-YoY over {cal['n']} months since "
                           f"{cal['from']} (R²={cal['r2']:.2f}, RMSE={cal['rmse']:.1f}pp); "
                           f"band = max(1, 2×RMSE); made with {n_ours} survey week"
                           f"{'' if n_ours == 1 else 's'} of the target month observed"),
                "status": "open",
            })

    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    return ledger


def severity(y: float | None) -> tuple[str, str]:
    if y is None:
        return "n/a", "dim"
    if y > RED_ABOVE:
        return "SEVERE", "red"
    if y > AMBER_ABOVE:
        return "ELEVATED", "amber"
    return "CONTAINED", "teal"


def read_board() -> dict:
    """Official CPI anchors. Optional — the page states plainly when they are unavailable.

    Sources, in priority order: the local STELE board (owner's machine), then anchors.json
    (the committed public-fact snapshot, so CI renders the same validation line without the
    private board)."""
    out = {"cpi": None, "food_cpi": None, "cpi_asof": None, "next_print": None,
           "board_value": None, "board_week": None, "beef_anchor": None,
           "beef_anchor_month": None}
    b = None
    if BOARD.exists():
        try:
            b = json.loads(BOARD.read_text(encoding="utf-8"))
        except Exception:
            b = None
    for t in (b or {}).get("thresholds", []):
        k, v, lu = t.get("key"), t.get("current_value"), t.get("last_updated") or ""
        if k == "cpi_yoy":
            out["cpi"] = v
            out["cpi_asof"] = lu.split(" ")[0] or None
            nxt = re.search(r"next\s+([A-Za-z]{3})[-\s](\d{1,2})", lu)
            if nxt and out["cpi_asof"]:
                try:
                    mon = datetime.strptime(nxt.group(1), "%b").month
                    yr = int(out["cpi_asof"][:4]) + (1 if mon < int(out["cpi_asof"][5:7]) else 0)
                    out["next_print"] = date(yr, mon, int(nxt.group(2))).isoformat()
                except ValueError:
                    pass
        elif k == "food_cpi_yoy":
            out["food_cpi"] = v
            # The CPI report's own UB beef average, as recorded on the board — the one
            # external constant the validation line may cite, attributed to the report.
            hit = re.search(r"beef\s+([\d,]{4,9})\s*MNT/kg",
                            json.dumps(t, ensure_ascii=False), re.I)
            if hit:
                out["beef_anchor"] = float(hit.group(1).replace(",", ""))
        elif k == "ub_meat_nowcast_yoy":
            out["board_value"] = v
            out["board_week"] = lu.split(" ")[0] or None
    if out["beef_anchor"] is not None and out["cpi_asof"]:
        out["beef_anchor_month"] = out["cpi_asof"][:7]
    if ANCHORS.exists():
        try:
            a = json.loads(ANCHORS.read_text(encoding="utf-8"))
            out["next_print"] = out["next_print"] or a.get("next_print")
            if out["beef_anchor"] is None and isinstance(a.get("cpi_report"), dict):
                out["beef_anchor"] = a["cpi_report"].get("ub_beef_avg_mnt")
                out["beef_anchor_month"] = a["cpi_report"].get("month")
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- svg

def fmt(n: float, dp: int = 0) -> str:
    return f"{n:,.{dp}f}"


def pct(v: float | None, dp: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.{dp}f}%"


def _pts(vals, x0, y0, w, h, lo, hi):
    span = (hi - lo) or 1
    n = len(vals) - 1 or 1
    return [(x0 + w * i / n, y0 + h - h * (v - lo) / span) for i, v in enumerate(vals)]


def price_chart(series: dict, key: str, cpi_asof: str | None, w=1180, h=340,
                window: int = CHART_WEEKS) -> str:
    """Beef weekly line with the 'lead zone' — the weeks the official CPI has not yet seen.

    The fetch reaches back years for ledger calibration; the chart shows only the last
    `window` weeks so the recent story stays readable."""
    s_full = series[key]
    ds = sorted(s_full)[-window:]
    s = {d: s_full[d] for d in ds}
    vals = [s[d] for d in ds]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.18 or 1
    lo, hi = lo - pad, hi + pad
    ml, mr, mt, mb = 62, 92, 18, 34
    pw, ph = w - ml - mr, h - mt - mb
    pts = _pts(vals, ml, mt, pw, ph, lo, hi)
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             f'aria-label="Weekly UB price, {key}, {ds[0]} to {ds[-1]}">']

    # y grid + labels at 4 steps
    for i in range(5):
        y = mt + ph * i / 4
        v = hi - (hi - lo) * i / 4
        parts.append(f'<line x1="{ml}" x2="{ml+pw}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="{C["line"]}" stroke-dasharray="2,3"/>')
        parts.append(f'<text x="{ml-10}" y="{y+4:.1f}" class="ax" text-anchor="end">'
                     f'₮{fmt(v/1000,1)}K</text>')

    # lead zone: everything after the last date the official CPI covers
    if cpi_asof:
        ahead = [i for i, d in enumerate(ds) if d > cpi_asof]
        if ahead:
            x_start = pts[ahead[0] - 1][0] if ahead[0] else ml
            parts.append(f'<rect x="{x_start:.1f}" y="{mt}" width="{ml+pw-x_start:.1f}" '
                         f'height="{ph}" fill="{C["teal"]}" opacity="0.05"/>')
            parts.append(f'<line x1="{x_start:.1f}" x2="{x_start:.1f}" y1="{mt}" y2="{mt+ph}" '
                         f'stroke="{C["teal"]}" stroke-dasharray="3,3" opacity="0.5"/>')
            # The band is usually only a week or two wide, so the label cannot live inside it.
            # Park it in the empty lower-left and point into the band.
            label = f"{len(ahead)} WEEK{'' if len(ahead) == 1 else 'S'} AHEAD OF THE CPI ▶"
            zone_w = ml + pw - x_start
            if zone_w > len(label) * 6.2:  # comfortably fits inside the band
                parts.append(f'<text x="{x_start + zone_w/2:.1f}" y="{mt+ph-12}" '
                             f'class="ax lead" text-anchor="middle">{label}</text>')
            else:
                parts.append(f'<text x="{x_start-8:.1f}" y="{mt+ph-12}" class="ax lead" '
                             f'text-anchor="end">{label}</text>')

    parts.append(f'<polyline points="{poly}" fill="none" stroke="{C["teal"]}" stroke-width="2"/>')

    # last point + price tag
    lx, ly = pts[-1]
    parts.append(f'<rect x="{lx-2:.1f}" y="{ly-2:.1f}" width="5" height="5" fill="{C["teal"]}"/>')
    parts.append(f'<rect x="{lx+8:.1f}" y="{ly-13:.1f}" width="80" height="24" fill="{C["teal"]}"/>')
    parts.append(f'<text x="{lx+48:.1f}" y="{ly+3:.1f}" class="tag" text-anchor="middle">'
                 f'₮{fmt(s[ds[-1]])}</text>')

    # x labels — first, quarter points, last
    for i in (0, len(ds) // 4, len(ds) // 2, 3 * len(ds) // 4, len(ds) - 1):
        x = pts[i][0]
        parts.append(f'<text x="{x:.1f}" y="{h-12}" class="ax" text-anchor="middle">'
                     f'{ds[i][2:7].replace("-", "·")}</text>')
    parts.append("</svg>")
    return "".join(parts)


def momentum_chart(track: list[tuple[str, float]], w=560, h=150) -> str:
    """The decay curve: 4-week momentum week by week, with zero marked. The whole argument."""
    if not track:
        return ""
    vals = [v for _, v in track]
    lo, hi = min(vals + [0]), max(vals + [0])
    pad = (hi - lo) * 0.2 or 1
    lo, hi = lo - pad, hi + pad
    ml, mr, mt, mb = 40, 14, 14, 22
    pw, ph = w - ml - mr, h - mt - mb
    pts = _pts(vals, ml, mt, pw, ph, lo, hi)
    zy = mt + ph - ph * (0 - lo) / ((hi - lo) or 1)

    p = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
         f'aria-label="4-week momentum of the meat basket, last {len(track)} weeks">']
    p.append(f'<line x1="{ml}" x2="{ml+pw}" y1="{zy:.1f}" y2="{zy:.1f}" stroke="{C["dim"]}" '
             f'stroke-dasharray="2,3" opacity="0.7"/>')
    p.append(f'<text x="{ml-8}" y="{zy+4:.1f}" class="ax" text-anchor="end">0%</text>')
    p.append(f'<text x="{ml-8}" y="{mt+8}" class="ax" text-anchor="end">{hi:.0f}%</text>')
    # bars, so a fading impulse reads as shrinking mass rather than a wiggling line
    bw = max(2.0, pw / len(pts) * 0.55)
    for (x, y), (_, v) in zip(pts, track):
        col = C["teal"] if v <= 0 else (C["red"] if v > 3 else C["teal_deep"])
        top, hh = (min(y, zy), abs(zy - y))
        p.append(f'<rect x="{x-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                 f'height="{max(hh,1):.1f}" fill="{col}" opacity="0.85"/>')
    p.append(f'<text x="{ml}" y="{h-6}" class="ax">{track[0][0][2:7]}</text>')
    p.append(f'<text x="{ml+pw}" y="{h-6}" class="ax" text-anchor="end">{track[-1][0][2:7]}</text>')
    p.append("</svg>")
    return "".join(p)


def last_step(s: dict) -> dict | None:
    """Change between the two most recent surveys of one item — administered fuel prices
    move in steps, so the step itself is reported, with its true span in days."""
    ds = sorted(s)
    if len(ds) < 2:
        return None
    prev, last = ds[-2], ds[-1]
    days = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(prev, "%Y-%m-%d")).days
    return {"pct": round(100 * (s[last] / s[prev] - 1), 1), "days": days,
            "prev_d": prev, "last_d": last, "prev_p": s[prev], "last_p": s[last]}


def fuel_chart(series: dict, keys=("a92", "diesel"), w=1180, h=300,
               window: int = CHART_WEEKS) -> str:
    """A-92 and diesel on one panel, shared scale, tagged line ends."""
    colors = {"a92": C["teal"], "diesel": C["amber"]}
    common = sorted(set(series[keys[0]]) & set(series[keys[1]]))[-window:]
    if len(common) < 2:
        return ""
    allv = [series[k][d] for k in keys for d in common]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.18 or 1
    lo, hi = lo - pad, hi + pad
    ml, mr, mt, mb = 62, 110, 18, 34
    pw, ph = w - ml - mr, h - mt - mb
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             f'aria-label="Weekly UB fuel prices, {common[0]} to {common[-1]}">']
    for i in range(5):
        y = mt + ph * i / 4
        v = hi - (hi - lo) * i / 4
        parts.append(f'<line x1="{ml}" x2="{ml+pw}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="{C["line"]}" stroke-dasharray="2,3"/>')
        parts.append(f'<text x="{ml-10}" y="{y+4:.1f}" class="ax" text-anchor="end">'
                     f'₮{fmt(v)}</text>')
    for key in keys:
        vals = [series[key][d] for d in common]
        pts = _pts(vals, ml, mt, pw, ph, lo, hi)
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{colors[key]}" '
                     f'stroke-width="2"/>')
        lx, ly = pts[-1]
        parts.append(f'<text x="{lx+10:.1f}" y="{ly+4:.1f}" class="ax" '
                     f'style="fill:{colors[key]}">{key.upper()} ₮{fmt(vals[-1])}</text>')
    for i in (0, len(common) // 2, len(common) - 1):
        x = ml + pw * (i / (len(common) - 1))
        parts.append(f'<text x="{x:.1f}" y="{h-12}" class="ax" text-anchor="middle">'
                     f'{common[i][2:7].replace("-", "·")}</text>')
    parts.append("</svg>")
    return "".join(parts)


def spark(s: dict, weeks: int = 13, w=104, h=28) -> str:
    ds = sorted(s)[-weeks:]
    vals = [s[d] for d in ds]
    lo, hi = min(vals), max(vals)
    pts = _pts(vals, 1, 3, w - 2, h - 6, lo - (hi - lo) * .1 or lo * .99, hi + (hi - lo) * .1 or hi * 1.01)
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (f'<svg viewBox="0 0 {w} {h}" class="spark"><polyline points="{poly}" fill="none" '
            f'stroke="{C["dim"]}" stroke-width="1.5"/></svg>')


# ---------------------------------------------------------------- render

def render(series: dict, live: bool, official: dict, official_live: bool,
           ledger: list, cal: dict | None, board: dict) -> str:
    weeks = sorted(series["beef_boneless"])
    as_of = weeks[-1]
    chart_from = weeks[-CHART_WEEKS] if len(weeks) >= CHART_WEEKS else weeks[0]

    # Anchors come from the NSO monthly table itself when available — the board is the
    # fallback and still supplies the next-print date and the validation beef anchor.
    off_transport = None
    if official:
        om = max(m for m in official if official[m])
        off_head, off_food = official[om].get("headline"), official[om].get("food")
        off_transport = official[om].get("transport")
        cpi_asof = month_end(om)
        anchor_note_txt = f"Last official print · {om} · NSO table DT_NSO_0600_010V1"
    else:
        off_head, off_food = board["cpi"], board["food_cpi"]
        cpi_asof = board["cpi_asof"]
        anchor_note_txt = (f"Last official print · data to {cpi_asof}" if cpi_asof
                           else "Official anchors unavailable")

    m = {}
    for k in series:
        s = series[k]
        d = sorted(s)[-1]
        lo, hi = PRICE_BOUNDS[k]
        if not (lo <= s[d] <= hi):
            sys.exit(f"build BOUNDS gate: {k}={s[d]} outside [{lo},{hi}] — not rendering")
        m[k] = {"price": s[d], "yoy": yoy_of(s, d), "m4": m4_of(s, d), "as_of": d}

    meat_yoy = [m[k]["yoy"] for k in MEAT if m[k]["yoy"] is not None]
    meat_m4 = [m[k]["m4"] for k in MEAT if m[k]["m4"] is not None]
    yoy = round(sum(meat_yoy) / len(meat_yoy), 1)
    mom4 = round(sum(meat_m4) / len(meat_m4), 1) if meat_m4 else None
    track = momentum_track(series)
    sev, sev_col = severity(yoy)

    # Honest staleness: the masthead must never look fresh on a failed fetch (contract, top
    # of file) — freshness requires BOTH a live fetch and a recent data week.
    today = date.today()
    age = (today - datetime.strptime(as_of, "%Y-%m-%d").date()).days
    if not live:
        badge_cls, badge_txt = "stale", "Cached · live fetch failed"
    elif age <= 14:  # one publish cycle plus slack; holiday gaps legitimately exceed this
        badge_cls, badge_txt = "ok", "Weekly · current"
    else:
        badge_cls, badge_txt = "stale", "Weekly · awaiting publish"

    # The verdict is assembled from the figures, never hand-written.
    if mom4 is not None and mom4 < 1.0 and yoy > AMBER_ABOVE:
        verdict = ("The spike is plateauing at altitude — the impulse has faded to near zero "
                   "while the level stays extreme. Prices stopped climbing; they never came down.")
    elif mom4 is not None and mom4 >= 3.0:
        verdict = ("The impulse is re-accelerating — four-week momentum is building again, "
                   "which lands in the next official print before it lands in policy.")
    elif yoy <= AMBER_ABOVE:
        verdict = ("The meat shock is unwinding at the consumer level — the strongest single "
                   "disinflation signal Mongolia can print.")
    else:
        verdict = ("The level remains elevated while momentum runs mildly positive — "
                   "the transmission has slowed but not stopped.")

    # The impulse must not read "calm" when it is re-accelerating — colour it by the value.
    if mom4 is None:
        imp_cls, imp_read = "cool", "no impulse reading"
    elif mom4 < 1.0:
        imp_cls, imp_read = "cool", "FADING — the shock is passing through"
    elif mom4 < 3.0:
        imp_cls, imp_read = "warm", "STILL RUNNING — slowed, not stopped"
    else:
        imp_cls, imp_read = "hot", "RE-ACCELERATING — this lands in the next print"

    lead_weeks = len([d for d in weeks if cpi_asof and d > cpi_asof])

    # "4 weeks" is really 4 SURVEYS — holiday gaps stretch the span, so label the real days.
    span_days = ((datetime.strptime(weeks[-1], "%Y-%m-%d")
                  - datetime.strptime(weeks[-5], "%Y-%m-%d")).days
                 if len(weeks) >= 5 else None)

    if lead_weeks:
        meta_lead = (f" — published {lead_weeks} week{'s' if lead_weeks != 1 else ''} "
                     f"ahead of the official CPI print")
        why_leads = (f"the shaded band is the {lead_weeks} week"
                     f"{'s' if lead_weeks != 1 else ''} of price data the last official "
                     f"print does not yet contain")
    else:
        meta_lead = ""
        why_leads = "every week shown is already covered by the last official print"

    # Only promise a next print date that is still in the future.
    next_print = (board["next_print"]
                  if board["next_print"] and board["next_print"] >= today.isoformat() else None)

    rows = []
    for key, en, mn, unit in DISPLAY:
        if key not in m:
            continue
        d = m[key]
        s_lab, s_col = severity(d["yoy"])
        rows.append(f"""
      <tr{' class="hero-row"' if key == "beef_boneless" else ''}>
        <td>
          <div class="item-en">{en}</div>
          <div class="item-mn" lang="mn">{mn}</div>
        </td>
        <td class="num">₮{fmt(d['price'])}<span class="unit">/{unit}</span></td>
        <td class="num sub">{pct(d['m4'])}</td>
        <td><span class="chip {s_col}">{s_lab} {pct(d['yoy'])}</span></td>
        <td class="sparkcell">{spark(series[key])}</td>
      </tr>""")

    # Fuel: table rows, and a callout whenever a survey-to-survey step clears the threshold.
    frows, jumps = [], []
    for key, en, mnn, unit in FUEL_DISPLAY:
        if key not in m:
            continue
        d = m[key]
        st = last_step(series[key])
        s_lab, s_col = severity(d["yoy"])
        step_cell = (f'{st["pct"]:+.1f}% <span class="unit">/ {st["days"]}d</span>'
                     if st else "—")
        if st and abs(st["pct"]) >= FUEL_STEP_CALLOUT_PCT:
            jumps.append(f"{en} moved {st['pct']:+.1f}% in one survey step — "
                         f"₮{fmt(st['prev_p'])} → ₮{fmt(st['last_p'])} per litre across "
                         f"{st['days']} days ({st['prev_d']} → {st['last_d']})")
        frows.append(f"""
      <tr{' class="hero-row"' if key == "a92" else ''}>
        <td>
          <div class="item-en">{en}</div>
          <div class="item-mn" lang="mn">{mnn}</div>
        </td>
        <td class="num">₮{fmt(d['price'])}<span class="unit">/{unit}</span></td>
        <td class="num sub">{step_cell}</td>
        <td><span class="chip {s_col}">{s_lab} {pct(d['yoy'])}</span></td>
        <td class="sparkcell">{spark(series[key])}</td>
      </tr>""")
    fuel_callout = ("" if not jumps else
                    '<div class="callout">' + "<br>".join(jumps) + "</div>")
    transport_note = (f", currently {pct(off_transport)} year on year"
                      if off_transport is not None else "")

    anchors = ""
    if off_food is not None:
        anchors += (f'<div class="anchor"><div class="anchor-l">Official food CPI · YoY</div>'
                    f'<div class="anchor-v">{off_food:.1f}%</div></div>')
    if off_head is not None:
        anchors += (f'<div class="anchor"><div class="anchor-l">Headline CPI · YoY</div>'
                    f'<div class="anchor-v">{off_head:.1f}%</div></div>')
    anchor_note = anchor_note_txt

    board_row = ""
    if board["board_value"] is not None and board["board_week"] and board["board_week"] != as_of:
        board_row = (f' · STELE board last wrote {board["board_value"]:.1f}% at week '
                     f'{board["board_week"]} (this page is computed fresh from NSO)')

    # Like-for-like validation, computed at render: mean of the weekly beef observations
    # INSIDE the official report's reference month vs that report's own UB beef average.
    # Never a hand-typed figure, never a week-vs-month comparison; omitted if unavailable.
    validation_row = ""
    if board["beef_anchor"] and board["beef_anchor_month"]:
        ref_month = board["beef_anchor_month"]
        obs = [v for d_, v in series["beef_boneless"].items() if d_[:7] == ref_month]
        if obs:
            mmean = sum(obs) / len(obs)
            diff = 100 * (mmean / board["beef_anchor"] - 1)
            validation_row = (
                f"<dt>Validation ·</dt><dd> mean of the {len(obs)} weekly beef observations "
                f"in {ref_month} (₮{fmt(mmean)}) against the official CPI report's UB beef "
                f"average for that month (₮{fmt(board['beef_anchor'])}) — a {abs(diff):.3f}% "
                f"difference{board_row}</dd>")

    # Scored calls — newest vintage first. The section appears once the first call exists.
    lrows = []
    for c in sorted(ledger, key=lambda c: (c["target_month"], len(c["weeks_observed"])),
                    reverse=True):
        nw = len(c["weeks_observed"])
        if c["status"] == "graded":
            chip_cls, chip_txt = ("teal", "HIT") if c["hit"] else ("red", "MISS")
            official_cell = f"{c['official_value']:+.1f}%"
            err_cell = f"{c['error']:+.1f}pp"
        else:
            chip_cls, chip_txt = "dim", "OPEN"
            official_cell = (f'<span class="unit">due {c["expected_release"]}</span>'
                             if c.get("expected_release") else "—")
            err_cell = "—"
        lrows.append(f"""
      <tr>
        <td class="num sub">{c['made_on']}</td>
        <td><div class="item-en">{c['target_month']} print</div>
            <div class="item-mn">{nw} survey week{'' if nw == 1 else 's'} observed</div></td>
        <td class="num">{c['predicted']:+.1f}% <span class="unit">± {c['band']:.1f}</span></td>
        <td class="num">{official_cell}</td>
        <td class="num sub">{err_cell}</td>
        <td><span class="chip {chip_cls}">{chip_txt}</span></td>
      </tr>""")
    ledger_section = "" if not lrows else f"""
<section class="wrap">
  <h2 class="lbl" style="margin-bottom:16px">Scored calls · dated before the print, graded after it</h2>
  <div class="panel">
    <table>
      <caption class="lbl">Predictions of the official food-CPI print · append-only ledger</caption>
      <thead><tr>
        <th scope="col">Made</th><th scope="col">Target</th>
        <th scope="col">Call · food CPI YoY</th><th scope="col">Official</th>
        <th scope="col">Error</th><th scope="col">Verdict</th>
      </tr></thead>
      <tbody>{''.join(lrows)}</tbody>
    </table>
    <div class="tnote">
      A call is never edited after it is made — grading only appends the official figure from
      the same NSO table the prediction targets. Raw ledger:
      <a href="calls_ledger.json">calls_ledger.json</a>.
      Latest method: {ledger[-1]['method']}.
    </div>
  </div>
</section>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nowflation.mn — Mongolian food and fuel prices, weekly, ahead of the CPI</title>
<meta name="description" content="A weekly nowcast of Ulaanbaatar food and fuel prices from the NSO price
 survey. Meat basket {pct(yoy)} year on year as of week {as_of}{meta_lead}.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500;700&family=Source+Serif+4:ital,wght@0,400;1,400&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  :root{{
    --bg:{C['bg']}; --surface:{C['surface']}; --surface-hi:{C['surface_hi']};
    --text:{C['text']}; --dim:{C['dim']}; --line:{C['line']};
    --teal:{C['teal']}; --teal-deep:{C['teal_deep']}; --red:{C['red']};
    --red-bg:{C['red_bg']}; --amber:{C['amber']};
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
    --mono:'JetBrains Mono',ui-monospace,'Cascadia Mono',Consolas,monospace;
    --serif:'Source Serif 4',Georgia,serif;
  }}
  html,body{{margin:0;padding:0}}
  h1,h2,h3{{margin:0}}
  body{{background:var(--bg);color:var(--text);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.5}}
  .wrap{{max-width:1280px;margin:0 auto;padding:0 24px}}
  a{{color:var(--teal);text-decoration:none}}
  a:hover{{text-decoration:underline}}
  .mono{{font-family:var(--mono)}}
  .lbl{{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.14em;
    text-transform:uppercase;color:var(--dim)}}

  /* masthead — a page, not an app: no sidebar, no fake nav */
  header.mast{{border-bottom:1px solid var(--line);background:var(--surface)}}
  .mast-in{{display:flex;flex-wrap:wrap;gap:16px;align-items:baseline;
    justify-content:space-between;padding:18px 0}}
  .brand{{font-size:22px;font-weight:800;letter-spacing:-.03em}}
  .brand span{{color:var(--teal)}}
  .cadence{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
  .badge{{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.1em;
    text-transform:uppercase;padding:5px 9px;border:1px solid var(--line);color:var(--dim)}}
  .badge.ok{{border-color:var(--teal-deep);color:var(--teal)}}
  .badge.stale{{border-color:var(--amber);color:var(--amber)}}

  section{{padding:52px 0;border-bottom:1px solid var(--line)}}

  /* hero */
  .hero-top{{display:flex;flex-wrap:wrap;gap:48px;align-items:flex-end}}
  .hero-l{{flex:1 1 460px;min-width:300px}}
  .kicker{{margin-bottom:14px}}
  .kicker .mn{{color:var(--dim);text-transform:none;letter-spacing:.02em}}
  .big{{font-size:clamp(72px,13vw,150px);font-weight:800;letter-spacing:-.05em;
    line-height:.86;color:var(--teal);font-variant-numeric:tabular-nums}}
  /* the hero is DATA, not brand — it takes the severity colour, same scale as the chips */
  .big.red{{color:var(--red)}}
  .big.amber{{color:var(--amber)}}
  .big-sub{{margin-top:12px;font-family:var(--mono);font-size:12px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--dim)}}
  .anchors{{display:flex;gap:36px;border-left:1px solid var(--line);padding-left:28px}}
  .anchor-l{{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.12em;
    text-transform:uppercase;color:var(--dim);margin-bottom:6px}}
  .anchor-v{{font-size:34px;font-weight:700;letter-spacing:-.02em;
    font-variant-numeric:tabular-nums}}
  .anchor-note{{margin-top:10px;font-family:var(--mono);font-size:10px;color:var(--dim);
    letter-spacing:.06em;text-transform:uppercase}}
  .verdict{{margin-top:40px;max-width:760px;border-left:4px solid var(--teal);
    padding:6px 0 6px 22px;font-family:var(--serif);font-style:italic;font-size:21px;
    line-height:1.55;color:#cfd9d8}}
  .callout{{border-left:4px solid var(--amber);background:var(--surface);
    padding:14px 20px;margin-bottom:16px;font-size:15px;line-height:1.65}}

  /* the momentum contrast — level vs impulse, the reason the page exists */
  .contrast{{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--line);
    background:var(--surface)}}
  .contrast > div{{padding:26px}}
  .contrast > div + div{{border-left:1px solid var(--line)}}
  .cv{{font-size:46px;font-weight:800;letter-spacing:-.03em;line-height:1;
    font-variant-numeric:tabular-nums;margin:10px 0 4px}}
  .cv.level{{color:var(--red)}}
  .cv.imp.cool{{color:var(--teal)}}
  .cv.imp.warm{{color:var(--amber)}}
  .cv.imp.hot{{color:var(--red)}}
  .cdesc{{font-size:14px;color:var(--dim);max-width:44ch}}
  .readout{{margin-top:18px;padding-top:16px;border-top:1px solid var(--line);
    font-family:var(--mono);font-size:12px;letter-spacing:.04em;color:var(--text)}}

  /* charts */
  .panel{{border:1px solid var(--line);background:var(--surface)}}
  .panel-h{{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;
    align-items:center;padding:16px 20px;border-bottom:1px solid var(--line)}}
  .panel-b{{padding:18px 20px;overflow-x:auto}}
  .panel-b .chart{{min-width:760px}}
  .scrollx{{overflow-x:auto}}
  .scrollx .chart{{min-width:430px}}
  .chart{{display:block;width:100%;height:auto}}
  .chart .ax{{font-family:var(--mono);font-size:12px;fill:{C['dim']}}}
  .chart .lead{{fill:{C['teal']};letter-spacing:.12em}}
  .chart .tag{{font-family:var(--mono);font-size:12px;font-weight:700;fill:#00201f}}
  .legend{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
  .legend i{{display:inline-block;width:14px;height:2px;background:var(--teal);
    margin-right:7px;vertical-align:middle}}
  .legend i.zone{{height:10px;background:rgba(79,232,226,.16);
    border-left:1px dashed var(--teal)}}

  /* table */
  table{{width:100%;border-collapse:collapse}}
  caption{{caption-side:top;text-align:left;padding:14px 16px;
    border-bottom:1px solid var(--line)}}
  th{{text-align:left;padding:13px 16px;background:var(--surface-hi);
    border-bottom:1px solid var(--line);font-family:var(--mono);font-size:10px;
    font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:var(--dim)}}
  td{{padding:15px 16px;border-bottom:1px solid var(--line);vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  .hero-row{{background:var(--surface-hi)}}
  .hero-row .item-en{{color:var(--teal)}}
  .item-en{{font-weight:600;font-size:15px}}
  .item-mn{{font-size:12px;color:var(--dim);margin-top:2px}}
  td.num{{font-family:var(--mono);font-size:16px;font-weight:500;white-space:nowrap;
    font-variant-numeric:tabular-nums}}
  td.num.sub{{font-size:13px;color:var(--dim)}}
  .unit{{color:var(--dim);font-size:10px;margin-left:3px}}
  .chip{{display:inline-block;padding:4px 8px;border:1px solid;font-family:var(--mono);
    font-size:10px;font-weight:700;letter-spacing:.06em;white-space:nowrap}}
  .chip.red{{color:#ffdad6;background:var(--red-bg);border-color:var(--red-bg)}}
  .chip.amber{{color:var(--amber);border-color:var(--amber)}}
  .chip.teal{{color:var(--teal);border-color:var(--teal)}}
  .chip.dim{{color:var(--dim);border-color:var(--line)}}
  .spark{{width:104px;height:28px;display:block}}
  .sparkcell{{width:120px}}
  .tnote{{padding:14px 16px;border-top:1px solid var(--line);font-family:var(--mono);
    font-size:11px;color:var(--dim);line-height:1.7}}

  /* provenance */
  footer{{padding:44px 0 64px;background:#0e0e0e;border-top:1px solid var(--line)}}
  .prov{{display:grid;grid-template-columns:1.35fr 1fr;gap:44px}}
  .prov dl{{margin:14px 0 0;font-family:var(--mono);font-size:12px;line-height:1.85;
    color:var(--dim)}}
  .prov dt{{color:var(--text);display:inline;font-weight:500}}
  .prov dd{{display:inline;margin:0}}
  .prov dd::after{{content:"";display:block}}
  .disclaim{{margin-top:22px;font-size:12px;color:var(--dim);max-width:60ch;line-height:1.7}}

  @media(max-width:860px){{
    .contrast{{grid-template-columns:1fr}}
    .contrast > div + div{{border-left:none;border-top:1px solid var(--line)}}
    .prov{{grid-template-columns:1fr;gap:28px}}
    .anchors{{border-left:none;padding-left:0;gap:28px}}
    section{{padding:38px 0}}
    td,th{{padding:12px 10px}}
    .sparkcell{{display:none}}
  }}
</style>
</head>
<body>

<header class="mast">
  <div class="wrap mast-in">
    <div>
      <h1 class="brand">NOWFLATION<span>.MN</span></h1>
      <div class="lbl" style="margin-top:4px">Ulaanbaatar food &amp; fuel prices · weekly · ahead of the CPI</div>
    </div>
    <div class="cadence">
      <span class="badge {badge_cls}">{badge_txt}</span>
      <span class="badge">Data week {as_of}</span>
      <span class="badge">{age} day{'' if age == 1 else 's'} old</span>
    </div>
  </div>
</header>

<main>
<section class="wrap">
  <div class="hero-top">
    <div class="hero-l">
      <h2 class="kicker lbl">UB meat basket · year on year<br>
        <span class="mn" lang="mn">Улаанбаатар · махны сагс · өмнөх оноос</span></h2>
      <div class="big {sev_col}">{pct(yoy)}</div>
      <div class="big-sub">Beef, mutton and goat · NSO weekly survey · week {as_of}</div>
    </div>
    <div>
      <div class="anchors">{anchors}</div>
      <div class="anchor-note">{anchor_note}</div>
    </div>
  </div>
  <p class="verdict">"{verdict}"</p>
</section>

<section class="wrap">
  <h2 class="lbl" style="margin-bottom:16px">Level versus impulse · read the momentum, not the level</h2>
  <div class="contrast">
    <div>
      <h3 class="lbl">Level · year on year</h3>
      <div class="cv level">{pct(yoy)}</div>
      <div class="cdesc">Where prices sit against a year ago. Stays high on base effects long
        after the shock itself has passed.</div>
      <div class="readout">STATUS: {sev} — board gate: amber &gt;{AMBER_ABOVE:.0f}% · red &gt;{RED_ABOVE:.0f}%</div>
    </div>
    <div>
      <h3 class="lbl">Impulse · last 4 surveys{f' · {span_days} days' if span_days else ''}</h3>
      <div class="cv imp {imp_cls}">{pct(mom4)}</div>
      <div class="cdesc">What prices are doing right now. This is the number that turns first —
        the bars below are the last {len(track)} surveys of it.</div>
      <div class="scrollx" style="margin-top:14px">{momentum_chart(track)}</div>
      <div class="readout">READING: {imp_read}</div>
    </div>
  </div>
</section>

<section class="wrap">
  <div class="panel">
    <div class="panel-h">
      <h2 class="lbl">Beef, boneless · ₮/kg · weekly · {chart_from} → {as_of}</h2>
      <div class="legend lbl">
        <span><i></i>NSO weekly survey</span>
        <span><i class="zone"></i>Ahead of the official print</span>
      </div>
    </div>
    <div class="panel-b">{price_chart(series, "beef_boneless", cpi_asof)}</div>
  </div>
</section>

<section class="wrap">
  <div class="panel">
    <table>
      <caption class="lbl">The basket · week {as_of}</caption>
      <thead><tr>
        <th scope="col">Item · <span lang="mn">Бараа</span></th><th scope="col">Price</th>
        <th scope="col">Last 4 surveys</th><th scope="col">Year on year</th>
        <th scope="col" class="sparkcell">13-week trend</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <div class="tnote">
      Severity gates: contained ≤{AMBER_ABOVE:.0f}% · elevated &gt;{AMBER_ABOVE:.0f}% ·
      severe &gt;{RED_ABOVE:.0f}% year on year.
      The headline basket is the mean of the four meat series; flour is shown as context and
      is not in it.
    </div>
  </div>
</section>

<section class="wrap">
  <h2 class="lbl" style="margin-bottom:16px">Fuel · Шатахуун · the administered price that moves in steps</h2>
  {fuel_callout}
  <div class="panel">
    <div class="panel-h">
      <h3 class="lbl">A-92 &amp; diesel · ₮/L · weekly</h3>
      <div class="legend lbl">
        <span><i></i>A-92</span>
        <span><i style="background:var(--amber)"></i>Diesel</span>
      </div>
    </div>
    <div class="panel-b">{fuel_chart(series)}</div>
    <table>
      <thead><tr>
        <th scope="col">Item · <span lang="mn">Бараа</span></th><th scope="col">Price</th>
        <th scope="col">Last survey step</th><th scope="col">Year on year</th>
        <th scope="col" class="sparkcell">13-week trend</th>
      </tr></thead>
      <tbody>{''.join(frows)}</tbody>
    </table>
    <div class="tnote">
      Fuel is outside the meat basket and the food group; its official counterpart is the
      transport group of the CPI (<span lang="mn">Тээвэр</span>){transport_note} — fuel
      prices feed it alongside vehicles and fares. Retail fuel is administered, so it moves
      in discrete steps; any step of {FUEL_STEP_CALLOUT_PCT:.0f}% or more between two
      surveys is called out above.
    </div>
  </div>
</section>
{ledger_section}
</main>

<footer>
  <div class="wrap">
    <h2 class="lbl" style="color:var(--teal)">Provenance</h2>
    <div class="prov">
      <div>
        <dl>
          <dt>Source ·</dt><dd> National Statistics Office of Mongolia, weekly Ulaanbaatar
            price survey, PXWeb table DT_NSO_0600_001V4; official monthly CPI by group,
            table DT_NSO_0600_010V1, base 2023=100 (both data.1212.mn)</dd>
          <dt>Data week ·</dt><dd> {as_of} — {age} day{'' if age == 1 else 's'} old at render</dd>
          <dt>Cadence ·</dt><dd> weekly reference periods, published on the statistics
            office's own schedule; gaps around public holidays are real, not missing data</dd>
          <dt>Method ·</dt><dd> year on year against the last surveyed week on or before the
            same date one year earlier; impulse is the change across the preceding four
            surveys{f', currently spanning {span_days} days' if span_days else ''}; the
            basket is the unweighted mean of beef bone-in, beef boneless, mutton and goat</dd>
          {validation_row}
          {'<dt>Calls ·</dt><dd> the scored-calls ledger is append-only — every prediction is dated and banded before the print and graded automatically from the same NSO table after it</dd>' if ledger else ''}
          <dt>Namesake ·</dt><dd> the name nods to <a href="https://nowflation.com">nowflation.com</a>,
            Steven Fiorillo's daily US inflation gauge, which inspired this page — the two
            projects are otherwise unrelated</dd>
          <dt>Rendered ·</dt><dd> {today.isoformat()}; weekly survey {'live' if live else 'from cache — the live fetch failed'}; official CPI table {'live' if official_live else 'from cache'}</dd>
        </dl>
        <p class="disclaim">This is a nowcast, not an official statistic. It tracks a narrow
        basket of Ulaanbaatar retail prices and is published ahead of, and independently of,
        the National Statistics Office consumer price index. It does not replace the CPI and
        makes no claim to reproduce it.</p>
      </div>
      <div>
        <h3 class="lbl">Why it leads</h3>
        <p class="disclaim" style="margin-top:12px">These prices are surveyed weekly; the
        consumer price index is published monthly. On the chart above,
        {why_leads}{'' if not next_print else f' — the next print is due {next_print}'}.</p>
        <h3 class="lbl" style="margin-top:26px">Reproduce it</h3>
        <p class="disclaim" style="margin-top:12px">Every figure on this page is computed at
        render time from the source tables or, for the validation anchor, read from the
        recorded CPI release. Nothing is typed in by hand. The build script, the cached
        series and the calls ledger sit beside this file.</p>
        <h3 class="lbl" style="margin-top:26px">More</h3>
        <p class="disclaim" style="margin-top:12px"><a href="aimag.html">Aimag price monitor</a>
        — the same survey across all 21 aimags (working demo) ·
        <a href="capability.html">Capability note</a> — extensions and institutional work.</p>
      </div>
    </div>
  </div>
</footer>
</body>
</html>"""


def main() -> None:
    # Windows consoles default to cp1252 and cannot print ₮ — the page is UTF-8 regardless.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    allow = "--no-fetch" not in sys.argv
    series, live = load_series(allow_fetch=allow)
    official, official_live = load_official(allow_fetch=allow)
    board = read_board()
    if "--emit-anchors" in sys.argv and board["beef_anchor"] and board["beef_anchor_month"]:
        ANCHORS.write_text(json.dumps({
            "next_print": board["next_print"],
            "cpi_report": {"month": board["beef_anchor_month"],
                           "ub_beef_avg_mnt": board["beef_anchor"]},
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"build: anchors.json emitted — report {board['beef_anchor_month']}, "
              f"next print {board['next_print']}")
    cal = calibrate(series, official)
    ledger = update_ledger(series, official, cal, board["next_print"], date.today())
    html = render(series, live, official, official_live, ledger, cal, board)
    OUT.write_text(html, encoding="utf-8")

    weeks = sorted(series["beef_boneless"])
    print(f"build: wrote {OUT} ({len(html):,} bytes) — data week {weeks[-1]}, "
          f"{'live' if live else 'CACHED'}")
    print("build: verification — every figure below must appear on the page")
    for key, en, _, unit in DISPLAY + FUEL_DISPLAY:
        if key in series:
            s = series[key]
            d = sorted(s)[-1]
            print(f"  {en:<20} ₮{fmt(s[d]):>9}/{unit:<2} YoY {pct(yoy_of(s,d)):>8} "
                  f"4wk {pct(m4_of(s,d)):>7}")
    mm = [m4_of(series[k], sorted(series[k])[-1]) for k in MEAT]
    yy = [yoy_of(series[k], sorted(series[k])[-1]) for k in MEAT]
    print(f"  {'MEAT BASKET':<20} YoY {sum(yy)/len(yy):+.1f}%  4wk {sum(mm)/len(mm):+.1f}%")
    if cal:
        print(f"build: calibration — {cal['n']} months since {cal['from']}: "
              f"food = {cal['a']:+.2f} + {cal['b']:.3f} × meat  "
              f"(R²={cal['r2']:.2f}, RMSE={cal['rmse']:.2f}pp)")
    else:
        print("build: calibration insufficient (<6 month pairs) — no call made")
    for c in ledger:
        tail = (f"official {c['official_value']:+.1f}%, err {c['error']:+.1f}pp — "
                f"{'HIT' if c['hit'] else 'MISS'}" if c["status"] == "graded"
                else f"grades at the {c.get('expected_release') or 'next'} release")
        print(f"  CALL {c['id']} made {c['made_on']}: food CPI YoY {c['predicted']:+.1f}% "
              f"± {c['band']:.1f} [{c['status'].upper()}] — {tail}")


if __name__ == "__main__":
    main()
