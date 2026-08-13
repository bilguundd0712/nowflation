r"""
build_aimag.py — renders aimag.html: the same weekly survey, across the country.

The working proof of the "aimag coverage" extension: NSO also surveys food and fuel prices
in all 21 aimags weekly (PXWeb table DT_NSO_0300_010V5, data.1212.mn — a different survey
series from the UB table, with its own publish rhythm). This page shows beef, petrol,
diesel and baled hay by aimag against Ulaanbaatar, with year-on-year where computable.

Same contract as build_nowflation.py: live fetch with cache fallback (aimag_cache.json),
per-item plausibility bounds, no hand-entered figures, honest staleness stamp.

Run: python build_aimag.py   [--no-fetch]   (writes aimag.html beside this file)
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import requests

BASE = Path(__file__).parent.resolve()
CACHE = BASE / "aimag_cache.json"
UB_CACHE = BASE / "series_cache.json"
OUT = BASE / "aimag.html"

URL = ("https://data.1212.mn/api/v1/mn/NSO/" + urllib.parse.quote("Economy, environment")
       + "/" + urllib.parse.quote("Consumer Price Index") + "/DT_NSO_0300_010V5.px")

PRODUCTS = {"6": "beef_bone_in", "2": "a92", "4": "diesel", "3": "hay"}
BOUNDS = {"beef_bone_in": (8000, 90000), "a92": (1500, 6000), "diesel": (1500, 8000),
          "hay": (3000, 200000)}

REGIONS = {"1": "Баруун бүс · West", "2": "Хангайн бүс · Khangai",
           "3": "Төвийн бүс · Central", "4": "Зүүн бүс · East"}
# Region code → member aimag codes, in the table's own order.
REGION_OF = {
    "1": ["183", "182", "181", "185", "184"],
    "2": ["265", "264", "263", "261", "262", "267"],
    "3": ["342", "345", "344", "348", "346", "343", "341"],
    "4": ["421", "422", "423"],
}
AIMAG_NAMES = {
    "183": "Баян-Өлгий", "182": "Говь-Алтай", "181": "Завхан", "185": "Увс", "184": "Ховд",
    "265": "Архангай", "264": "Баянхонгор", "263": "Булган", "261": "Орхон",
    "262": "Өвөрхангай", "267": "Хөвсгөл",
    "342": "Говьсүмбэр", "345": "Дархан-Уул", "344": "Дорноговь", "348": "Дундговь",
    "346": "Өмнөговь", "343": "Сэлэнгэ", "341": "Төв",
    "421": "Дорнод", "422": "Сүхбаатар", "423": "Хэнтий",
}

C = {"bg": "#0A0A0A", "surface": "#141414", "surface_hi": "#1c1b1b", "text": "#F5F5F5",
     "dim": "#888888", "line": "#262626", "teal": "#4fe8e2", "red": "#ffb3ad",
     "red_bg": "#a40217", "amber": "#ffc8a1"}


def norm_week(label: str) -> str | None:
    """NSO week labels are usually ISO but NOT always — this table has emitted '2026-8-10'
    for 2026-08-10 (1 label in 60). An unpadded month breaks string sorting the moment a
    zero-padded later month arrives ('2026-09-07' < '2026-8-10'), which would silently
    freeze the page on a stale week and corrupt the year-ago base lookup. Normalise on
    ingest so every downstream string comparison is safe."""
    try:
        y, m, d = (int(x) for x in str(label).strip().split("-"))
        return date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


def fetch(weeks: int = 60) -> dict:
    meta = requests.get(URL, timeout=40).json()
    tv = next(v for v in meta["variables"] if v["code"] == "Хугацаа")
    idx = tv["values"][:weeks]
    labels = dict(zip(tv["values"], tv["valueTexts"]))
    geo = [g for members in REGION_OF.values() for g in members]
    body = {"query": [
        {"code": "Бүтээгдэхүүн", "selection": {"filter": "item", "values": list(PRODUCTS)}},
        {"code": "Бүс", "selection": {"filter": "item", "values": geo}},
        {"code": "Хугацаа", "selection": {"filter": "item", "values": idx}},
    ], "response": {"format": "json"}}
    r = requests.post(URL, json=body, timeout=90)
    r.raise_for_status()
    out: dict = {}
    for row in r.json()["data"]:
        prod = PRODUCTS.get(row["key"][0])
        geo_c = row["key"][1]
        wk = norm_week(labels.get(row["key"][2], row["key"][2]))
        if wk is None:
            continue
        try:
            val = float(row["values"][0])
        except (TypeError, ValueError):
            continue
        if prod:
            out.setdefault(geo_c, {}).setdefault(prod, {})[wk] = val
    return out


def load(allow_fetch: bool = True) -> tuple[dict, bool]:
    if allow_fetch:
        try:
            d = fetch()
            if d and len(d) >= 15:
                CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                return d, True
            print("aimag: fetch too thin — falling back to cache", file=sys.stderr)
        except Exception as e:
            print(f"aimag: fetch failed ({e}) — falling back to cache", file=sys.stderr)
    if not CACHE.exists():
        sys.exit("aimag: no live data and no cache — not rendering")
    return json.loads(CACHE.read_text(encoding="utf-8")), False


def yoy_of(s: dict, at: str) -> float | None:
    try:
        target = (datetime.strptime(at, "%Y-%m-%d").date()
                  .replace(year=datetime.strptime(at, "%Y-%m-%d").year - 1)).isoformat()
    except ValueError:
        target = f"{int(at[:4]) - 1}-02-28"
    base = next((d for d in reversed(sorted(s)) if d <= target), None)
    if base is None or s.get(base) in (None, 0):
        return None
    return round(100 * (s[at] / s[base] - 1), 1)


def latest(s: dict) -> tuple[str, float] | None:
    if not s:
        return None
    d = sorted(s)[-1]
    return (d, s[d])


def fmt(n: float) -> str:
    return f"{n:,.0f}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def cell(geo: dict, prod: str) -> tuple[str, str, float | None]:
    """(price_html, yoy_str, price_val) for one product in one geography."""
    s = geo.get(prod) or {}
    lt = latest(s)
    if not lt:
        return "—", "—", None
    d, v = lt
    lo, hi = BOUNDS[prod]
    if not (lo <= v <= hi):
        return "—", "—", None
    return f"₮{fmt(v)}", pct(yoy_of(s, d)), v


def render(data: dict, live: bool) -> str:
    # UB comparison row from the main page's cache (same product definitions).
    ub = {}
    if UB_CACHE.exists():
        try:
            ub = json.loads(UB_CACHE.read_text(encoding="utf-8"))
        except Exception:
            ub = {}

    all_weeks = sorted({w for g in data.values() for p in g.values() for w in p})
    as_of = all_weeks[-1] if all_weeks else "?"
    today = date.today()
    age = (today - datetime.strptime(as_of, "%Y-%m-%d").date()).days if all_weeks else None

    # Beef spread stat: cheapest and dearest aimag this week.
    beef = []
    for code, geo in data.items():
        _, _, v = cell(geo, "beef_bone_in")
        if v is not None:
            beef.append((v, AIMAG_NAMES.get(code, code)))
    beef.sort()
    spread = ""
    if len(beef) >= 2:
        (lo_v, lo_n), (hi_v, hi_n) = beef[0], beef[-1]
        spread_pct = 100 * (hi_v / lo_v - 1)
        spread = (f'Beef, bone-in this week: cheapest in <b lang="mn">{lo_n}</b> at ₮{fmt(lo_v)}, '
                  f'dearest in <b lang="mn">{hi_n}</b> at ₮{fmt(hi_v)} — a {spread_pct:.0f}% '
                  f'spread across the country for the same cut.')

    rows = []
    for rcode, rname in REGIONS.items():
        rows.append(f'<tr class="region"><td colspan="5" lang="mn">{rname}</td></tr>')
        for code in REGION_OF[rcode]:
            geo = data.get(code, {})
            b_p, b_y, _ = cell(geo, "beef_bone_in")
            g_p, _, _ = cell(geo, "a92")
            d_p, _, _ = cell(geo, "diesel")
            h_p, h_y, _ = cell(geo, "hay")
            rows.append(f"""
      <tr>
        <td><div class="item-en" lang="mn">{AIMAG_NAMES.get(code, code)}</div></td>
        <td class="num">{b_p} <span class="unit">{b_y}</span></td>
        <td class="num">{g_p}</td>
        <td class="num">{d_p}</td>
        <td class="num">{h_p} <span class="unit">{h_y}</span></td>
      </tr>""")

    ub_row = ""
    if ub:
        def ub_cell(k):
            s = ub.get(k) or {}
            if not s:
                return "—"
            d = sorted(s)[-1]
            return f"₮{fmt(s[d])}"
        ub_row = (f'<tr class="hero-row"><td><div class="item-en">Улаанбаатар '
                  f'<span class="unit">(UB survey, DT_NSO_0600_001V4)</span></div></td>'
                  f'<td class="num">{ub_cell("beef_bone_in")}</td>'
                  f'<td class="num">{ub_cell("a92")}</td>'
                  f'<td class="num">{ub_cell("diesel")}</td><td class="num">—</td></tr>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nowflation.mn — aimag price monitor (working demo)</title>
<meta name="description" content="Weekly NSO food and fuel prices across all 21 Mongolian
 aimags — beef, petrol, diesel and baled hay by region, against Ulaanbaatar. A working demo
 of the aimag extension of nowflation.mn.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  :root{{--bg:{C['bg']};--surface:{C['surface']};--surface-hi:{C['surface_hi']};
    --text:{C['text']};--dim:{C['dim']};--line:{C['line']};--teal:{C['teal']};
    --red:{C['red']};--amber:{C['amber']};
    --sans:'Inter',system-ui,sans-serif;--mono:'JetBrains Mono',ui-monospace,monospace}}
  html,body{{margin:0;padding:0}}
  h1,h2{{margin:0}}
  body{{background:var(--bg);color:var(--text);font-family:var(--sans);line-height:1.5}}
  .wrap{{max-width:1080px;margin:0 auto;padding:0 24px}}
  a{{color:var(--teal);text-decoration:none}} a:hover{{text-decoration:underline}}
  .lbl{{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.14em;
    text-transform:uppercase;color:var(--dim)}}
  header.mast{{border-bottom:1px solid var(--line);background:var(--surface)}}
  .mast-in{{display:flex;flex-wrap:wrap;gap:16px;align-items:baseline;
    justify-content:space-between;padding:18px 0}}
  .brand{{font-size:20px;font-weight:800;letter-spacing:-.03em}}
  .brand span{{color:var(--teal)}}
  .badge{{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.1em;
    text-transform:uppercase;padding:5px 9px;border:1px solid var(--line);color:var(--dim)}}
  section{{padding:44px 0}}
  .demo-note{{border-left:4px solid var(--teal);background:var(--surface);
    padding:14px 20px;margin-bottom:28px;font-size:15px;line-height:1.65}}
  .spreadline{{margin:0 0 24px;font-size:16px;line-height:1.6;color:#cfd9d8;max-width:70ch}}
  .panel{{border:1px solid var(--line);background:var(--surface);overflow-x:auto}}
  table{{width:100%;border-collapse:collapse;min-width:680px}}
  caption{{caption-side:top;text-align:left;padding:14px 16px;
    border-bottom:1px solid var(--line)}}
  th{{text-align:left;padding:12px 14px;background:var(--surface-hi);
    border-bottom:1px solid var(--line);font-family:var(--mono);font-size:10px;
    font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:var(--dim)}}
  td{{padding:11px 14px;border-bottom:1px solid var(--line)}}
  tr:last-child td{{border-bottom:none}}
  tr.region td{{background:var(--surface-hi);font-family:var(--mono);font-size:11px;
    font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--teal)}}
  .hero-row{{background:var(--surface-hi)}}
  .item-en{{font-weight:600;font-size:14px}}
  td.num{{font-family:var(--mono);font-size:14px;white-space:nowrap;
    font-variant-numeric:tabular-nums}}
  .unit{{color:var(--dim);font-size:10px;margin-left:4px}}
  footer{{padding:36px 0 60px;background:#0e0e0e;border-top:1px solid var(--line);
    font-family:var(--mono);font-size:12px;color:var(--dim);line-height:1.8}}
</style>
</head>
<body>
<header class="mast">
  <div class="wrap mast-in">
    <div>
      <h1 class="brand">NOWFLATION<span>.MN</span> <span class="lbl">/ aimag monitor</span></h1>
      <div class="lbl" style="margin-top:4px"><a href="./">← back to the UB monitor</a> ·
        <a href="capability.html">capability note</a></div>
    </div>
    <div>
      <span class="badge">Data week {as_of}</span>
      <span class="badge">{'' if age is None else str(age) + (' day' if age == 1 else ' days') + ' old'}</span>
      <span class="badge">{'live' if live else 'cached'}</span>
    </div>
  </div>
</header>

<main>
<section class="wrap">
  <div class="demo-note">This page is a <b>working demo</b> of the aimag extension: the same
    weekly NSO survey, read across all 21 aimags — beef, petrol, diesel and baled hay
    (<span lang="mn">боодолтой өвс</span>, the herder staple that matters in a dzud winter).
    Custom builds on this data — full baskets, chosen aimags, early-warning thresholds,
    delivery as a feed — are what we do. <a href="capability.html">Details here.</a></div>
  <h2 class="lbl" style="margin-bottom:14px">Weekly prices by aimag · week {as_of}</h2>
  <p class="spreadline">{spread}</p>
  <div class="panel">
    <table>
      <caption class="lbl">Beef bone-in ₮/kg (with YoY) · A-92 ₮/L · diesel ₮/L ·
        baled hay ₮ per bale* (with YoY)</caption>
      <thead><tr>
        <th scope="col">Aimag · <span lang="mn">Аймаг</span></th>
        <th scope="col">Beef, bone-in</th><th scope="col">Petrol A-92</th>
        <th scope="col">Diesel</th><th scope="col">Baled hay</th>
      </tr></thead>
      <tbody>{ub_row}{''.join(rows)}</tbody>
    </table>
  </div>
</section>
</main>

<footer>
  <div class="wrap">
    Source: National Statistics Office of Mongolia, weekly aimag price survey, PXWeb table
    DT_NSO_0300_010V5 (data.1212.mn), CC BY 4.0 · Ulaanbaatar row from table
    DT_NSO_0600_001V4 · Rendered {today.isoformat()} · Missing cells are weeks the survey
    did not report for that aimag — shown as gaps, never interpolated.
    <br>* The source publishes hay as "<span lang="mn">Боодолтой өвс</span>" with no explicit
    unit, unlike the other items; the magnitudes indicate a price per bale. Every other column
    carries the unit the source states.
  </div>
</footer>
</body>
</html>"""


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    data, live = load(allow_fetch="--no-fetch" not in sys.argv)
    html = render(data, live)
    OUT.write_text(html, encoding="utf-8")
    n_geo = len(data)
    weeks = sorted({w for g in data.values() for p in g.values() for w in p})
    print(f"aimag: wrote {OUT} ({len(html):,} bytes) — {n_geo} aimags, "
          f"data week {weeks[-1] if weeks else '?'}, {'live' if live else 'CACHED'}")


if __name__ == "__main__":
    main()
