#!/usr/bin/env python3
"""
Membury PPG forecast: builds a plain HTML + CSS web page. The page contains no JavaScript.

How it works
  GitHub runs this script every hour (see .github/workflows/forecast.yml). It downloads the
  forecast from Open-Meteo, rates each daylight hour against the limits below, and writes a
  finished page to site/index.html, which GitHub Pages then publishes. Visitors' browsers
  only display the page, so it works on any phone or computer browser.

Usage
  python3 build.py                     fetch live data and write site/index.html
  python3 build.py --out page.html     choose a different output file
  python3 build.py --sample data.json  build from a saved Open-Meteo reply (for testing)
  python3 build.py --no-data           build a page with just the Windy map and links

Only the Python standard library is used, so there is nothing to install.
"""

import argparse
import html
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ===========================================================================
# 1. SETTINGS YOU CAN EDIT
#    Edit these on GitHub (pencil icon) and the page rebuilds within a minute or two.
# ===========================================================================
SITE = {
    "name": "Membury Airfield",
    "lat": 51.482432,
    "lon": -1.561366,
    "tz": "Europe/London",
}

# Flying limits. Every hour is checked against all of them.
LIMITS = {
    "max_wind": 10,           # mph, average wind at 10 m (ground level)
    "max_gust": 15,           # mph, strongest gust
    "max_spread": 5,          # mph, gust minus average wind (how gusty it is)
    "max_wind_100m": 20,      # mph, wind at 100 m (about 330 ft), where you'll be after take-off
    "max_rain_chance": 20,    # %, chance of rain in the hour; any forecast rain is also No-go
    "min_visibility_km": 5,   # km
    "max_cape": 300,          # J/kg, energy for showers and thunderstorms
}

OPTIONS = {
    "days": 7,
    # Weather models. For every hour the page uses the WORST value any of them gives
    # (highest wind, gust, rain; lowest visibility), so a spike in one model is never hidden.
    "models": ["ukmo_seamless", "ecmwf_ifs025", "gfs_seamless"],
    "amber_margin": 0.15,         # Marginal when within 15% of a limit
    "golden_hours": 2,            # hours after sunrise and before sunset marked with a sun
    "min_window_hours": 1,        # shortest run of Good hours counted as a flyable window
    "bar_scale_min_mph": 30,      # the hourly bars run from 0 to at least this speed
    "page_refresh_seconds": 1800, # browsers reload the page this often while it is open
}

MODEL_NAMES = {
    "ukmo_seamless": "Met Office (UKMO)",
    "ecmwf_ifs025": "ECMWF",
    "gfs_seamless": "GFS (NOAA)",
    None: "Open-Meteo best match",
}

# Trusted sources to check before flying. Edit freely: (title, address, what it is for).
LAT, LON = SITE["lat"], SITE["lon"]
LINKS = [
    ("Windy", f"https://www.windy.com/?{LAT:.3f},{LON:.3f},10",
     "Compare models (ECMWF, ICON, UKV) and look at wind at different heights."),
    ("Met Office forecast", "https://www.metoffice.gov.uk/weather/forecast/",
     "Official UK forecast. Search for Lambourn or Hungerford."),
    ("XC Weather", "https://xcweather.co.uk/",
     "Wind and gust forecasts plus live reports from nearby stations."),
    ("RASP UK", "https://rasp.stratus.org.uk/",
     "Free high-resolution soaring forecast: thermal strength, cloud base, boundary layer wind."),
    ("Meteo-parapente", "https://meteo-parapente.com/",
     "Free 2.5 km model built for paragliding, with wind at altitude and thermals."),
    ("Brize Norton METAR and TAF", "https://metar-taf.com/EGVN",
     "Latest actual weather report from the nearest reporting airfield, about 30 km away."),
    ("NOTAMs (NATS AIS)", "https://nats-uk.ead-it.com/",
     "Airspace restrictions and warnings for the day."),
]

# ===========================================================================
# 2. FETCH
# ===========================================================================
HOURLY = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "wind_speed_100m",
          "precipitation", "precipitation_probability", "cloud_cover", "visibility",
          "cape", "temperature_2m"]


def fetch(models):
    params = {
        "latitude": LAT, "longitude": LON,
        "hourly": ",".join(HOURLY), "daily": "sunrise,sunset",
        "timezone": SITE["tz"], "timeformat": "unixtime",
        "wind_speed_unit": "mph", "precipitation_unit": "mm",
        "forecast_days": OPTIONS["days"],
    }
    if models:
        params["models"] = ",".join(models)
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "membury-ppg-forecast (static page builder)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    if data.get("error"):
        raise RuntimeError(data.get("reason", "Open-Meteo returned an error"))
    return data


def fetch_with_fallback():
    """Try all models (3 attempts), then Open-Meteo's single best-match model."""
    last = None
    for attempt in range(3):
        try:
            return fetch(OPTIONS["models"]), OPTIONS["models"]
        except Exception as e:  # network error, timeout, bad reply
            last = e
            print(f"Multi-model request failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    try:
        return fetch(None), [None]
    except Exception as e:
        raise RuntimeError(f"All forecast requests failed. Last errors: {last}; {e}")


# ===========================================================================
# 3. COMBINE MODELS (worst case) AND RATE EACH HOUR
#    Only direct model outputs are used: no derived turbulence or thermal formulas.
# ===========================================================================
def ok(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def combine(data, models):
    H = data.get("hourly") or {}
    times = H.get("time")
    if not isinstance(times, list) or not times:
        raise RuntimeError("Forecast reply had no hourly data")

    def col(var, m):
        a = H.get(f"{var}_{m}" if m else var)
        return a if isinstance(a, list) else None

    def values(var, i):
        out = []
        for m in models:
            a = col(var, m)
            if a and i < len(a) and ok(a[i]):
                out.append((m, a[i]))
        return out

    used = set()
    hours = []
    for i, t in enumerate(times):
        wind = values("wind_speed_10m", i)
        for m, _ in wind:
            used.add(m)
        gust = values("wind_gusts_10m", i)
        w100 = values("wind_speed_100m", i)
        rain = values("precipitation", i)
        prob = values("precipitation_probability", i)
        cloud = values("cloud_cover", i)
        vis = values("visibility", i)
        cape = values("cape", i)
        temp = values("temperature_2m", i)
        # Direction is for information only: average the models' directions as vectors
        dirs = values("wind_direction_10m", i)
        direction = None
        if dirs:
            sx = sum(math.sin(math.radians(d)) for _, d in dirs)
            cx = sum(math.cos(math.radians(d)) for _, d in dirs)
            direction = (math.degrees(math.atan2(sx, cx)) + 360) % 360
        hi = lambda v: max(x for _, x in v) if v else None
        lo = lambda v: min(x for _, x in v) if v else None
        h = {
            "t": t,
            "wind": hi(wind), "gust": hi(gust), "wind100": hi(w100),
            "rain": hi(rain), "prob": hi(prob), "cloud": hi(cloud),
            "vis": lo(vis) / 1000 if vis else None, "cape": hi(cape),
            "temp": sum(x for _, x in temp) / len(temp) if temp else None,
            "dir": direction,
        }
        if ok(h["gust"]) and ok(h["wind"]) and h["gust"] < h["wind"]:
            h["gust"] = h["wind"]
        hours.append(h)

    # Sunrise and sunset. With several models the keys get a suffix (sunrise_ukmo_seamless).
    sun = {}
    D = data.get("daily") or {}
    for i, t in enumerate(D.get("time") or []):
        def pick(name):
            for k, a in D.items():
                if (k == name or k.startswith(name + "_")) and isinstance(a, list) and i < len(a) and ok(a[i]):
                    return a[i]
            return None
        rise, sset = pick("sunrise"), pick("sunset")
        if rise and sset:
            sun[local(t).date()] = (rise, sset)

    names = [MODEL_NAMES.get(m, m) for m in models if m in used]
    if not hours or not any(ok(h["wind"]) for h in hours):
        raise RuntimeError("Forecast reply had no wind data")
    return hours, sun, names


TZ = ZoneInfo(SITE["tz"])


def local(t):
    return datetime.fromtimestamp(t, TZ)


def rate(h):
    """0 Good, 1 Marginal, 2 No-go, with reasons in plain English (most serious first)."""
    m = OPTIONS["amber_margin"]
    reasons = []

    def fmt(v, unit, dec=0):
        return f"{v:.{dec}f} {unit}".strip()

    def over(v, lim, label, unit):
        if not ok(v):
            return
        dec = 1 if round(v) == round(lim) and v > lim else 0   # show 15.4, not "15 (limit 15)"
        if v > lim:
            reasons.append((2, f"{label} {fmt(v, unit, dec)} (limit {fmt(lim, unit)})"))
        elif v > lim * (1 - m):
            reasons.append((1, f"{label} {fmt(v, unit)} (near limit {fmt(lim, unit)})"))

    def under(v, lim, label, unit):
        if not ok(v):
            return
        if v < lim:
            reasons.append((2, f"{label} {fmt(v, unit, 1)} (minimum {fmt(lim, unit)})"))
        elif v < lim * (1 + m):
            reasons.append((1, f"{label} {fmt(v, unit, 1)} (near minimum {fmt(lim, unit)})"))

    L = LIMITS
    over(h["wind"], L["max_wind"], "Wind", "mph")
    over(h["gust"], L["max_gust"], "Gusts", "mph")
    if ok(h["gust"]) and ok(h["wind"]):
        over(h["gust"] - h["wind"], L["max_spread"], "Gusts above average wind by", "mph")
    over(h["wind100"], L["max_wind_100m"], "Wind at 100 m", "mph")
    over(h["prob"], L["max_rain_chance"], "Rain chance", "%")
    if ok(h["rain"]) and h["rain"] >= 0.1:
        reasons.append((2, f"Rain forecast ({h['rain']:.1f} mm)"))
    under(h["vis"], L["min_visibility_km"], "Visibility", "km")
    over(h["cape"], L["max_cape"], "Shower risk (CAPE)", "J/kg")
    if not ok(h["wind"]) or not ok(h["gust"]):
        reasons.append((1, "Wind forecast missing for this hour"))
    reasons.sort(key=lambda r: -r[0])
    return (max(r[0] for r in reasons) if reasons else 0), reasons


def build_days(hours, sun, now):
    days = {}
    for h in hours:
        d = local(h["t"]).date()
        if d not in sun:
            continue
        rise, sset = sun[d]
        if min(h["t"] + 3600, sset) - max(h["t"], rise) < 1800:   # at least 30 min of daylight
            continue
        if h["t"] + 3600 <= now:                                 # hour already over
            continue
        h["level"], h["reasons"] = rate(h)
        g = OPTIONS["golden_hours"] * 3600
        h["golden"] = h["t"] < rise + g or h["t"] + 3600 > sset - g
        days.setdefault(d, []).append(h)

    out = []
    for d in sorted(days):
        hrs, (rise, sset) = days[d], sun[d]
        wins, run = [], []

        def flush():
            if len(run) >= OPTIONS["min_window_hours"]:
                wins.append({"start": max(run[0]["t"], rise), "end": min(run[-1]["t"] + 3600, sset),
                             "hours": len(run), "golden": any(x["golden"] for x in run)})
        for h in hrs:
            if h["level"] == 0 and (not run or h["t"] - run[-1]["t"] == 3600):
                run.append(h)
            else:
                flush()
                run = [h] if h["level"] == 0 else []
        flush()
        best = max(wins, key=lambda w: w["hours"]) if wins else None
        level = 0 if best else (1 if any(h["level"] == 1 for h in hrs) else 2)
        out.append({"date": d, "hours": hrs, "rise": rise, "set": sset, "wins": wins, "best": best, "level": level})
    return out


# ===========================================================================
# 4. HTML
# ===========================================================================
LV = [("go", "✔", "Good"), ("warn", "⚠", "Marginal"), ("stop", "✖", "No-go")]
C16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
e = html.escape


def hm(t):
    return local(t).strftime("%H:%M")


def dur(sec):
    mins = int(round(sec / 60))
    h, m = divmod(mins, 60)
    return f"{h} h {m} min" if h and m else (f"{h} h" if h else f"{m} min")


def day_label(d, today):
    delta = (d - today).days
    return "Today" if delta == 0 else "Tomorrow" if delta == 1 else d.strftime("%A")


def r0(v):
    return "-" if not ok(v) else str(int(round(v)))


CSS = """
:root{--bg:#f4f7fa;--card:#fff;--text:#0b1620;--muted:#3d4f60;--line:#c9d4de;--track:#e3eaf0;
 --go:#0a7a3b;--go-bg:#d6f5e1;--warn:#8a5200;--warn-bg:#fff0bf;--stop:#b3150b;--stop-bg:#ffdcd7;
 --bar-go:#13a150;--bar-warn:#e09b00;--bar-stop:#e0402f;--lim:#0b1620;--accent:#0b5cad;--gold:#9a6200}
@media (prefers-color-scheme:dark){:root{--bg:#0b1622;--card:#132331;--text:#f2f7fb;--muted:#b4c4d2;--line:#2c4052;--track:#1e3243;
 --go:#5ee69a;--go-bg:#103a24;--warn:#ffc94d;--warn-bg:#3d2f0b;--stop:#ff8173;--stop-bg:#45170f;
 --bar-go:#3ddc84;--bar-warn:#ffc23d;--bar-stop:#ff6a5c;--lim:#f2f7fb;--accent:#8fd3ff;--gold:#ffd27a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:17px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:860px;margin:0 auto;padding:14px 14px 40px}
h1{font-size:1.6rem;margin:4px 0 0;line-height:1.15}
h2{font-size:1.25rem;margin:28px 0 8px}
p{margin:0}
a{color:var(--accent)}
.muted{color:var(--muted)} .small{font-size:.9rem}
.go{color:var(--go)} .warn{color:var(--warn)} .stop{color:var(--stop)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;margin:12px 0}
.banner{border-radius:16px;padding:16px 18px;margin:14px 0;font-size:1.15rem;font-weight:600;border:2px solid currentColor}
.banner.go{background:var(--go-bg)} .banner.stop{background:var(--stop-bg)}
.banner b{font-size:1.35rem;display:block;color:var(--text)}
.alert{border:2px solid var(--stop);background:var(--stop-bg);border-radius:14px;padding:12px 14px;margin:12px 0}
/* Week at a glance */
.glance{width:100%;border-collapse:separate;border-spacing:3px;font-size:.8rem;table-layout:fixed}
.glance th{font-weight:600;color:var(--muted);text-align:center;padding:0}
.glance th.d{text-align:left;width:4.2em;color:var(--text)}
.glance td{height:28px;border-radius:5px;text-align:center;font-weight:700;padding:0}
.c0{background:var(--bar-go);color:#03210f} .c1{background:var(--bar-warn);color:#2a1c00} .c2{background:var(--bar-stop);color:#2b0400} .cn{background:transparent}
/* Day cards */
details.day{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:12px 0;overflow:hidden}
details.day>summary{list-style:none;cursor:pointer;padding:14px 16px;display:grid;grid-template-columns:1fr auto;gap:2px 10px;min-height:64px}
details.day>summary::-webkit-details-marker{display:none}
.dname{font-size:1.2rem;font-weight:700}
.dname span{font-weight:500;color:var(--muted);font-size:1rem;margin-left:6px}
.badge{border-radius:999px;padding:4px 12px;font-weight:700;white-space:nowrap;align-self:start}
.badge.go{background:var(--bar-go);color:#03210f} .badge.warn{background:var(--bar-warn);color:#2a1c00} .badge.stop{background:var(--bar-stop);color:#2b0400}
.dwin{grid-column:1/-1;font-weight:600}
.dstat{grid-column:1/-1;color:var(--muted);font-size:.9rem}
.open{grid-column:1/-1;color:var(--accent);font-weight:600;font-size:.95rem}
details[open] .open .closed,details:not([open]) .open .opened{display:none}
.daybody{padding:0 16px 16px;border-top:1px solid var(--line)}
.key{font-size:.85rem;color:var(--muted);margin:10px 0 6px}
.key i{display:inline-block;width:18px;height:10px;border-radius:2px;vertical-align:middle;margin:0 4px 0 2px}
.key .kw{background:var(--bar-go)} .key .kg{background:var(--bar-go);opacity:.4}
.key .kl{width:2px;height:14px;background:var(--lim)} .key .kd{width:0;height:14px;border-left:2px dotted var(--lim);border-radius:0}
/* One row per hour */
ol.hours{list-style:none;margin:0;padding:0}
.row{display:grid;grid-template-columns:4.4rem minmax(0,1fr) auto;align-items:center;gap:4px 10px;padding:9px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}
.t{font-weight:700;font-size:1.05rem}
.t small{font-weight:400;color:var(--muted)}
.nums{font-size:1.05rem}
.nums b{font-size:1.25rem}
.nums .ic{font-weight:700;margin-right:4px}
.track{grid-column:2/-1;position:relative;height:22px;background:var(--track);border-radius:5px;overflow:hidden}
.track .g,.track .w{position:absolute;left:0;top:0;bottom:0}
.l0 .w,.l0 .g{background:var(--bar-go)} .l1 .w,.l1 .g{background:var(--bar-warn)} .l2 .w,.l2 .g{background:var(--bar-stop)}
.track .g{opacity:.4}
.track .lw,.track .lg{position:absolute;top:0;bottom:0;width:0}
.track .lw{border-left:2px solid var(--lim)}
.track .lg{border-left:2px dotted var(--lim)}
.p{text-align:right;color:var(--muted);font-size:.95rem;white-space:nowrap}
.why{grid-column:2/-1;font-size:.9rem;color:var(--text)}
.extra{grid-column:2/-1;font-size:.82rem;color:var(--muted)}
.sun{color:var(--gold)}
.scale{display:grid;grid-template-columns:4.4rem minmax(0,1fr);gap:0 10px;font-size:.78rem;color:var(--muted);margin-top:4px}
.scale .ax{position:relative;height:1.2em}
.scale .ax span{position:absolute;transform:translateX(-50%)}
.scale .ax span:first-child{transform:none}
.scale .ax span:last-child{transform:translateX(-100%)}
/* Windy */
.map{width:100%;height:520px;border:0;border-radius:12px;display:block;background:var(--track)}
@media (max-width:600px){.map{height:440px}}
ul.links{padding-left:20px;margin:6px 0}
ul.links li{margin:8px 0}
.disc{border:2px solid var(--warn);background:var(--warn-bg);border-radius:14px;padding:14px 16px;margin-top:24px}
.disc b{color:var(--warn)}
footer{margin-top:18px;font-size:.85rem;color:var(--muted)}
footer p{margin:4px 0}
"""


def windy_iframe():
    q = urllib.parse.urlencode({
        "lat": f"{LAT:.3f}", "lon": f"{LON:.3f}", "detailLat": f"{LAT:.3f}", "detailLon": f"{LON:.3f}",
        "zoom": 9, "level": "surface", "overlay": "wind", "product": "ecmwf", "menu": "", "message": "true",
        "marker": "true", "calendar": "now", "pressure": "", "type": "map", "location": "coordinates",
        "detail": "true", "metricWind": "mph", "metricTemp": "°C", "radarRange": "-1",
    })
    return (f'<iframe class="map" src="https://embed.windy.com/embed2.html?{e(q)}" title="Windy wind map and forecast for {e(SITE["name"])}" '
            f'loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>')


def links_html():
    return "<ul class=\"links\">" + "".join(
        f'<li><a href="{e(u)}" rel="noopener">{e(t)}</a>: {e(d)}</li>' for t, u, d in LINKS) + "</ul>"


def page(days, models, built, error=None, no_data=False):
    now_l = local(built)
    today = now_l.date()
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta http-equiv="refresh" content="{OPTIONS['page_refresh_seconds']}">
<meta name="description" content="Paramotor flying forecast for {e(SITE['name'])}">
<title>Membury PPG Forecast</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>Membury PPG forecast</h1>
<p class="muted small">{e(SITE['name'])}. All times UK local, speeds in mph.</p>""")

    if not no_data:
        parts.append(f"""<p class="small" style="margin-top:6px"><b>Updated {f"{now_l.strftime('%a')} {now_l.day} {now_l.strftime('%b, %H:%M')}"}</b>.
The page updates every hour. If this time is more than 2 hours old, the update has stopped.</p>""")
    if error:
        parts.append(f'<div class="alert"><b>Forecast unavailable.</b> {e(error)}</div>')

    if days:
        # Banner: next flyable window
        nxt = None
        for d in days:
            for w in d["wins"]:
                if w["end"] > built:
                    nxt = (d, w)
                    break
            if nxt:
                break
        if nxt:
            d, w = nxt
            parts.append(f'<div class="banner go">✔ Next flyable window<b>{day_label(d["date"], today)} {hm(w["start"])} to {hm(w["end"])}</b>'
                         f'<span class="small">{dur(w["end"] - w["start"])}{" ☀ golden hour" if w["golden"] else ""}</span></div>')
        else:
            parts.append(f'<div class="banner stop">✖ No flyable windows<b>in the next {len(days)} days</b></div>')

        # Week at a glance
        first = min(local(h["t"]).hour for d in days for h in d["hours"])
        last = max(local(h["t"]).hour for d in days for h in d["hours"])
        cols = list(range(first, last + 1))
        rows = []
        for d in days:
            by = {local(h["t"]).hour: h for h in d["hours"]}
            cells = "".join(
                (f'<td class="c{by[c]["level"]}" title="{c:02d}:00 {LV[by[c]["level"]][2]}">{LV[by[c]["level"]][1]}</td>' if c in by else '<td class="cn"></td>')
                for c in cols)
            lbl = day_label(d["date"], today)
            short = {"Today": "Today", "Tomorrow": "Tmrw"}.get(lbl, d["date"].strftime("%a"))
            rows.append(f'<tr><th class="d" scope="row">{short}</th>{cells}</tr>')
        head = "".join(f"<th>{c:02d}</th>" if (c - first) % 2 == 0 else "<th></th>" for c in cols)
        parts.append(f"""<h2>Week at a glance</h2>
<div class="card"><table class="glance"><thead><tr><th class="d"></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class="small muted" style="margin-top:6px">Daylight hours only. ✔ Good, ⚠ Marginal, ✖ No-go. Open a day below for the reasons.</p></div>""")

        # Day cards
        parts.append("<h2>Day by day</h2>")
        for k, d in enumerate(days):
            # Each day's bars run from 0 to at least 30 mph (more on windy days), in steps of 10
            day_max = max((h["gust"] or 0) for h in d["hours"])
            scale = min(60, max(OPTIONS["bar_scale_min_mph"], math.ceil(day_max / 10) * 10))
            pct = lambda v, sc=scale: 0 if not ok(v) else max(0.0, min(100.0, v / sc * 100))
            lw, lg = pct(LIMITS["max_wind"]), pct(LIMITS["max_gust"])
            ax = "".join(f'<span style="left:{t / scale * 100:.1f}%">{t}</span>' for t in range(0, scale + 1, 10))
            cls, icon, word = LV[d["level"]]
            label = day_label(d["date"], today)
            if d["best"]:
                b = d["best"]
                more = len(d["wins"]) - 1
                win = (f'<span class="go">✔ Window {hm(b["start"])} to {hm(b["end"])}</span> <span class="muted">({dur(b["end"] - b["start"])})</span>'
                       f'{" <span class=sun>☀</span>" if b["golden"] else ""}'
                       f'{" <span class=muted>Only one hour.</span>" if b["hours"] == 1 else ""}'
                       f'{f" <span class=muted>+{more} more</span>" if more > 0 else ""}')
            else:
                top = next((r[1] for h in d["hours"] for r in h["reasons"] if r[0] == d["level"]), "")
                win = f'<span class="{cls}">{icon} No flyable window</span> <span class="muted">{e(top)}</span>'
            mx_w = max((h["wind"] or 0) for h in d["hours"])
            mx_g = max((h["gust"] or 0) for h in d["hours"])
            probs = [h["prob"] for h in d["hours"] if ok(h["prob"])]
            stat = f"Wind up to {r0(mx_w)} mph, gusts up to {r0(mx_g)} mph" + (f", rain chance up to {r0(max(probs))}%" if probs else "")
            stat += f". Sunrise {hm(d['rise'])}, sunset {hm(d['set'])}."
            rows = []
            for h in d["hours"]:
                c, ic, wd = LV[h["level"]]
                lt = local(h["t"])
                dirtxt = C16[int(round(h["dir"] / 22.5)) % 16] if ok(h["dir"]) else ""
                why = "; ".join(r[1] for r in h["reasons"]) if h["level"] else ""
                rows.append(
                    f'<li class="row l{h["level"]}">'
                    f'<span class="t">{lt.hour:02d}<small>:00</small>{" <span class=sun>☀</span>" if h["golden"] else ""}</span>'
                    f'<span class="nums"><span class="ic {c}">{ic}<span class="sr"> {wd}:</span></span><b>{r0(h["wind"])}</b> mph, gusts <b>{r0(h["gust"])}</b></span>'
                    f'<span class="p">rain {r0(h["prob"])}%</span>'
                    f'<span class="track" aria-hidden="true"><span class="g" style="width:{pct(h["gust"]):.1f}%"></span><span class="w" style="width:{pct(h["wind"]):.1f}%"></span>'
                    f'<span class="lw" style="left:{lw:.1f}%"></span><span class="lg" style="left:{lg:.1f}%"></span></span>'
                    + (f'<span class="why"><span class="{c}">{ic}</span> {e(why)}</span>' if why else "")
                    + f'<span class="extra">From {dirtxt or "-"}. Wind at 100 m {r0(h["wind100"])} mph, cloud {r0(h["cloud"])}%, {r0(h["temp"])}°C</span>'
                    + "</li>")
            parts.append(f"""<details class="day"{" open" if k == 0 else ""}>
<summary><span class="dname">{e(label)}<span>{f"{d['date'].day} {d['date'].strftime('%b')}"}</span></span><span class="badge {cls}">{icon} {word}</span>
<span class="dwin">{win}</span><span class="dstat">{stat}</span>
<span class="open"><span class="closed">Show hours ▾</span><span class="opened">Hide hours ▴</span></span></summary>
<div class="daybody">
<p class="key"><i class="kw"></i>average wind <i class="kg"></i>gusts <i class="kl"></i>wind limit {LIMITS['max_wind']} <i class="kd"></i>gust limit {LIMITS['max_gust']}. Bar colour shows the hour's rating.</p>
<div class="scale" aria-hidden="true"><span>mph</span><span class="ax">{ax}</span></div>
<ol class="hours">{''.join(rows)}</ol>
</div></details>""")

        parts.append(f"""<p class="small muted">Forecast from {e(', '.join(models))}, via Open-Meteo. Each hour uses the worst value any model gives.
Limits: wind {LIMITS['max_wind']} mph, gusts {LIMITS['max_gust']} mph, gusts no more than {LIMITS['max_spread']} mph above the average,
wind at 100 m {LIMITS['max_wind_100m']} mph, rain chance {LIMITS['max_rain_chance']}% and no rain, visibility {LIMITS['min_visibility_km']} km,
shower energy (CAPE) {LIMITS['max_cape']} J/kg. Marginal means within {int(OPTIONS['amber_margin'] * 100)}% of a limit.</p>""")

    parts.append(f"""<h2>Live wind map</h2>
<div class="card" style="padding:8px">{windy_iframe()}
<p class="small muted" style="padding:8px 6px 2px">Map and point forecast by Windy (ECMWF model, switchable in the map).
It is Windy's own embedded viewer. If it doesn't appear, <a href="https://www.windy.com/?{LAT:.3f},{LON:.3f},10" rel="noopener">open it on Windy</a>.</p></div>
<h2>Check before you fly</h2>
<div class="card">{links_html()}</div>
<div class="disc"><b>⚠ This is an estimate, not a go/no-go decision.</b> The page compares weather model forecasts with your limits.
Models can be wrong, especially for gusts, showers and local effects. Check actual conditions on site, NOTAMs and airspace,
site rules and landowner or club requirements, and make your own decision as pilot in command.</div>
<footer><p>Forecast point {LAT}, {LON}. <a href="https://open-meteo.com/" rel="noopener">Weather data by Open-Meteo.com</a>
(CC BY 4.0). Map by <a href="https://www.windy.com/" rel="noopener">Windy.com</a>.</p></footer>
</main>
</body>
</html>
""")
    return "\n".join(parts)


# ===========================================================================
# 5. MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--sample", help="build from a saved Open-Meteo JSON reply")
    ap.add_argument("--sample-models", default=",".join(OPTIONS["models"]),
                    help="models in the sample reply, or 'none' for a single-model reply")
    ap.add_argument("--now", type=float, help="pretend the current time is this unix time (testing)")
    ap.add_argument("--no-data", action="store_true", help="only the Windy map and links")
    a = ap.parse_args()

    built = a.now or time.time()
    days, models, error = [], [], None
    if not a.no_data:
        try:
            if a.sample:
                with open(a.sample, encoding="utf-8") as f:
                    data = json.load(f)
                used = [None] if a.sample_models == "none" else a.sample_models.split(",")
            else:
                data, used = fetch_with_fallback()
            hours, sun, models = combine(data, used)
            days = build_days(hours, sun, built)
        except Exception as ex:
            # Fail the run so GitHub keeps the last good page instead of publishing an empty one
            print(f"Build failed: {ex}", file=sys.stderr)
            sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page(days, models, built, error=error, no_data=a.no_data))
    print(f"Wrote {a.out}: {len(days)} days, models: {', '.join(models) or 'none'}")


if __name__ == "__main__":
    main()
