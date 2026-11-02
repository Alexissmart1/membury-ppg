#!/usr/bin/env python3
"""
Membury PPG forecast: builds plain HTML + CSS web pages. The pages contain no JavaScript.

How it works
  GitHub runs this script every hour (see .github/workflows/forecast.yml). It downloads the
  forecast from Open-Meteo, rates each daylight hour, and writes finished pages to site/,
  which GitHub Pages publishes. Browsers only display the pages.

  A page without JavaScript can't recalculate when you change a setting, so the script writes
  one page for every combination of the wind, gust and gust-spread limits in CHOICES
  (for example w12-g18-s5.html). The buttons on the page link between them. index.html uses
  the default LIMITS.

Usage
  python3 build.py                          fetch live data, write site/
  python3 build.py --out-dir somewhere      choose the output folder
  python3 build.py --sample f.json --sample-ens e.json   build from saved replies (testing)
  python3 build.py --no-data                one page with just the Windy map and links

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
    "max_rain_chance": 20,     # %, chance of rain from the ensemble; forecast rain is also No-go
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
    # Models downloaded. Like Windy and Windfinder, each hour's figures come from ONE lead model,
    # with wind and gusts always taken together from it. The others are used for comparison only.
    "models": ["ukmo_seamless", "ecmwf_ifs025", "gfs_seamless"],
    # Lead model: the Met Office model (2 km UKV over the UK) for the first hours, then ECMWF.
    # If the lead model has no value for something, the next model in the list fills the gap.
    "lead_short": ["ukmo_seamless", "ecmwf_ifs025", "gfs_seamless"],
    "lead_short_hours": 48,
    "lead_long": ["ecmwf_ifs025", "ukmo_seamless", "gfs_seamless"],
    # A model's gust is treated as suspect (left out of the disagreement check) if it is more than
    # this many times its own average wind AND at least gust_suspect_extra_mph above it.
    "gust_suspect_ratio": 2.5,
    "gust_suspect_extra_mph": 12,
    # Rain chance: share of ensemble runs with rain within an hour either side.
    # The first model that answers is used.
    "rain_ensembles": ["ecmwf_ifs025", "gfs_seamless"],
    "rain_threshold_mm": 0.1,
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
    "disagree_is_marginal": False,     # True = hours where models disagree are at least Marginal
    "bar_scale_min_mph": 30,           # hourly bars run from 0 to at least this speed
    "page_refresh_seconds": 1800,      # browsers reload the page this often while it is open
    "stale_hours": 3,                  # warn when the forecast is older than this
    "cache_max_age_hours": 24,         # if downloads fail, reuse a saved forecast up to this old
}

BUILD_VERSION = "2026-11-02c (lead model + gust safeguard)"
MODEL_NAMES = {"ukmo_seamless": "UKMO", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS", None: "Best match"}
MODEL_LONG = {"ukmo_seamless": "Met Office UKMO", "ecmwf_ifs025": "ECMWF", "gfs_seamless": "NOAA GFS", None: "Open-Meteo best match"}

LAT, LON = SITE["lat"], SITE["lon"]
# Links open at (or as near as the site allows to) Membury. (title, address, what it is for)
LINKS = [
    ("Windy", f"https://www.windy.com/{LAT:.3f}/{LON:.3f}?gust,surface,{LAT:.3f},{LON:.3f},10",
     "Point forecast for the airfield on a gust map. Switch models and heights in the menus."),
    ("Windfinder: Membury Airfield", "https://en.windfinder.com/forecast/membury_airfield",
     "Wind and gust forecast for the airfield itself."),
    ("Met Office: Lambourn", "https://www.metoffice.gov.uk/weather/forecast/gcnvj0v16",
     "Official forecast for Lambourn, about 5 km from the airfield."),
    ("RASP UK", "https://rasp.stratus.org.uk/index.php/rasptable-desktop",
     "Soaring forecast for the UK: thermals, cloud base, boundary layer wind. Find Membury on the map."),
    ("Meteo-parapente", "https://meteo-parapente.com/",
     "2.5 km model built for paragliding. Opens the general map; zoom to Membury."),
    ("Brize Norton METAR and TAF", "https://en.allmetsat.com/metar-taf/england-wales.php?icao=EGVN",
     "Actual weather at the nearest reporting airfield, about 30 km away."),
    ("NOTAMinfo", "https://notaminfo.com/",
     "Free map of UK NOTAMs and airspace warnings, made for sport pilots."),
]

# ===========================================================================
# 2. FETCH (with a saved copy for when Open-Meteo is unreachable)
# ===========================================================================
HOURLY = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "wind_speed_100m", "wind_direction_100m",
          "precipitation", "cloud_cover", "cloud_cover_low", "visibility", "cape", "temperature_2m", "dew_point_2m"]
# Variables where a whole week of exact zeros means "not supplied", not a real forecast.
ZERO_MEANS_MISSING = {"wind_speed_10m", "wind_gusts_10m", "wind_speed_100m", "visibility"}


def get_json(base, params):
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "membury-ppg-forecast (static page builder)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    if data.get("error"):
        raise RuntimeError(data.get("reason", "Open-Meteo returned an error"))
    return data


def common_params():
    p = {"latitude": LAT, "longitude": LON, "timezone": SITE["tz"], "timeformat": "unixtime",
         "forecast_days": OPTIONS["days"]}
    if SITE.get("elevation_m") is not None:
        p["elevation"] = SITE["elevation_m"]
    return p


def fetch_forecast(models):
    p = dict(common_params(), hourly=",".join(HOURLY), daily="sunrise,sunset",
             wind_speed_unit="mph", precipitation_unit="mm")
    if models:
        p["models"] = ",".join(models)
    return get_json("https://api.open-meteo.com/v1/forecast", p)


def fetch_forecast_with_fallback():
    last = None
    for attempt in range(3):
        try:
            return fetch_forecast(OPTIONS["models"]), OPTIONS["models"]
        except Exception as ex:
            last = ex
            print(f"Multi-model request failed (attempt {attempt + 1}): {ex}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    try:
        return fetch_forecast(None), [None]
    except Exception as ex:
        raise RuntimeError(f"all forecast requests failed ({last}; {ex})")


def fetch_ensemble():
    """Rain from an ensemble (many runs of one model). Returns (reply, model) or (None, None)."""
    for m in OPTIONS["rain_ensembles"]:
        try:
            return get_json("https://ensemble-api.open-meteo.com/v1/ensemble",
                            dict(common_params(), hourly="precipitation", models=m, precipitation_unit="mm")), m
        except Exception as ex:
            print(f"Ensemble request for {m} failed: {ex}", file=sys.stderr)
    return None, None


def rain_chance(ens):
    """Map unix time -> % of ensemble runs with rain (>= threshold) in that hour or the hour either side."""
    if not ens:
        return {}, 0
    H = ens.get("hourly") or {}
    times = H.get("time") or []
    members = [a for k, a in H.items()
               if k != "time" and k.startswith("precipitation") and isinstance(a, list) and any(ok(x) for x in a)]
    if not members:
        return {}, 0
    thr, out = OPTIONS["rain_threshold_mm"], {}
    for i, t in enumerate(times):
        wet = valid = 0
        for a in members:
            win = [a[j] for j in (i - 1, i, i + 1) if 0 <= j < len(a) and ok(a[j])]
            if win:
                valid += 1
                wet += any(x >= thr for x in win)
        if valid:
            out[t] = 100.0 * wet / valid
    return out, len(members)


# ===========================================================================
# 3. PREPARE EACH HOUR
#    Forecast values come straight from the lead model for each hour (named on the page).
#    Estimated values are calculated here and are always labelled "est." on the page.
# ===========================================================================
TZ = ZoneInfo(SITE["tz"])


def ok(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def local(t):
    return datetime.fromtimestamp(t, TZ)


def r0(v):
    return "-" if not ok(v) else str(int(round(v)))


def model_values(d):
    """Add this model's estimated values: shear, gradient and cloud base."""
    if ok(d["w10"]) and ok(d["w100"]) and ok(d["d10"]) and ok(d["d100"]):
        th = math.radians(d["d100"] - d["d10"])
        d["shear"] = math.sqrt(max(0.0, d["w100"] ** 2 + d["w10"] ** 2 - 2 * d["w100"] * d["w10"] * math.cos(th)))
    else:
        d["shear"] = None
    d["alpha"] = (math.log(d["w100"] / d["w10"]) / math.log(100 / 10)
                  if ok(d["w10"]) and ok(d["w100"]) and d["w10"] >= 3 and d["w100"] > 0 else None)
    d["base"] = 125 * (d["T"] - d["Td"]) * 3.281 if ok(d["T"]) and ok(d["Td"]) else None
    return d


def suspect_gust(w, g):
    """A gust far out of line with the same model's average wind (likely a model artefact)."""
    O = OPTIONS
    return ok(w) and ok(g) and g > O["gust_suspect_ratio"] * max(w, 1) and g - w >= O["gust_suspect_extra_mph"]


def combine(per, prob, order):
    """The hour's figures from ONE lead model (the first in `order` with wind data),
    like Windy or Windfinder. Gaps are filled from the next model in `order`, and named."""
    O = OPTIONS
    order = [m for m in order if m in per] + [m for m in per if m not in order]
    lead = next((m for m in order if ok(per[m].get("w10"))), order[0] if order else None)

    def take(k, where=None):
        for m in order:
            v = per[m].get(k)
            if ok(v) and (where is None or where(per[m])):
                return v, m
        return None, None

    L = per.get(lead, {})
    h = {"prob": prob, "lead": lead}
    h["wind"], h["wind_m"] = (L.get("w10"), lead) if ok(L.get("w10")) else (None, None)
    # Gusts always from the same model as the wind, so the two belong together
    h["gust"], h["gust_m"] = (L.get("g"), lead) if ok(L.get("g")) else (None, None)
    if ok(h["gust"]) and ok(h["wind"]) and h["gust"] < h["wind"]:
        h["gust"] = h["wind"]
    # Safeguard: if the lead model's gust is far out of line with its own wind (for example ECMWF's
    # "strongest gust in the previous 3 hours" smeared across several hours), use the highest gust
    # from the other models that look plausible, and say so on the page.
    h["gust_note"] = None
    if suspect_gust(h["wind"], h["gust"]):
        others = [(per[m]["g"], m) for m in order
                  if m != lead and ok(per[m].get("g")) and ok(per[m].get("w10")) and not suspect_gust(per[m]["w10"], per[m]["g"])]
        if others:
            g, gm = max(others)
            h["gust_note"] = (f"{MODEL_NAMES.get(lead, lead)} gave {r0(h['gust'])} mph, unusually high for its wind, "
                              f"so {MODEL_NAMES.get(gm, gm)}'s gust is used")
            h["gust"], h["gust_m"] = max(g, h["wind"]), gm
        else:
            h["gust_note"] = "unusually high for the wind; compare Windy"
    h["gust_suspect"] = h["gust_note"] is not None
    h["w100"], h["w100_m"] = take("w100")
    h["rain"], h["rain_m"] = take("rain")
    h["cloud"], _ = take("cloud")
    h["low"], _ = take("low")
    vis, h["vis_m"] = take("vis")
    h["vis"] = vis / 1000 if ok(vis) else None
    h["cape"], h["cape_m"] = take("cape")
    h["temp"], _ = take("T")
    # Estimates come from a single model's own 10 m and 100 m winds, never mixed
    h["shear"], h["shear_m"] = take("shear")
    h["alpha"], h["alpha_m"] = take("alpha")
    h["base"], h["base_m"] = take("base", where=lambda d: ok(d["low"]) and d["low"] >= O["low_cloud_pct"])
    if ok(h["base"]) and h["base_m"] != (take("low")[1]):
        h["base"], h["base_m"] = None, None   # only use a cloud base from the model giving the low cloud
    h["base_any"], _ = take("base")
    h["dir"] = L.get("d10") if ok(L.get("d10")) else take("d10")[0]
    # Compare the models (suspect gusts left out)
    ws = [(m, per[m]["w10"], per[m]["g"]) for m in order if ok(per[m].get("w10"))]
    h["each"] = ws
    h["suspects"] = [m for m, w, g in ws if suspect_gust(w, g)]
    h["disagree"] = False
    if len(ws) >= 2:
        winds = [w for _, w, _ in ws]
        gusts = [g for m, w, g in ws if ok(g) and m not in h["suspects"]]
        mean = sum(winds) / len(winds)
        if (max(winds) - min(winds) > max(O["disagree_wind_mph"], O["disagree_wind_frac"] * mean)
                or (len(gusts) >= 2 and max(gusts) - min(gusts) > O["disagree_gust_mph"])):
            h["disagree"] = True
    h["n_models"] = len(ws)
    return h


def prepare(data, models, ens, now):
    H = data.get("hourly") or {}
    times = H.get("time")
    if not isinstance(times, list) or not times:
        raise RuntimeError("the forecast reply had no hourly data")
    # A series that is missing, all empty, or (for some variables) a whole week of exact zeros
    # is treated as "not supplied".
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
            "cloud": "cloud_cover", "low": "cloud_cover_low", "vis": "visibility", "cape": "cape",
            "T": "temperature_2m", "Td": "dew_point_2m"}
    probs, n_members = rain_chance(ens)
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
            per[m] = model_values(d)
        order = OPTIONS["lead_short"] if t < now + OPTIONS["lead_short_hours"] * 3600 else OPTIONS["lead_long"]
        h = combine(per, probs.get(t), order if models != [None] else [None])
        h["t"], h["per"] = t, per
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
    info = {"models": [m for m in models if m in used],
            "missing": {m: v for m, v in missing.items() if m in used},
            "elevation": data.get("elevation"), "n_members": n_members}
    return hours, sun, info


# ===========================================================================
# 4. RATE HOURS AND FIND WINDOWS
# ===========================================================================
def rate(h, L, golden):
    """Check every requirement for one hour.
    Returns (level, checks). level: 0 Good, 1 Marginal, 2 No-go.
    Each check: name, value, limit, level, est (estimated?), model, reason (plain-English line)."""
    O, m = OPTIONS, OPTIONS["amber_margin"]
    C = []

    def add(name, value, limit, level, est=False, model=None, reason=None):
        C.append({"name": name, "value": value, "limit": limit, "level": level, "est": est, "model": model,
                  "reason": reason or f"{name} {value}"})

    def over(name, v, lim, unit, est=False, model=None, dec=0):
        if not ok(v):
            add(name, "n/a", f"≤ {lim:g}{unit}", 0, est, None)
            return
        d = 1 if round(v) == round(lim) and v > lim else dec
        val = f"{v:,.{d}f}{unit}"
        lv = 2 if v > lim else 1 if v > lim * (1 - m) else 0
        add(name, val, f"≤ {lim:g}{unit}", lv, est, model,
            f"{name} {val} ({'limit' if lv == 2 else 'near limit'} {lim:g}{unit})")

    def under(name, v, lim, unit, est=False, model=None, dec=1, note=""):
        if not ok(v):
            add(name, "n/a", f"≥ {lim:,g}{unit}", 0, est, None)
            return
        val = f"{v:,.{dec}f}{unit}{note}"
        lv = 2 if v < lim else 1 if v < lim * (1 + m) else 0
        add(name, val, f"≥ {lim:,g}{unit}", lv, est, model,
            f"{name} {val} ({'minimum' if lv == 2 else 'near minimum'} {lim:,g}{unit})")

    over("Wind", h["wind"], L["max_wind"], " mph", model=h["wind_m"])
    over("Gusts", h["gust"], L["max_gust"], " mph", model=h["gust_m"])
    if h.get("gust_note"):
        C[-1]["value"] += f" ({h['gust_note']})"
    spread = h["gust"] - h["wind"] if ok(h["gust"]) and ok(h["wind"]) else None
    over("Gust spread", spread, L["max_spread"], " mph", est=True)
    over("Wind at 100 m", h["w100"], L["max_wind_100m"], " mph", model=h["w100_m"])
    over("Shear, 10 to 100 m", h["shear"], L["max_shear"], " mph", est=True, model=h["shear_m"])
    # Climb gradient (caution only)
    climb = (ok(h["alpha"]) and h["alpha"] > O["gradient_alpha"] and ok(h["w100"]) and ok(h["wind"])
             and h["w100"] - h["wind"] >= O["gradient_min_increase_mph"])
    golden_shear = (golden and not climb and ok(h["shear"]) and h["shear"] >= O["golden_shear_fraction"] * L["max_shear"])
    if ok(h["alpha"]):
        lead = "☀ Golden hour, but watch the climb gradient" if golden else "Strong low-level gradient expected"
        add("Climb gradient", f"α {h['alpha']:.2f}", f"caution > {O['gradient_alpha']}", 1 if (climb or golden_shear) else 0, True,
            h["alpha_m"], f"{lead}: {r0(h['wind'])} mph at ground, {r0(h['w100'])} mph at 100 m")
    else:
        add("Climb gradient", "n/a", f"caution > {O['gradient_alpha']}", 0, True)
    over("Rain chance", h["prob"], L["max_rain_chance"], "%")
    if ok(h["rain"]):
        add("Rain amount", f"{h['rain']:.1f} mm", "0 mm", 2 if h["rain"] >= 0.1 else 0, False, h["rain_m"],
            f"Rain forecast ({h['rain']:.1f} mm)")
    under("Visibility", h["vis"], L["min_visibility_km"], " km", model=h["vis_m"])
    if ok(h["base"]):
        under("Cloud base", round(h["base"] / 100) * 100, L["min_cloud_base_ft"], " ft", est=True,
              model=h["base_m"], dec=0, note=f", low cloud {r0(h['low'])}%")
    else:
        val = (f"~{round(h['base_any'] / 100) * 100:,.0f} ft, low cloud only {r0(h['low'])}%, so not checked"
               if ok(h["base_any"]) else "n/a")
        add("Cloud base", val, f"≥ {L['min_cloud_base_ft']:,} ft", 0, True)
    over("Shower energy (CAPE)", h["cape"], L["max_cape"], " J/kg", model=h["cape_m"])
    if h["n_models"] >= 2:
        each = ", ".join(f"{MODEL_NAMES[mm]} {r0(w)}" for mm, w, _ in h["each"])
        add("Model agreement", f"{each} mph", f"spread ≤ {O['disagree_wind_mph']} mph",
            1 if h["disagree"] and O["disagree_is_marginal"] else 0, False, None,
            f"Models disagree: {each} mph")
    if not ok(h["wind"]) or not ok(h["gust"]):
        add("Wind data", "missing", "needed", 1)
    return max(c["level"] for c in C), C


def reasons(h):
    return sorted([c for c in h["checks"] if c["level"] > 0], key=lambda c: -c["level"])


def build_days(hours, sun, now, L, model=False):
    """model=False: the main view (lead model each hour). model=<id>: that model on its own."""
    days = {}
    for base in hours:
        d = local(base["t"]).date()
        if d not in sun:
            continue
        rise, sset = sun[d]
        if min(base["t"] + 3600, sset) - max(base["t"], rise) < 1800 or base["t"] + 3600 <= now:
            continue
        g = OPTIONS["golden_hours"] * 3600
        if model is False:
            h = dict(base)
        else:
            if not ok(base["per"].get(model, {}).get("w10")):
                continue
            h = combine({model: base["per"][model]}, base["prob"], [model])
            h["t"] = base["t"]
        h["golden"] = h["t"] < rise + g or h["t"] + 3600 > sset - g
        h["level"], h["checks"] = rate(h, L, h["golden"])
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
:root{--bg:#f3f6f9;--card:#fff;--text:#0b1620;--muted:#41525f;--line:#d3dce4;--track:#e6edf2;--grid:rgba(11,22,32,.12);--chip:#eef3f7;
 --go:#0a7a3b;--go-bg:#d6f5e1;--warn:#8a5200;--warn-bg:#fff0bf;--stop:#b3150b;--stop-bg:#ffdcd7;
 --bar-go:#16a34a;--bar-warn:#e39b00;--bar-stop:#df3b2c;--lim:#0b1620;--accent:#0b5cad;--gold:#9a6200;--est:#5b3fa8;
 --rain:#2563eb;--stripe:rgba(255,255,255,.55)}
@media (prefers-color-scheme:dark){:root{--bg:#0b1622;--card:#132331;--text:#f2f7fb;--muted:#b4c4d2;--line:#2a3d4f;--track:#1d3142;--grid:rgba(242,247,251,.12);--chip:#1b2e3e;
 --go:#5ee69a;--go-bg:#103a24;--warn:#ffc94d;--warn-bg:#3d2f0b;--stop:#ff8173;--stop-bg:#45170f;
 --bar-go:#3ddc84;--bar-warn:#ffc23d;--bar-stop:#ff6a5c;--lim:#f2f7fb;--accent:#8fd3ff;--gold:#ffd27a;--est:#c9b6ff;
 --rain:#60a5fa;--stripe:rgba(11,22,34,.45)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:17px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:880px;margin:0 auto;padding:12px 14px 40px}
h1{font-size:1.5rem;margin:2px 0 0;line-height:1.15}
h2{font-size:1.25rem;margin:26px 0 8px}
h3{font-size:1.05rem;margin:0 0 8px}
p{margin:0}
a{color:var(--accent)}
.muted{color:var(--muted)} .small{font-size:.9rem}
.go{color:var(--go)} .warn{color:var(--warn)} .stop{color:var(--stop)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;margin:12px 0}
.stale{border:2px solid var(--stop);background:var(--stop-bg);border-radius:14px;padding:12px 14px;margin:10px 0;font-weight:600}
summary{list-style:none;cursor:pointer}
summary::-webkit-details-marker{display:none}
em.est{font-style:normal;font-size:.72em;font-weight:700;color:var(--est);border:1px solid currentColor;border-radius:5px;padding:0 .35em;margin-left:.35em;white-space:nowrap;vertical-align:.08em}
i.mt{font-style:normal;font-size:.75em;font-weight:600;color:var(--muted);background:var(--chip);border-radius:5px;padding:0 .35em;margin-left:.3em;white-space:nowrap}
/* Stripes = models disagree */
.dis{background-image:repeating-linear-gradient(135deg,var(--stripe) 0 3px,transparent 3px 8px)}
/* Limits picker (opened by the #limits link, no JavaScript) */
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
.map{width:100%;height:520px;border:0;border-radius:12px;display:block;background:var(--track)}
@media (max-width:600px){.map{height:430px}}
.banner{border-radius:16px;padding:14px 16px;margin:14px 0;font-size:1.1rem;font-weight:600;border:2px solid currentColor}
.banner.go{background:var(--go-bg)} .banner.stop{background:var(--stop-bg)}
.banner b{font-size:1.35rem;display:block;color:var(--text)}
/* Week at a glance */
.gwrap{overflow-x:auto}
.glance{width:100%;border-collapse:separate;border-spacing:3px;table-layout:fixed;font-size:.78rem}
.glance thead th{font-weight:600;color:var(--muted);text-align:center;padding:0 0 2px}
.glance th.d{text-align:left;width:4.6em;font-weight:600;color:var(--text);font-size:.85rem;white-space:nowrap}
.glance th.d small{color:var(--muted);font-weight:500}
.glance td{height:30px;border-radius:6px;padding:0;text-align:center;font-size:.72rem;color:rgba(0,0,0,.55)}
.glance .axis{font-size:.75rem;color:var(--muted);text-align:right;padding-top:2px}
.c0{background-color:var(--bar-go)} .c1{background-color:var(--bar-warn)} .c2{background-color:var(--bar-stop)} .cn{background:transparent}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:10px;font-size:.88rem;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:22px;height:14px;border-radius:4px}
.legend i.sg{background-color:var(--bar-go)} .legend i.sw{background-color:var(--bar-warn)} .legend i.ss{background-color:var(--bar-stop)}
.legend i.sd{background-color:var(--muted)}
details.compare{margin-top:12px;border-top:1px solid var(--line);padding-top:6px}
details.compare>summary,details.howto>summary,details.method>summary{font-weight:700;min-height:44px;display:flex;align-items:center;gap:8px}
details.compare>summary::after,details.howto>summary::after,details.method>summary::after{content:"Show ▾";margin-left:auto;color:var(--accent);font-size:.9rem;font-weight:600}
details.compare[open]>summary::after,details.howto[open]>summary::after,details.method[open]>summary::after{content:"Hide ▴"}
.mgrid{margin:12px 0 4px}
.mgrid h4{margin:0 0 4px;font-size:.95rem}
/* How to read */
.howgrid{display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center;font-size:.92rem;margin:4px 0 8px}
.swb{width:64px;height:16px;border-radius:4px;position:relative;background:var(--track);overflow:hidden}
.swb .w,.swb .g{position:absolute;left:0;top:0;bottom:0}
.swb .g{opacity:.42}
/* Day cards */
details.day{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:12px 0;overflow:hidden}
details.day>summary{padding:14px 16px;display:grid;grid-template-columns:1fr auto;gap:2px 10px;min-height:64px}
.dname{font-size:1.2rem;font-weight:700}
.dname span{font-weight:500;color:var(--muted);font-size:1rem;margin-left:6px}
.badge{border-radius:999px;padding:4px 12px;font-weight:700;white-space:nowrap;align-self:start;color:#0b1620}
.badge.go{background:var(--bar-go)} .badge.warn{background:var(--bar-warn)} .badge.stop{background:var(--bar-stop)}
@media (prefers-color-scheme:dark){.badge{color:#07121c}}
.dwin{grid-column:1/-1;font-weight:600}
.dstat{grid-column:1/-1;color:var(--muted);font-size:.9rem}
.open{grid-column:1/-1;color:var(--accent);font-weight:600;font-size:.95rem}
details[open] .open .closed,details:not([open]) .open .opened{display:none}
.daybody{padding:4px 16px 14px;border-top:1px solid var(--line)}
/* Hour rows: one bar for wind and gusts, a small rain meter, tap for details */
.cols{display:grid;grid-template-columns:4.2rem minmax(0,1fr) 5.6rem 1rem;gap:0 10px;align-items:center}
.axisrow{font-size:.75rem;color:var(--muted);padding:8px 0 4px;border-bottom:1px solid var(--line)}
.axisrow .ax{position:relative;height:1.2em}
.axisrow .ax span{position:absolute;transform:translateX(-50%)}
.axisrow .ax span:first-child{transform:none}
.axisrow .ax span:last-child{transform:translateX(-100%)}
.axisrow .rx{display:flex;justify-content:space-between}
details.hr{border-bottom:1px solid var(--line)}
details.hr:last-child{border-bottom:0}
details.hr>summary{padding:7px 0;min-height:40px}
.t{font-weight:700;font-size:.98rem;white-space:nowrap}
.t small{font-weight:400;color:var(--muted)}
.t .ic{font-size:.85rem;margin-left:2px}
.track{position:relative;height:24px;background-color:var(--track);border-radius:6px;overflow:hidden;
 background-image:linear-gradient(to right,var(--grid) 1px,transparent 1px)}
.track .g,.track .w{position:absolute;left:0;top:0;bottom:0}
.l0 .w,.l0 .g{background-color:var(--bar-go)} .l1 .w,.l1 .g{background-color:var(--bar-warn)} .l2 .w,.l2 .g{background-color:var(--bar-stop)}
.track .g{opacity:.42}
.track .lw,.track .lg{position:absolute;top:0;bottom:0;width:0}
.track .lw{border-left:2px solid var(--lim)}
.track .lg{border-left:2px dotted var(--lim)}
.rain{display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--muted)}
.rmeter{position:relative;flex:1;height:10px;background:var(--track);border-radius:5px;overflow:hidden}
.rmeter .f{position:absolute;left:0;top:0;bottom:0;background:var(--rain)}
.rmeter .rl{position:absolute;top:0;bottom:0;border-left:2px solid var(--lim)}
.rain b{font-weight:600;min-width:2.4em;text-align:right;color:var(--text)}
.chev{color:var(--accent);font-size:.8rem;text-align:center}
details.hr[open] .chev{transform:rotate(180deg)}
.hd{padding:2px 0 14px 4.2rem}
@media (max-width:520px){.hd{padding-left:0}}
.hd .verdict{font-weight:700;margin-bottom:6px}
table.checks{width:100%;border-collapse:collapse;font-size:.86rem}
@media (max-width:520px){table.checks{font-size:.8rem}table.checks td{padding:4px}}
table.checks td.s{width:1.6em;white-space:nowrap}
table.checks th{text-align:left;color:var(--muted);font-weight:600;font-size:.78rem;padding:4px 6px;border-bottom:1px solid var(--line)}
table.checks td{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
table.checks tr.k1 td{background:var(--warn-bg)} table.checks tr.k2 td{background:var(--stop-bg)}
table.checks td.s{text-align:center;font-weight:700;width:1.6em}
.extra{font-size:.84rem;color:var(--muted);margin-top:8px}
.sun{color:var(--gold)}
ul.links{padding-left:20px;margin:6px 0}
ul.links li{margin:9px 0}
details.method{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:4px 16px;margin:12px 0}
.method h4{font-size:1rem;margin:14px 0 4px}
.method p{margin:0 0 8px;max-width:72ch;font-size:.94rem}
.method code{font-size:.88em;background:var(--chip);padding:1px 5px;border-radius:5px}
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
            on = v == L[key]
            links.append(f'<a href="{fname(dict(L, **{key: v}))}#limits" class="{"on" if on else ""}"{" aria-current=page" if on else ""}>{v}</a>')
        return f'<div class="optrow"><p>{title}</p><div class="opts">{"".join(links)}</div></div>'
    return f"""<section class="limits" id="limits" aria-label="Your limits">
<div class="limsum"><span>Your limits: wind <b>{L['max_wind']}</b>, gusts <b>{L['max_gust']}</b>, gust spread <b>{L['max_spread']}</b> mph</span>
<a class="btn chg" href="#limits">Change limits</a></div>
<div class="limpanel">
{row('max_wind', 'Max average wind (mph)')}
{row('max_gust', 'Max gusts (mph)')}
{row('max_spread', 'Max gust spread (mph): how far gusts may rise above the average wind')}
<p class="small muted">Each choice opens a page already worked out for those limits. Bookmark it or add it to your home screen to keep them.
Other limits (wind at 100 m {LIMITS['max_wind_100m']} mph, shear {LIMITS['max_shear']} mph, rain chance {LIMITS['max_rain_chance']}%,
visibility {LIMITS['min_visibility_km']} km, cloud base {LIMITS['min_cloud_base_ft']:,} ft, CAPE {LIMITS['max_cape']} J/kg) are set in build.py.</p>
<p style="margin-top:8px"><a class="btn" href="{fname(L)}">Done</a></p>
</div></section>"""


def glance_table(days, today, caption):
    if not days:
        return '<p class="small muted">No data from this model for these days.</p>'
    first = min(local(h["t"]).hour for d in days for h in d["hours"])
    last = max(local(h["t"]).hour for d in days for h in d["hours"])
    cols = list(range(first, last + 1))
    head = "".join(f"<th>{c:02d}</th>" for c in cols)
    rows = []
    for d in days:
        by = {local(h["t"]).hour: h for h in d["hours"]}
        cells = []
        for c in cols:
            if c not in by:
                cells.append('<td class="cn"></td>')
                continue
            h = by[c]
            _, ic, wd = LV[h["level"]]
            dis = " dis" if h["disagree"] else ""
            tip = f'{c:02d}:00 {wd}' + (", models disagree" if h["disagree"] else "")
            cells.append(f'<td class="c{h["level"]}{dis}" title="{tip}"><span class="sr">{tip}</span><span aria-hidden="true">{ic}</span></td>')
        lbl = day_label(d["date"], today)
        short = {"Today": "Today", "Tomorrow": "Tmrw"}.get(lbl, d["date"].strftime("%a"))
        rows.append(f'<tr><th class="d" scope="row">{short} <small>{d["date"].day}</small></th>{"".join(cells)}</tr>')
    return (f'<div class="gwrap"><table class="glance" aria-label="{e(caption)}"><thead><tr><th class="d"></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="axis small muted" style="text-align:right">Hour of day, UK time</p>')


def legend_html():
    return ('<div class="legend"><span><i class="sg"></i>Good</span><span><i class="sw"></i>Marginal</span>'
            '<span><i class="ss"></i>No-go</span><span><i class="sd dis"></i>Models disagree</span></div>')


def howto_html(L):
    return f"""<details class="howto card"><summary>How to read the bars</summary>
<div class="howgrid">
<span class="swb l0"><span class="g" style="width:60%"></span><span class="w" style="width:35%"></span></span>
<span>Solid part: average wind. Lighter part: gusts. The colour is the hour's rating: green Good, amber Marginal, red No-go.</span>
<span class="swb" style="background:none"><span style="position:absolute;left:18px;top:0;bottom:0;border-left:2px solid var(--lim)"></span><span style="position:absolute;left:42px;top:0;bottom:0;border-left:2px dotted var(--lim)"></span></span>
<span>Solid line: your wind limit ({L['max_wind']} mph). Dotted line: your gust limit ({L['max_gust']} mph). Faint lines every 10 mph.</span>
<span class="swb l2"><span class="w dis" style="width:70%"></span></span>
<span>Stripes: the models disagree, so the forecast is less certain.</span>
<span class="rmeter" style="width:64px;flex:none"><span class="f" style="width:30%"></span><span class="rl" style="left:{L['max_rain_chance']}%"></span></span>
<span>Blue meter: chance of rain, 0 to 100%. The line marks your limit ({L['max_rain_chance']}%).</span>
</div>
<p class="small muted">Tap any hour for its numbers and each requirement. <i class="mt">GFS</i> names the model a value came from;
{EST} marks values this page calculates rather than forecasts.</p></details>"""


def method_html(info):
    L, O = LIMITS, OPTIONS
    n = info.get("n_members") or 0
    return f"""<details class="method"><summary>How the ratings are worked out</summary>
<h4>Forecast values</h4>
<p>Wind, gusts, wind at 100 m, rain amount, cloud, visibility, CAPE and temperature come straight from the models via Open-Meteo.
Like Windy and Windfinder, each hour uses one lead model: the Met Office model (2 km UKV over the UK) for the next
{O['lead_short_hours']} hours, then ECMWF, widely regarded as the most accurate global model. Wind and gusts always come from the same
model. If the lead model doesn't supply a value, the next model fills the gap and is named. A week of exact zeros counts as missing.</p>
<h4>Rain chance</h4>
<p>Worked out from an ensemble: {n or 'many'} runs of the same model, each started slightly differently. The rain chance is the share of
runs with at least {O['rain_threshold_mm']} mm of rain within an hour either side. Individual models don't provide a rain chance of their own.</p>
<h4>Estimated values {EST}</h4>
<p><b>Gust spread</b>: gusts minus average wind.</p>
<p><b>Wind shear</b> between 10 m and 100 m, using speed and direction: <code>√(V100² + V10² − 2·V100·V10·cos θ)</code>, where θ is the
difference in direction. Worked out for each model; the highest is used.</p>
<p><b>Climb gradient</b>: <code>α = ln(V100 / V10) / ln(10)</code>. Above {O['gradient_alpha']}, with at least {O['gradient_min_increase_mph']} mph more
wind at 100 m, the hour is Marginal. Common in calm air around dawn and dusk.</p>
<p><b>Cloud base</b> (Espy's rule): <code>125 × (temperature − dew point) × 3.281</code> feet, checked only when a model forecasts at least
{O['low_cloud_pct']}% low cloud. Below {L['min_cloud_base_ft']:,} ft is No-go. Best for cumulus; can be wrong for layered cloud.</p>
<p>These use model winds at only two heights and simple rules of thumb, so treat them as prompts to look more closely.</p>
<h4>Model disagreement</h4>
<p>Striped when the models' winds differ by more than {O['disagree_wind_mph']} mph (or {int(O['disagree_wind_frac'] * 100)}% of the average),
or their gusts by more than {O['disagree_gust_mph']} mph{". Such hours are at least Marginal" if O['disagree_is_marginal'] else ""}.</p>
<h4>Ratings</h4>
<p>Good: every limit met. Marginal: within {int(O['amber_margin'] * 100)}% of a limit, or a caution above. No-go: any limit broken, or rain
forecast. Daylight hours only.</p>
</details>"""


def hour_html(h, L, scale):
    c, ic, wd = LV[h["level"]]
    lt = local(h["t"])
    pct = lambda v: 0 if not ok(v) else max(0.0, min(100.0, v / scale * 100))
    dis = " dis" if h["disagree"] else ""
    prob = h["prob"]
    rain = (f'<span class="rain"><span class="rmeter"><span class="f" style="width:{prob:.0f}%"></span>'
            f'<span class="rl" style="left:{L["max_rain_chance"]}%"></span></span><b>{r0(prob)}%</b></span>'
            if ok(prob) else '<span class="rain"><span class="rmeter"></span><b>n/a</b></span>')
    sr = (f'{lt.hour:02d}:00, {wd}. Wind {r0(h["wind"])} mph, gusts {r0(h["gust"])} mph, rain chance {r0(prob)}%'
          + (". Models disagree" if h["disagree"] else ""))
    summary = (f'<summary class="cols l{h["level"]}" title="Tap for details">'
               f'<span class="t">{lt.hour:02d}<small>:00</small><span class="ic {c}" aria-hidden="true"> {ic}</span>{" <span class=sun>☀</span>" if h["golden"] else ""}</span>'
               f'<span class="track" aria-hidden="true" style="background-size:{1000 / scale:.3f}% 100%">'
               f'<span class="g{dis}" style="width:{pct(h["gust"]):.1f}%"></span><span class="w{dis}" style="width:{pct(h["wind"]):.1f}%"></span>'
               f'<span class="lw" style="left:{pct(L["max_wind"]):.1f}%"></span><span class="lg" style="left:{pct(L["max_gust"]):.1f}%"></span></span>'
               f'{rain}<span class="chev" aria-hidden="true">▾</span><span class="sr">{e(sr)}</span></summary>')
    rows = []
    for k in h["checks"]:
        lv = k["level"]
        mark = LV[lv][1]
        rows.append(f'<tr class="k{lv}"><td>{e(k["name"])}{EST if k["est"] else ""}</td>'
                    f'<td>{e(k["value"])}{mtag(k["model"]) if h["n_models"] > 1 else ""}</td><td>{e(k["limit"])}</td>'
                    f'<td class="s {LV[lv][0]}" title="{LV[lv][2]}">{mark}</td></tr>')
    rs = reasons(h)
    verdict = (f'<p class="verdict {c}">{ic} {wd}' + (f': {e(rs[0]["reason"])}{EST if rs[0]["est"] else ""}' if rs else ": every requirement met")
               + "</p>")
    dirtxt = C16[int(round(h["dir"] / 22.5)) % 16] if ok(h["dir"]) else "-"
    each = ""
    if h["n_models"] > 1:
        each = (f" Figures from {MODEL_NAMES.get(h['lead'], h['lead'])}. Each model (wind / gusts): "
                + ", ".join(f"{MODEL_NAMES[m]} {r0(w)} / {r0(g)}{' (gust looks unusually high)' if m in h.get('suspects', []) else ''}"
                            for m, w, g in h["each"]) + " mph.")
    extra = f'<p class="extra">Wind from {dirtxt}. Cloud {r0(h["cloud"])}%, {r0(h["temp"])}°C.{each}</p>'
    body = (f'<div class="hd">{verdict}<table class="checks"><thead><tr><th>Requirement</th><th>Forecast</th><th>Your limit</th><th></th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>{extra}</div>')
    return f'<details class="hr">{summary}{body}</details>'


def page(days, info, L, built, fetched=None, stale_reason=None, no_data=False, per_model=None):
    now_l = local(built)
    today = now_l.date()
    P = [f"""<!DOCTYPE html>
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
<p class="muted small">{e(SITE['name'])}. UK local time, speeds in mph.</p>"""]
    if not no_data:
        f_l = local(fetched or built)
        P.append(f'<p class="small" style="margin-top:4px"><b>Forecast downloaded {f_l.strftime("%a")} {f_l.day} {f_l.strftime("%b, %H:%M")}</b>. '
                 f'Updates every hour; if this is more than {OPTIONS["stale_hours"]} hours ago, updates have stopped.</p>')
        if stale_reason or (built - (fetched or built)) / 3600 > OPTIONS["stale_hours"]:
            P.append(f'<div class="stale" role="alert">⚠ Forecast not updated. The last download attempt failed at {hm(built)}'
                     f'{" (" + e(stale_reason) + ")" if stale_reason else ""}. This forecast is {dur(built - fetched)} old.</div>')
        P.append(limits_html(L))

    P.append(f"""<h2 style="margin-top:14px">Live wind map</h2>
<div class="card" style="padding:8px">{windy_iframe()}
<p class="small muted" style="padding:8px 6px 2px">Windy's own map and point forecast (ECMWF model, switchable in the map).
If it doesn't appear, <a href="{e(LINKS[0][1])}" rel="noopener">open it on Windy</a>.</p></div>""")

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

        # Overview with model comparison
        compare = ""
        if per_model and len(info["models"]) > 1:
            grids = "".join(f'<div class="mgrid"><h4>{e(MODEL_LONG.get(m, str(m)))} on its own</h4>{glance_table(pm, today, MODEL_LONG.get(m, str(m)))}</div>'
                            for m, pm in per_model.items())
            compare = f"""<details class="compare"><summary>Why are some hours striped? Compare the models</summary>
<p class="small">Each hour's rating comes from one lead model: the Met Office model for the next {OPTIONS['lead_short_hours']} hours,
then ECMWF. The other models are only used to check it. Stripes mark hours where the models disagree by more than
{OPTIONS['disagree_wind_mph']} mph of wind or {OPTIONS['disagree_gust_mph']} mph of gusts, so the forecast is less certain.
This is normal several days ahead and usually settles as the day gets closer. A gust far out of line with the same model's own wind
is treated as a model quirk and left out of this check. Below is how each model on its own rates the same hours against your limits.</p>{grids}</details>"""
        P.append(f"""<h2>Day by day</h2>
<div class="card"><h3>Week at a glance</h3>{glance_table(days, today, 'Week at a glance')}{legend_html()}{compare}</div>
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
                top = next((c for h in d["hours"] for c in reasons(h) if c["level"] == d["level"]), None)
                win = f'<span class="{cls}">{icon} No flyable window</span>' + (
                    f' <span class="muted">{e(top["reason"])}{EST if top["est"] else ""}</span>' if top else "")
            mx_w = max((h["wind"] or 0) for h in d["hours"])
            mx_g = max((h["gust"] or 0) for h in d["hours"])
            probs = [h["prob"] for h in d["hours"] if ok(h["prob"])]
            stat = f"Wind up to {r0(mx_w)} mph, gusts up to {r0(mx_g)} mph" + (f", rain chance up to {r0(max(probs))}%" if probs else "")
            stat += f". Sunrise {hm(d['rise'])}, sunset {hm(d['set'])}."
            scale = min(60, max(OPTIONS["bar_scale_min_mph"], math.ceil(max(mx_g, mx_w) / 10) * 10))
            ax = "".join(f'<span style="left:{t / scale * 100:.1f}%">{t}</span>' for t in range(0, scale + 1, 10))
            P.append(f"""<details class="day"{" open" if k == 0 else ""}>
<summary><span class="dname">{e(label)}<span>{d['date'].day} {d['date'].strftime('%b')}</span></span><span class="badge {cls}">{icon} {word}</span>
<span class="dwin">{win}</span><span class="dstat">{stat}</span>
<span class="open"><span class="closed">Show hours ▾</span><span class="opened">Hide hours ▴</span></span></summary>
<div class="daybody">
<div class="cols axisrow" aria-hidden="true"><span>Time</span><span class="ax">{ax}</span><span class="rx"><span>Rain</span><span>%</span></span><span></span></div>
<div class="cols axisrow" aria-hidden="true" style="padding-top:0;border:0"><span></span><span>Wind and gusts, mph</span><span></span><span></span></div>
{''.join(hour_html(h, L, scale) for h in d['hours'])}
</div></details>""")

        miss = []
        nice = {"wind_speed_100m": "100 m wind", "wind_direction_100m": "100 m direction", "cloud_cover_low": "low cloud",
                "visibility": "visibility", "cape": "CAPE", "dew_point_2m": "dew point", "wind_gusts_10m": "gusts",
                "wind_speed_10m": "wind", "precipitation": "rain amount", "cloud_cover": "cloud", "temperature_2m": "temperature",
                "wind_direction_10m": "wind direction"}
        for m, vars_ in info["missing"].items():
            items = sorted({nice.get(v, v.replace("_", " ")) for v in vars_})
            if items:
                miss.append(f"{MODEL_LONG.get(m, m)} does not supply {', '.join(items)}")
        elev = info.get("elevation")
        rain_src = (f"Rain chance from {info['n_members']} ECMWF ensemble runs. " if info.get("n_members") and info.get("ens_model") == "ecmwf_ifs025"
                    else f"Rain chance from {info['n_members']} ensemble runs. " if info.get("n_members") else "Rain chance unavailable this hour. ")
        P.append(f"""<p class="small muted">Forecast from {e(', '.join(MODEL_LONG.get(m, str(m)) for m in info['models']))} via Open-Meteo,
lead model each hour (Met Office for {OPTIONS['lead_short_hours']} hours, then ECMWF). {e(rain_src)}{e('. '.join(miss) + '. ' if miss else '')}{f'Terrain height used for this point: {elev:.0f} m.' if ok(elev) else ''}</p>""")

    P.append(f"""<h2>Check before you fly</h2>
<div class="card"><ul class="links">{''.join(f'<li><a href="{e(u)}" rel="noopener">{e(t)}</a>: {e(dd)}</li>' for t, u, dd in LINKS)}</ul></div>
{method_html(info) if days else ''}
<footer><p>Forecast point {LAT}, {LON}. <a href="https://open-meteo.com/" rel="noopener">Weather data by Open-Meteo.com</a> (CC BY 4.0).
Map by <a href="https://www.windy.com/" rel="noopener">Windy.com</a>. Always check conditions on site before flying.</p>
<p>Page version {BUILD_VERSION}.</p></footer>
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
    ap.add_argument("--sample", help="build from a saved Open-Meteo forecast reply")
    ap.add_argument("--sample-ens", help="saved Open-Meteo ensemble reply to go with --sample")
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

    stale_reason, fetched, ens, ens_model = None, built, None, None
    cache_file = os.path.join(a.cache_dir, "last.json")
    if a.sample:
        with open(a.sample, encoding="utf-8") as f:
            data = json.load(f)
        if a.sample_ens:
            with open(a.sample_ens, encoding="utf-8") as f:
                ens, ens_model = json.load(f), "ecmwf_ifs025"
        used = [None] if a.sample_models == "none" else a.sample_models.split(",")
    else:
        try:
            data, used = fetch_forecast_with_fallback()
            ens, ens_model = fetch_ensemble()
            os.makedirs(a.cache_dir, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"fetched": built, "models": used, "data": data, "ens": ens, "ens_model": ens_model}, f)
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
            ens, ens_model = cached.get("ens"), cached.get("ens_model")
            stale_reason = "Open-Meteo could not be reached"
            print(f"Download failed ({ex}); using the forecast saved at {hm(fetched)}", file=sys.stderr)

    try:
        hours, sun, info = prepare(data, used, ens, built)
        info["ens_model"] = ens_model
    except Exception as ex:
        print(f"Build failed: {ex}", file=sys.stderr)
        sys.exit(1)

    count = 0
    for L in all_limit_sets():
        per_model = {m: build_days(hours, sun, built, L, model=m) for m in info["models"]} if len(info["models"]) > 1 else None
        html_text = page(build_days(hours, sun, built, L), info, L, built, fetched, stale_reason, per_model=per_model)
        with open(os.path.join(a.out_dir, fname(L)), "w", encoding="utf-8") as f:
            f.write(html_text)
        if all(L[k] == LIMITS[k] for k in CHOICES):
            with open(os.path.join(a.out_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(html_text)
        count += 1
    print(f"Wrote {count} pages plus index.html. Models: {', '.join(MODEL_NAMES.get(m, str(m)) for m in info['models'])}. "
          f"Rain chance from {info['n_members']} ensemble runs.")


if __name__ == "__main__":
    main()
