#!/usr/bin/env python3
"""
Membury PPG forecast: builds plain HTML + CSS web pages. The pages contain no JavaScript.

How it works
  GitHub runs this script every hour (see .github/workflows/forecast.yml). It downloads the
  forecast from Open-Meteo, rates each daylight hour, and writes finished pages to site/,
  which GitHub Pages publishes. Browsers only display the pages.

  Because a page without JavaScript can't recalculate when you change a setting, the script
  writes one page for every combination of the wind, gust and gust-spread limits in CHOICES
  (for example w12-g18-s5.html). The buttons on the page link between them. index.html uses
  the default LIMITS.

Usage
  python3 build.py                        fetch live data, write site/
  python3 build.py --out-dir somewhere    choose the output folder
  python3 build.py --sample data.json     build from a saved Open-Meteo reply (testing)
  python3 build.py --no-data              one page with just the Windy map and links

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
from datetime import datetime
from zoneinfo import ZoneInfo

# ===========================================================================
# 1. SETTINGS YOU CAN EDIT
#    Edit on GitHub (pencil icon), commit, and the pages rebuild within a minute or two.
# ===========================================================================
SITE = {
    "name": "Membury Airfield",
    "lat": 51.482432,
    "lon": -1.561366,
    "tz": "Europe/London",
    # Terrain height in metres. None lets Open-Meteo use its 90 m elevation map for this point
    # (the page shows the height it used). Put a surveyed height here to override it.
    "elevation_m": None,
}

# Default limits, used by index.html.
LIMITS = {
    "max_wind": 10,            # mph, average wind at 10 m (forecast)
    "max_gust": 15,            # mph, strongest gust (forecast)
    "max_spread": 5,           # mph, gusts minus average wind (estimated)
    "max_wind_100m": 20,       # mph, wind at 100 m, about 330 ft (forecast)
    "max_shear": 12,           # mph, change in wind between 10 m and 100 m (estimated)
    "max_rain_chance": 20,     # %, chance of rain; any forecast rain is also No-go (forecast)
    "min_visibility_km": 5,    # km (forecast)
    "max_cape": 300,           # J/kg, energy for showers and thunderstorms (forecast)
    "min_cloud_base_ft": 1000, # ft above ground, when low cloud is likely (estimated)
}

# Limits the user can change on the web page. One page is built for every combination,
# so keep these lists short (5 x 5 x 3 = 75 pages).
CHOICES = {
    "max_wind": [6, 8, 10, 12, 14],
    "max_gust": [10, 12, 15, 18, 22],
    "max_spread": [3, 5, 8],
}

OPTIONS = {
    "days": 7,
    # For every hour the page uses the WORST value any model gives (highest wind, gusts, rain;
    # lowest visibility and cloud base), so a spike in one model is never averaged away.
    "models": ["ukmo_seamless", "ecmwf_ifs025", "gfs_seamless"],
    "amber_margin": 0.15,              # Marginal when within 15% of a limit
    "golden_hours": 2,                 # hours after sunrise and before sunset marked with a sun
    "min_window_hours": 1,             # shortest run of Good hours counted as a flyable window
    "gradient_alpha": 0.25,            # wind profile exponent above this = strong low-level gradient
    "gradient_min_increase_mph": 4,    # ...and the 100 m wind must be at least this much stronger
    "golden_shear_fraction": 0.6,      # golden-hour caution when shear reaches 60% of its limit
    "low_cloud_pct": 50,               # cloud base is only checked when low cloud is at least this
    "disagree_wind_mph": 5,            # models disagree if their winds differ by more than this...
    "disagree_wind_frac": 0.4,         # ...or 40% of the average wind, whichever is larger
    "disagree_gust_mph": 8,            # ...or their gusts differ by more than this
    "disagree_is_marginal": True,      # treat hours where models disagree as Marginal
    "bar_scale_min_mph": 30,           # hourly bars run from 0 to at least this speed
    "page_refresh_seconds": 1800,      # browsers reload the page this often while it is open
    "stale_hours": 3,                  # warn when the forecast is older than this
    "cache_max_age_hours": 24,         # if downloads fail, reuse a saved forecast up to this old
}

MODEL_NAMES = {"ukmo_seamless": "UKMO", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS", None: "Best match"}
MODEL_LONG = {"ukmo_seamless": "Met Office UKMO", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "NOAA GFS", None: "Open-Meteo best match"}

LAT, LON = SITE["lat"], SITE["lon"]
LINKS = [
    ("Windy", f"https://www.windy.com/?{LAT:.3f},{LON:.3f},10",
     "Compare models and look at wind at different heights."),
    ("Met Office forecast", "https://www.metoffice.gov.uk/weather/forecast/",
     "Official UK forecast. Search for Lambourn or Hungerford."),
    ("XC Weather", "https://xcweather.co.uk/", "Wind and gust forecasts plus live reports from nearby stations."),
    ("RASP UK", "https://rasp.stratus.org.uk/", "Free soaring forecast: thermals, cloud base, boundary layer wind."),
    ("Meteo-parapente", "https://meteo-parapente.com/", "Free 2.5 km model built for paragliding."),
    ("Brize Norton METAR and TAF", "https://metar-taf.com/EGVN",
     "Actual weather at the nearest reporting airfield, about 30 km away."),
    ("NOTAMs (NATS AIS)", "https://nats-uk.ead-it.com/", "Airspace restrictions and warnings for the day."),
]

# ===========================================================================
# 2. FETCH (with a saved copy for when Open-Meteo is unreachable)
# ===========================================================================
HOURLY = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "wind_speed_100m", "wind_direction_100m",
          "precipitation", "precipitation_probability", "cloud_cover", "cloud_cover_low", "visibility",
          "cape", "temperature_2m", "dew_point_2m"]
# Variables where a whole week of exact zeros means "not supplied", not a real forecast.
ZERO_MEANS_MISSING = {"wind_speed_10m", "wind_gusts_10m", "wind_speed_100m", "visibility", "precipitation_probability"}


def fetch(models):
    params = {
        "latitude": LAT, "longitude": LON, "hourly": ",".join(HOURLY), "daily": "sunrise,sunset",
        "timezone": SITE["tz"], "timeformat": "unixtime", "wind_speed_unit": "mph",
        "precipitation_unit": "mm", "forecast_days": OPTIONS["days"],
    }
    if SITE.get("elevation_m") is not None:
        params["elevation"] = SITE["elevation_m"]
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
    last = None
    for attempt in range(3):
        try:
            return fetch(OPTIONS["models"]), OPTIONS["models"]
        except Exception as ex:
            last = ex
            print(f"Multi-model request failed (attempt {attempt + 1}): {ex}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    try:
        return fetch(None), [None]
    except Exception as ex:
        raise RuntimeError(f"all forecast requests failed ({last}; {ex})")


# ===========================================================================
# 3. PREPARE EACH HOUR
#    Forecast values come straight from the models (worst case, with the model named).
#    Estimated values are calculated here and are always labelled "est." on the page.
# ===========================================================================
TZ = ZoneInfo(SITE["tz"])


def ok(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def local(t):
    return datetime.fromtimestamp(t, TZ)


def prepare(data, models):
    H = data.get("hourly") or {}
    times = H.get("time")
    if not isinstance(times, list) or not times:
        raise RuntimeError("the forecast reply had no hourly data")

    # Clean each model's series: a series that is missing, all empty, or (for some variables)
    # a whole week of exact zeros is treated as "not supplied", so it can't skew the worst case.
    series, missing = {}, {}
    for var in HOURLY:
        for m in models:
            a = H.get(f"{var}_{m}" if m else var)
            if isinstance(a, list):
                vals = [x for x in a if ok(x)]
                if not vals or (var in ZERO_MEANS_MISSING and all(x == 0 for x in vals)):
                    a = None
            else:
                a = None
            series[(var, m)] = a
            if a is None:
                missing.setdefault(m, []).append(var)

    keys = {"w10": "wind_speed_10m", "g": "wind_gusts_10m", "d10": "wind_direction_10m",
            "w100": "wind_speed_100m", "d100": "wind_direction_100m", "rain": "precipitation",
            "prob": "precipitation_probability", "cloud": "cloud_cover", "low": "cloud_cover_low",
            "vis": "visibility", "cape": "cape", "T": "temperature_2m", "Td": "dew_point_2m"}
    O = OPTIONS
    hours, used = [], set()
    for i, t in enumerate(times):
        per = {}
        for m in models:
            d = {}
            for k, var in keys.items():
                a = series[(var, m)]
                d[k] = a[i] if a is not None and i < len(a) and ok(a[i]) else None
            if ok(d["w10"]):
                used.add(m)
            # Estimated values for this model (labelled est. on the page)
            if ok(d["w10"]) and ok(d["w100"]) and ok(d["d10"]) and ok(d["d100"]):
                th = math.radians(d["d100"] - d["d10"])
                d["shear"] = math.sqrt(max(0.0, d["w100"] ** 2 + d["w10"] ** 2 - 2 * d["w100"] * d["w10"] * math.cos(th)))
            else:
                d["shear"] = None
            d["alpha"] = (math.log(d["w100"] / d["w10"]) / math.log(100 / 10)
                          if ok(d["w10"]) and ok(d["w100"]) and d["w10"] >= 3 and d["w100"] > 0 else None)
            d["base"] = 125 * (d["T"] - d["Td"]) * 3.281 if ok(d["T"]) and ok(d["Td"]) else None
            per[m] = d

        def worst(k, low=False, where=None):
            c = [(d[k], m) for m, d in per.items() if ok(d[k]) and (where is None or where(d))]
            if not c:
                return None, None
            return (min if low else max)(c, key=lambda x: x[0])

        h = {"t": t}
        h["wind"], h["wind_m"] = worst("w10")
        h["gust"], h["gust_m"] = worst("g")
        if ok(h["gust"]) and ok(h["wind"]) and h["gust"] < h["wind"]:
            h["gust"], h["gust_m"] = h["wind"], h["wind_m"]
        h["w100"], h["w100_m"] = worst("w100")
        h["rain"], h["rain_m"] = worst("rain")
        h["prob"], h["prob_m"] = worst("prob")
        h["cloud"], _ = worst("cloud")
        h["low"], _ = worst("low")
        vis, h["vis_m"] = worst("vis", low=True)
        h["vis"] = vis / 1000 if ok(vis) else None
        h["cape"], h["cape_m"] = worst("cape")
        temps = [d["T"] for d in per.values() if ok(d["T"])]
        h["temp"] = sum(temps) / len(temps) if temps else None
        h["shear"], h["shear_m"] = worst("shear")
        h["alpha"], h["alpha_m"] = worst("alpha")
        # Cloud base only matters where that model also forecasts plenty of low cloud
        h["base"], h["base_m"] = worst("base", low=True, where=lambda d: ok(d["low"]) and d["low"] >= O["low_cloud_pct"])
        h["base_any"], _ = worst("base", low=True)
        dm = per.get(h["wind_m"]) or {}
        h["dir"] = dm.get("d10") if ok(dm.get("d10")) else next((d["d10"] for d in per.values() if ok(d["d10"])), None)
        # Model disagreement
        ws = [(m, d["w10"]) for m, d in per.items() if ok(d["w10"])]
        gs = [d["g"] for d in per.values() if ok(d["g"])]
        h["disagree"] = None
        if len(ws) >= 2:
            vals = [v for _, v in ws]
            spread = max(vals) - min(vals)
            mean = sum(vals) / len(vals)
            gspread = max(gs) - min(gs) if len(gs) >= 2 else 0
            if spread > max(O["disagree_wind_mph"], O["disagree_wind_frac"] * mean) or gspread > O["disagree_gust_mph"]:
                h["disagree"] = "Models disagree on wind: " + ", ".join(f"{MODEL_NAMES[m]} {r0(v)}" for m, v in ws) + " mph"
        h["n_models"] = len(ws)
        hours.append(h)

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
    if not any(ok(h["wind"]) for h in hours):
        raise RuntimeError("the forecast reply had no wind data")
    info = {
        "models": [m for m in models if m in used],
        "missing": {m: v for m, v in missing.items() if m in used},
        "elevation": data.get("elevation"),
    }
    return hours, sun, info


# ===========================================================================
# 4. RATE HOURS AND FIND WINDOWS
# ===========================================================================
def r0(v):
    return "-" if not ok(v) else str(int(round(v)))


def rate(h, L, golden):
    """Return (level, reasons). level 0 Good, 1 Marginal, 2 No-go.
    reasons: (level, text, estimated?) with the most serious first."""
    m, O, R = OPTIONS["amber_margin"], OPTIONS, []

    def fmt(v, unit, dec=0):
        return f"{v:,.{dec}f}%" if unit == "%" else f"{v:,.{dec}f} {unit}".strip()

    def over(v, lim, label, unit, est=False):
        if not ok(v):
            return
        dec = 1 if round(v) == round(lim) and v > lim else 0
        if v > lim:
            R.append((2, f"{label} {fmt(v, unit, dec)} (limit {fmt(lim, unit)})", est))
        elif v > lim * (1 - m):
            R.append((1, f"{label} {fmt(v, unit)} (near limit {fmt(lim, unit)})", est))

    def under(v, lim, label, unit, est=False, dec=1):
        if not ok(v):
            return
        if v < lim:
            R.append((2, f"{label} {fmt(v, unit, dec)} (minimum {fmt(lim, unit)})", est))
        elif v < lim * (1 + m):
            R.append((1, f"{label} {fmt(v, unit, dec)} (near minimum {fmt(lim, unit)})", est))

    over(h["wind"], L["max_wind"], "Wind", "mph")
    over(h["gust"], L["max_gust"], "Gusts", "mph")
    if ok(h["gust"]) and ok(h["wind"]):
        over(h["gust"] - h["wind"], L["max_spread"], "Gust spread", "mph", est=True)
    over(h["w100"], L["max_wind_100m"], "Wind at 100 m", "mph")
    over(h["shear"], L["max_shear"], "Wind shear 10 to 100 m", "mph", est=True)
    climb = ok(h["alpha"]) and h["alpha"] > O["gradient_alpha"] and ok(h["w100"]) and ok(h["wind"]) \
        and h["w100"] - h["wind"] >= O["gradient_min_increase_mph"]
    if climb:
        lead = "☀ Golden hour, but watch the climb gradient" if golden else "Strong low-level gradient expected"
        R.append((1, f"{lead}: {r0(h['wind'])} mph at ground, {r0(h['w100'])} mph at 100 m", True))
    elif golden and ok(h["shear"]) and h["shear"] >= O["golden_shear_fraction"] * L["max_shear"] and h["shear"] <= L["max_shear"] * (1 - m):
        R.append((1, f"☀ Golden hour, but watch the climb gradient: wind changes by {r0(h['shear'])} mph between ground and 100 m", True))
    over(h["prob"], L["max_rain_chance"], "Rain chance", "%")
    if ok(h["rain"]) and h["rain"] >= 0.1:
        R.append((2, f"Rain forecast ({h['rain']:.1f} mm)", False))
    under(h["vis"], L["min_visibility_km"], "Visibility", "km")
    if ok(h["base"]):
        under(round(h["base"] / 100) * 100, L["min_cloud_base_ft"], "Cloud base about", "ft", est=True, dec=0)
    over(h["cape"], L["max_cape"], "Shower energy (CAPE)", "J/kg")
    if h["disagree"] and O["disagree_is_marginal"]:
        R.append((1, h["disagree"], False))
    if not ok(h["wind"]) or not ok(h["gust"]):
        R.append((1, "Wind forecast missing for this hour", False))
    R.sort(key=lambda r: -r[0])
    return (max(r[0] for r in R) if R else 0), R


def build_days(hours, sun, now, L):
    days = {}
    for base in hours:
        d = local(base["t"]).date()
        if d not in sun:
            continue
        rise, sset = sun[d]
        if min(base["t"] + 3600, sset) - max(base["t"], rise) < 1800 or base["t"] + 3600 <= now:
            continue
        g = OPTIONS["golden_hours"] * 3600
        h = dict(base)
        h["golden"] = h["t"] < rise + g or h["t"] + 3600 > sset - g
        h["level"], h["reasons"] = rate(h, L, h["golden"])
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
# 5. HTML
# ===========================================================================
LV = [("go", "✔", "Good"), ("warn", "⚠", "Marginal"), ("stop", "✖", "No-go")]
C16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
e = html.escape
EST = '<em class="est" title="Estimated: calculated by this page from model data, not a direct forecast or a measurement">est.</em>'


def hm(t):
    return local(t).strftime("%H:%M")


def dur(sec):
    mins = int(round(sec / 60))
    h, mm = divmod(mins, 60)
    return f"{h} h {mm} min" if h and mm else (f"{h} h" if h else f"{mm} min")


def day_label(d, today):
    delta = (d - today).days
    return "Today" if delta == 0 else "Tomorrow" if delta == 1 else d.strftime("%A")


def fname(L):
    return f"w{L['max_wind']}-g{L['max_gust']}-s{L['max_spread']}.html"


def mtag(m):
    """Small grey tag naming the model a value came from (none when only one model was used)."""
    return "" if m is None else f'<i class="mt">{e(MODEL_NAMES.get(m, str(m)))}</i>'


CSS = """
:root{--bg:#f4f7fa;--card:#fff;--text:#0b1620;--muted:#3d4f60;--line:#c9d4de;--track:#e3eaf0;--chip:#eef3f7;
 --go:#0a7a3b;--go-bg:#d6f5e1;--warn:#8a5200;--warn-bg:#fff0bf;--stop:#b3150b;--stop-bg:#ffdcd7;
 --bar-go:#13a150;--bar-warn:#e09b00;--bar-stop:#e0402f;--lim:#0b1620;--accent:#0b5cad;--gold:#9a6200;--est:#5b3fa8}
@media (prefers-color-scheme:dark){:root{--bg:#0b1622;--card:#132331;--text:#f2f7fb;--muted:#b4c4d2;--line:#2c4052;--track:#1e3243;--chip:#1b2e3e;
 --go:#5ee69a;--go-bg:#103a24;--warn:#ffc94d;--warn-bg:#3d2f0b;--stop:#ff8173;--stop-bg:#45170f;
 --bar-go:#3ddc84;--bar-warn:#ffc23d;--bar-stop:#ff6a5c;--lim:#f2f7fb;--accent:#8fd3ff;--gold:#ffd27a;--est:#c9b6ff}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:17px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:860px;margin:0 auto;padding:12px 14px 40px}
h1{font-size:1.5rem;margin:2px 0 0;line-height:1.15}
h2{font-size:1.25rem;margin:26px 0 8px}
p{margin:0}
a{color:var(--accent)}
.muted{color:var(--muted)} .small{font-size:.9rem}
.go{color:var(--go)} .warn{color:var(--warn)} .stop{color:var(--stop)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;margin:12px 0}
.stale{border:2px solid var(--stop);background:var(--stop-bg);border-radius:14px;padding:12px 14px;margin:10px 0;font-weight:600}
/* "est." label for values this page calculates */
em.est{font-style:normal;font-size:.72em;font-weight:700;letter-spacing:.02em;color:var(--est);border:1px solid currentColor;border-radius:5px;padding:0 .35em;margin-left:.35em;white-space:nowrap;vertical-align:.08em}
i.mt{font-style:normal;font-size:.75em;font-weight:600;color:var(--muted);background:var(--chip);border-radius:5px;padding:0 .35em;margin-left:.3em;white-space:nowrap}
/* Limits picker (works with the #limits link, no JavaScript) */
.limits{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:10px 14px;margin:10px 0}
.limsum{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}
.limsum b{font-size:1.1rem}
.btn{display:inline-block;min-height:40px;line-height:38px;padding:0 14px;border-radius:10px;border:1px solid var(--line);background:var(--chip);color:var(--text);text-decoration:none;font-weight:600}
.limpanel{display:none;margin-top:10px}
#limits:target .limpanel{display:block}
#limits:target .chg{display:none}
.optrow{margin:10px 0}
.optrow p{font-weight:600;margin-bottom:4px}
.opts{display:flex;flex-wrap:wrap;gap:6px}
.opts a{display:inline-block;min-width:52px;min-height:44px;line-height:42px;text-align:center;border-radius:10px;border:1px solid var(--line);background:var(--chip);color:var(--text);text-decoration:none;font-weight:700}
.opts a.on{background:var(--accent);border-color:var(--accent);color:var(--card)}
/* Windy */
.map{width:100%;height:520px;border:0;border-radius:12px;display:block;background:var(--track)}
@media (max-width:600px){.map{height:430px}}
.banner{border-radius:16px;padding:14px 16px;margin:14px 0;font-size:1.1rem;font-weight:600;border:2px solid currentColor}
.banner.go{background:var(--go-bg)} .banner.stop{background:var(--stop-bg)}
.banner b{font-size:1.35rem;display:block;color:var(--text)}
/* Week at a glance */
.glance{width:100%;border-collapse:separate;border-spacing:3px;font-size:.8rem;table-layout:fixed}
.glance th{font-weight:600;color:var(--muted);text-align:center;padding:0}
.glance th.d{text-align:left;width:4.2em;color:var(--text)}
.glance td{height:28px;border-radius:5px;text-align:center;font-weight:700;padding:0}
.c0{background:var(--bar-go);color:#03210f} .c1{background:var(--bar-warn);color:#2a1c00} .c2{background:var(--bar-stop);color:#2b0400} .cn{background:transparent}
/* How to read the bars */
.howto{margin:8px 0 4px}
.howto summary,.method summary{cursor:pointer;font-weight:700;min-height:44px;display:flex;align-items:center;list-style:none}
.howto summary::-webkit-details-marker,.method summary::-webkit-details-marker{display:none}
.howto summary::after,.method summary::after{content:"Show ▾";margin-left:auto;color:var(--accent);font-size:.9rem}
.howto[open] summary::after,.method[open] summary::after{content:"Hide ▴"}
.demo{display:grid;grid-template-columns:1fr;gap:8px;margin:6px 0 10px}
.demo ul{margin:0;padding-left:0;list-style:none;font-size:.92rem}
.demo li{margin:4px 0;display:flex;gap:8px;align-items:center}
.sw{display:inline-block;flex:none;width:26px;height:14px;border-radius:3px}
.sw.w{background:var(--bar-go)} .sw.g{background:var(--bar-go);opacity:.4}
.sw.lw{width:0;height:18px;border-left:2px solid var(--lim);border-radius:0;margin:0 12px}
.sw.lg{width:0;height:18px;border-left:2px dotted var(--lim);border-radius:0;margin:0 12px}
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
.key{font-size:.85rem;color:var(--muted);margin:10px 0 4px}
ol.hours{list-style:none;margin:0;padding:0}
.row{display:grid;grid-template-columns:4.4rem minmax(0,1fr) auto;align-items:center;gap:5px 10px;padding:10px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}
.t{font-weight:700;font-size:1.05rem}
.t small{font-weight:400;color:var(--muted)}
.nums{font-size:1.05rem}
.nums b{font-size:1.25rem}
.nums .ic{font-weight:700;margin-right:4px}
.p{text-align:right;color:var(--muted);font-size:.95rem;white-space:nowrap}
.track{grid-column:2/-1;position:relative;height:22px;background:var(--track);border-radius:5px;overflow:hidden}
.track .g,.track .w{position:absolute;left:0;top:0;bottom:0}
.l0 .w,.l0 .g{background:var(--bar-go)} .l1 .w,.l1 .g{background:var(--bar-warn)} .l2 .w,.l2 .g{background:var(--bar-stop)}
.track .g{opacity:.4}
.track .lw,.track .lg{position:absolute;top:0;bottom:0;width:0}
.track .lw{border-left:2px solid var(--lim)}
.track .lg{border-left:2px dotted var(--lim)}
.why{grid-column:2/-1;font-size:.9rem}
.why li{margin:2px 0}
.why ul{margin:0;padding:0;list-style:none}
details.more{grid-column:2/-1;font-size:.85rem}
details.more>summary{cursor:pointer;color:var(--accent);font-weight:600;min-height:32px;display:inline-flex;align-items:center;list-style:none}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::after{content:" ▾"}
details.more[open]>summary::after{content:" ▴"}
.chips{display:flex;flex-wrap:wrap;gap:4px 6px;font-size:.82rem;color:var(--muted);padding-bottom:2px}
.chips span{background:var(--chip);border-radius:6px;padding:1px 7px}
.sun{color:var(--gold)}
.scale{display:grid;grid-template-columns:4.4rem minmax(0,1fr);gap:0 10px;font-size:.78rem;color:var(--muted);margin-top:4px}
.scale .ax{position:relative;height:1.2em}
.scale .ax span{position:absolute;transform:translateX(-50%)}
.scale .ax span:first-child{transform:none}
.scale .ax span:last-child{transform:translateX(-100%)}
ul.links{padding-left:20px;margin:6px 0}
ul.links li{margin:8px 0}
.method{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:4px 16px;margin:12px 0}
.method h3{font-size:1rem;margin:14px 0 4px}
.method p{margin:0 0 8px;max-width:72ch;font-size:.94rem}
.method code{font-size:.88em;background:var(--chip);padding:1px 5px;border-radius:5px}
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


def limits_html(L):
    def row(key, title):
        opts = sorted(set(CHOICES[key]) | {LIMITS[key]})
        links = []
        for v in opts:
            target = dict(L, **{key: v})
            on = v == L[key]
            links.append(f'<a href="{fname(target)}#limits" class="{"on" if on else ""}"{" aria-current=page" if on else ""}>{v}</a>')
        return f'<div class="optrow"><p>{title}</p><div class="opts">{"".join(links)}</div></div>'
    return f"""<section class="limits" id="limits" aria-label="Your limits">
<div class="limsum"><span>Your limits: wind <b>{L['max_wind']}</b>, gusts <b>{L['max_gust']}</b>, gust spread <b>{L['max_spread']}</b> mph</span>
<a class="btn chg" href="#limits">Change limits</a></div>
<div class="limpanel">
{row('max_wind', 'Max average wind (mph)')}
{row('max_gust', 'Max gusts (mph)')}
{row('max_spread', 'Max gust spread (mph): how far gusts may rise above the average wind')}
<p class="small muted">Each choice opens a page already worked out for those limits. Bookmark it or add it to your home screen to keep them.
The other limits (wind at 100 m {LIMITS['max_wind_100m']} mph, shear {LIMITS['max_shear']} mph, rain chance {LIMITS['max_rain_chance']}%,
visibility {LIMITS['min_visibility_km']} km, cloud base {LIMITS['min_cloud_base_ft']:,} ft, CAPE {LIMITS['max_cape']} J/kg) are set in build.py.</p>
<p style="margin-top:8px"><a class="btn" href="{fname(L)}">Done</a></p>
</div></section>"""


def howto_html(L):
    return f"""<details class="howto card"><summary>How to read the bars and labels</summary>
<div class="demo">
<div class="row l0" style="border:0;padding:0"><span class="t">10<small>:00</small></span><span class="nums"><span class="ic go">✔</span><b>6</b> mph, gusts <b>10</b></span><span class="p">rain 10%</span>
<span class="track" aria-hidden="true"><span class="g" style="width:33%"></span><span class="w" style="width:20%"></span><span class="lw" style="left:{L['max_wind'] / 30 * 100:.0f}%"></span><span class="lg" style="left:{min(100, L['max_gust'] / 30 * 100):.0f}%"></span></span></div>
<ul>
<li><span class="sw w"></span>Solid part: the average wind (forecast).</li>
<li><span class="sw g"></span>Lighter part: how far the gusts reach (forecast).</li>
<li><span class="sw lw"></span>Solid line: your wind limit ({L['max_wind']} mph). If the solid part passes it, it's too windy.</li>
<li><span class="sw lg"></span>Dotted line: your gust limit ({L['max_gust']} mph). If the lighter part passes it, it's too gusty.</li>
<li><span class="sw w" style="background:var(--bar-warn)"></span>Colour: the hour's rating. Green Good, amber Marginal, red No-go.</li>
</ul>
<p class="small">Labels: a grey tag such as <i class="mt">GFS</i> names the model that gave the worst (used) value.
{EST} means <b>estimated</b>: worked out by this page from model data. It is not a forecast any model makes and not a measurement.
<b>?</b> means the models disagree, so the forecast is less certain.</p>
</div></details>"""


def method_html():
    L, O = LIMITS, OPTIONS
    return f"""<details class="method"><summary>How the ratings are worked out</summary>
<h3>Forecast values</h3>
<p>Wind, gusts, wind at 100 m, rain, rain chance, cloud, visibility, CAPE and temperature come straight from the models via Open-Meteo.
For each hour the page uses the worst value any model gives and names that model. A model that does not supply a value
(for example UKMO has no 100 m wind or rain chance) is left out of that value, and a week of exact zeros is treated as missing,
so it can't drag the worst case down.</p>
<h3>Estimated values {EST}</h3>
<p><b>Gust spread</b> = gusts minus average wind.</p>
<p><b>Wind shear</b> between 10 m and 100 m, using both speed and direction: <code>√(V100² + V10² − 2·V100·V10·cos θ)</code>, where θ is the
difference in direction. Worked out for each model; the highest is used.</p>
<p><b>Wind gradient</b>: <code>α = ln(V100 / V10) / ln(100 / 10)</code>. Above {O['gradient_alpha']}, with at least {O['gradient_min_increase_mph']} mph more
wind at 100 m, the hour is Marginal: "Strong low-level gradient expected". This is common in calm air around dawn and dusk.</p>
<p><b>Cloud base</b> (Espy's rule): <code>125 × (temperature − dew point) × 3.281</code> feet. Only checked when that model forecasts at least
{O['low_cloud_pct']}% low cloud. Below {L['min_cloud_base_ft']:,} ft is No-go. The rule fits cumulus best and can be wrong for layered cloud.</p>
<p>These estimates use model winds at only two heights and simple rules of thumb. Treat them as warnings to look more closely, not as precise numbers.</p>
<h3>Model disagreement</h3>
<p>When the models' winds differ by more than {O['disagree_wind_mph']} mph (or {int(O['disagree_wind_frac'] * 100)}% of the average), or their gusts by
more than {O['disagree_gust_mph']} mph, the hour shows <b>?</b>{" and is Marginal" if O['disagree_is_marginal'] else ""}.</p>
<h3>Ratings</h3>
<p>Good: every limit met. Marginal: within {int(O['amber_margin'] * 100)}% of a limit, or a caution above. No-go: any limit broken, or any rain forecast.
Daylight hours only. A flyable window is at least {O['min_window_hours']} Good hour{"s" if O['min_window_hours'] > 1 else ""} in a row.</p>
</details>"""


def page(days, info, L, built, fetched=None, stale_reason=None, no_data=False):
    now_l = local(built)
    today = now_l.date()
    P = []
    P.append(f"""<!DOCTYPE html>
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
<p class="muted small">{e(SITE['name'])}. UK local time, speeds in mph.</p>""")
    if not no_data:
        f_l = local(fetched or built)
        P.append(f'<p class="small" style="margin-top:4px"><b>Forecast downloaded {f_l.strftime("%a")} {f_l.day} {f_l.strftime("%b, %H:%M")}</b>. '
                 f'Updates every hour; if this is more than {OPTIONS["stale_hours"]} hours ago, updates have stopped.</p>')
        age_h = (built - (fetched or built)) / 3600
        if stale_reason or age_h > OPTIONS["stale_hours"]:
            P.append(f'<div class="stale" role="alert">⚠ Forecast not updated. The last download attempt failed at {hm(built)}'
                     f'{" (" + e(stale_reason) + ")" if stale_reason else ""}. This forecast is {dur(built - fetched)} old.</div>')
        P.append(limits_html(L))

    P.append(f"""<h2 style="margin-top:14px">Live wind map</h2>
<div class="card" style="padding:8px">{windy_iframe()}
<p class="small muted" style="padding:8px 6px 2px">Windy's own map and point forecast (ECMWF model, switchable in the map).
If it doesn't appear, <a href="https://www.windy.com/?{LAT:.3f},{LON:.3f},10" rel="noopener">open it on Windy</a>.</p></div>""")

    if days:
        nxt = None
        for d in days:
            w = next((w for w in d["wins"] if w["end"] > built), None)
            if w:
                nxt = (d, w)
                break
        if nxt:
            d, w = nxt
            P.append(f'<div class="banner go">✔ Next flyable window for your limits<b>{day_label(d["date"], today)} {hm(w["start"])} to {hm(w["end"])}</b>'
                     f'<span class="small">{dur(w["end"] - w["start"])}{" ☀ golden hour" if w["golden"] else ""}</span></div>')
        else:
            P.append(f'<div class="banner stop">✖ No flyable windows for your limits<b>in the next {len(days)} days</b></div>')

        first = min(local(h["t"]).hour for d in days for h in d["hours"])
        last = max(local(h["t"]).hour for d in days for h in d["hours"])
        cols = list(range(first, last + 1))
        rows = []
        for d in days:
            by = {local(h["t"]).hour: h for h in d["hours"]}
            cells = "".join(
                (f'<td class="c{by[c]["level"]}" title="{c:02d}:00 {LV[by[c]["level"]][2]}">{LV[by[c]["level"]][1]}{"?" if by[c]["disagree"] else ""}</td>'
                 if c in by else '<td class="cn"></td>') for c in cols)
            lbl = day_label(d["date"], today)
            short = {"Today": "Today", "Tomorrow": "Tmrw"}.get(lbl, d["date"].strftime("%a"))
            rows.append(f'<tr><th class="d" scope="row">{short}</th>{cells}</tr>')
        head = "".join(f"<th>{c:02d}</th>" if (c - first) % 2 == 0 else "<th></th>" for c in cols)
        P.append(f"""<h2>Day by day</h2>
<div class="card"><table class="glance"><thead><tr><th class="d"></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p class="small muted" style="margin-top:6px">Week at a glance, daylight hours only. ✔ Good, ⚠ Marginal, ✖ No-go, ? models disagree.</p></div>
{howto_html(L)}""")

        for k, d in enumerate(days):
            cls, icon, word = LV[d["level"]]
            label = day_label(d["date"], today)
            if d["best"]:
                b = d["best"]
                more = len(d["wins"]) - 1
                win = (f'<span class="go">✔ Window {hm(b["start"])} to {hm(b["end"])}</span> <span class="muted">({dur(b["end"] - b["start"])})</span>'
                       + (' <span class="sun">☀</span>' if b["golden"] else "")
                       + (' <span class="muted">Only one hour.</span>' if b["hours"] == 1 else "")
                       + (f' <span class="muted">+{more} more</span>' if more > 0 else ""))
            else:
                top = next((r for h in d["hours"] for r in h["reasons"] if r[0] == d["level"]), None)
                win = f'<span class="{cls}">{icon} No flyable window</span>' + (f' <span class="muted">{e(top[1])}{EST if top[2] else ""}</span>' if top else "")
            mx_w = max((h["wind"] or 0) for h in d["hours"])
            mx_g = max((h["gust"] or 0) for h in d["hours"])
            probs = [h["prob"] for h in d["hours"] if ok(h["prob"])]
            stat = f"Wind up to {r0(mx_w)} mph, gusts up to {r0(mx_g)} mph" + (f", rain chance up to {r0(max(probs))}%" if probs else "")
            stat += f". Sunrise {hm(d['rise'])}, sunset {hm(d['set'])}."
            day_max = max((h["gust"] or 0) for h in d["hours"])
            scale = min(60, max(OPTIONS["bar_scale_min_mph"], math.ceil(day_max / 10) * 10))
            pct = lambda v, sc=scale: 0 if not ok(v) else max(0.0, min(100.0, v / sc * 100))
            lw, lg = pct(L["max_wind"]), pct(L["max_gust"])
            ax = "".join(f'<span style="left:{t / scale * 100:.1f}%">{t}</span>' for t in range(0, scale + 1, 10))
            rows = []
            for h in d["hours"]:
                c, ic, wd = LV[h["level"]]
                lt = local(h["t"])
                dirtxt = C16[int(round(h["dir"] / 22.5)) % 16] if ok(h["dir"]) else "-"
                multi = h["n_models"] > 1
                why = ""
                if h["level"]:
                    why = '<div class="why"><ul>' + "".join(
                        f'<li><span class="{LV[r[0]][0]}">{LV[r[0]][1]}</span> {e(r[1])}{EST if r[2] else ""}</li>' for r in h["reasons"]) + "</ul></div>"
                chips = [f'<span>From {dirtxt}</span>']
                if multi:
                    src = (f'Wind and gusts from{mtag(h["wind_m"])}' if h["wind_m"] == h["gust_m"]
                           else f'Wind from{mtag(h["wind_m"])} gusts from{mtag(h["gust_m"])}')
                    chips.append(f'<span>{src}</span>')
                chips.append(f'<span>100 m: {r0(h["w100"])} mph{mtag(h["w100_m"])}</span>')
                if ok(h["gust"]) and ok(h["wind"]):
                    chips.append(f'<span>Spread {r0(h["gust"] - h["wind"])} mph{EST}</span>')
                if ok(h["shear"]):
                    chips.append(f'<span>Shear {r0(h["shear"])} mph{EST}</span>')
                if ok(h["alpha"]):
                    chips.append(f'<span>Gradient α {h["alpha"]:.2f}{EST}</span>')
                if ok(h["base_any"]):
                    chips.append(f'<span>Cloud base ~{round(h["base_any"] / 100) * 100:,} ft{EST}, low cloud {r0(h["low"])}%</span>')
                chips.append(f'<span>Cloud {r0(h["cloud"])}%, {r0(h["temp"])}°C</span>')
                rows.append(
                    f'<li class="row l{h["level"]}">'
                    f'<span class="t">{lt.hour:02d}<small>:00</small>{" <span class=sun>☀</span>" if h["golden"] else ""}</span>'
                    f'<span class="nums"><span class="ic {c}">{ic}{"?" if h["disagree"] else ""}<span class="sr"> {wd}:</span></span><b>{r0(h["wind"])}</b> mph, gusts <b>{r0(h["gust"])}</b></span>'
                    f'<span class="p">rain {r0(h["prob"])}%</span>'
                    f'<span class="track" aria-hidden="true"><span class="g" style="width:{pct(h["gust"]):.1f}%"></span><span class="w" style="width:{pct(h["wind"]):.1f}%"></span>'
                    f'<span class="lw" style="left:{lw:.1f}%"></span><span class="lg" style="left:{lg:.1f}%"></span></span>'
                    + why + f'<details class="more"><summary>More for {lt.hour:02d}:00</summary><div class="chips">{"".join(chips)}</div></details></li>')
            P.append(f"""<details class="day"{" open" if k == 0 else ""}>
<summary><span class="dname">{e(label)}<span>{d['date'].day} {d['date'].strftime('%b')}</span></span><span class="badge {cls}">{icon} {word}</span>
<span class="dwin">{win}</span><span class="dstat">{stat}</span>
<span class="open"><span class="closed">Show hours ▾</span><span class="opened">Hide hours ▴</span></span></summary>
<div class="daybody">
<p class="key">Bars: solid = average wind, light = gusts, solid line = wind limit {L['max_wind']}, dotted line = gust limit {L['max_gust']} mph.</p>
<div class="scale" aria-hidden="true"><span>mph</span><span class="ax">{ax}</span></div>
<ol class="hours">{''.join(rows)}</ol>
</div></details>""")

        miss = []
        for m, vars_ in info["missing"].items():
            nice = {"wind_speed_100m": "100 m wind", "wind_direction_100m": "100 m direction", "precipitation_probability": "rain chance",
                    "cloud_cover_low": "low cloud", "visibility": "visibility", "cape": "CAPE", "dew_point_2m": "dew point",
                    "wind_gusts_10m": "gusts", "wind_speed_10m": "wind"}
            items = sorted({nice.get(v, v.replace("_", " ")) for v in vars_})
            if items:
                miss.append(f"{MODEL_LONG.get(m, m)} does not supply {', '.join(items)}")
        elev = info.get("elevation")
        P.append(f"""<p class="small muted">Forecast from {e(', '.join(MODEL_LONG.get(m, str(m)) for m in info['models']))} via Open-Meteo, worst value each hour.
{e('. '.join(miss) + '. ' if miss else '')}{f'Terrain height used for this point: {elev:.0f} m.' if ok(elev) else ''}</p>""")

    P.append(f"""<h2>Check before you fly</h2>
<div class="card"><ul class="links">{''.join(f'<li><a href="{e(u)}" rel="noopener">{e(t)}</a>: {e(dd)}</li>' for t, u, dd in LINKS)}</ul></div>
{method_html() if days else ''}
<div class="disc"><b>⚠ This is an estimate, not a go/no-go decision.</b> The page compares weather model forecasts, and some values it
estimates from them, with your limits. Models can be wrong, especially for gusts, showers and local effects. Check actual conditions on
site, NOTAMs and airspace, site rules and landowner or club requirements, and make your own decision as pilot in command.</div>
<footer><p>Forecast point {LAT}, {LON}. <a href="https://open-meteo.com/" rel="noopener">Weather data by Open-Meteo.com</a> (CC BY 4.0).
Map by <a href="https://www.windy.com/" rel="noopener">Windy.com</a>.</p></footer>
</main>
</body>
</html>
""")
    return "\n".join(P)


# ===========================================================================
# 6. MAIN
# ===========================================================================
def all_limit_sets():
    sets = []
    for w in sorted(set(CHOICES["max_wind"]) | {LIMITS["max_wind"]}):
        for g in sorted(set(CHOICES["max_gust"]) | {LIMITS["max_gust"]}):
            for s in sorted(set(CHOICES["max_spread"]) | {LIMITS["max_spread"]}):
                sets.append(dict(LIMITS, max_wind=w, max_gust=g, max_spread=s))
    return sets


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="site")
    ap.add_argument("--cache-dir", default="data", help="where the last good forecast is kept")
    ap.add_argument("--sample", help="build from a saved Open-Meteo JSON reply")
    ap.add_argument("--sample-models", default=",".join(OPTIONS["models"]), help="models in the sample, or 'none'")
    ap.add_argument("--now", type=float, help="pretend the current time is this unix time (testing)")
    ap.add_argument("--no-data", action="store_true", help="one page with only the Windy map and links")
    a = ap.parse_args()

    built = a.now or time.time()
    os.makedirs(a.out_dir, exist_ok=True)
    if a.no_data:
        with open(os.path.join(a.out_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(page([], {}, LIMITS, built, no_data=True))
        print("Wrote map-and-links page")
        return

    stale_reason, fetched = None, built
    cache_file = os.path.join(a.cache_dir, "last.json")
    if a.sample:
        with open(a.sample, encoding="utf-8") as f:
            data = json.load(f)
        used = [None] if a.sample_models == "none" else a.sample_models.split(",")
    else:
        try:
            data, used = fetch_with_fallback()
            os.makedirs(a.cache_dir, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"fetched": built, "models": used, "data": data}, f)
        except Exception as ex:
            cached = None
            try:
                with open(cache_file, encoding="utf-8") as f:
                    cached = json.load(f)
            except Exception:
                pass
            if not cached or built - cached["fetched"] > OPTIONS["cache_max_age_hours"] * 3600:
                print(f"Build failed and no recent saved forecast: {ex}", file=sys.stderr)
                sys.exit(1)   # GitHub keeps the previous pages
            data, used, fetched = cached["data"], cached["models"], cached["fetched"]
            stale_reason = "Open-Meteo could not be reached"
            print(f"Download failed ({ex}); using the forecast saved at {hm(fetched)}", file=sys.stderr)

    try:
        hours, sun, info = prepare(data, used)
    except Exception as ex:
        print(f"Build failed: {ex}", file=sys.stderr)
        sys.exit(1)

    count = 0
    for L in all_limit_sets():
        html_text = page(build_days(hours, sun, built, L), info, L, built, fetched, stale_reason)
        with open(os.path.join(a.out_dir, fname(L)), "w", encoding="utf-8") as f:
            f.write(html_text)
        if all(L[k] == LIMITS[k] for k in CHOICES):
            with open(os.path.join(a.out_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(html_text)
        count += 1
    print(f"Wrote {count} pages plus index.html. Models: {', '.join(MODEL_NAMES.get(m, str(m)) for m in info['models'])}")


if __name__ == "__main__":
    main()
