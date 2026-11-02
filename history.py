#!/usr/bin/env python3
"""
Membury PPG forecast: private history log for testing the forecast. Nothing here is published
on the website.

Each hourly GitHub run calls this after build.py. It:
  1. Logs what the forecast said for upcoming hours at fixed distances ahead (3 h, 12 h, 1 day,
     2 days ... 6 days): each model's wind and gusts, the figure the page used, and its rating.
  2. Logs real observations (METARs) from nearby airfields, fetched from the NOAA Aviation
     Weather Center. GitHub's computer can read these directly; a web page could not.
  3. Writes REPORT.md comparing the two.

Files go in the folder given by --log-dir, which the workflow saves to the repository's
"history" branch:
  forecasts-YYYY-MM.csv     one row per target hour per distance ahead
  observations-YYYY-MM.csv  one row per METAR
  REPORT.md                 the comparison (opens nicely on GitHub)

Usage (the workflow does this for you)
  python3 history.py --log-dir hist --cache data/last.json
Testing
  python3 history.py --log-dir hist --cache saved.json --now 1793692800 --metar-sample metars.json

Only the Python standard library is used.
"""

import argparse
import csv
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build as B  # noqa: E402  (reuses the page's own forecast logic, so the log matches the site)

# ===========================================================================
# SETTINGS
# ===========================================================================
LEAD_BUCKETS_H = [3, 12, 24, 48, 72, 96, 120, 144]   # how far ahead forecasts are logged
STATIONS = ["EGVN", "EGUB", "EGDM"]                   # METAR stations, nearest first; the first is compared
MATCH_MINUTES = 40                                    # observation must be within this of the forecast hour
REPORT_DAYS = 30                                      # the report covers this many recent days
KT_TO_MPH = 1.150779
MODELS = [("ukmo_seamless", "ukmo"), ("ecmwf_ifs025", "ecmwf"), ("gfs_seamless", "gfs")]

FC_COLS = (["logged_utc", "logged_at", "target_utc", "target_time", "lead_bucket_h", "lead_h", "daylight",
            "used_model", "used_wind", "used_gust", "used_level", "gust_replaced", "rain_chance", "disagree"]
           + [f"{k}_{v}" for _, k in MODELS for v in ("wind", "gust")])
OBS_COLS = ["icao", "obs_utc", "obs_time", "wind_kt", "gust_kt", "dir", "vis_sm", "temp_c", "dew_c", "raw"]


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def rnd(v, d=1):
    return "" if not B.ok(v) else (int(round(v)) if d == 0 else round(v, d))


def month_path(log_dir, kind, t):
    return os.path.join(log_dir, f"{kind}-{datetime.fromtimestamp(t, timezone.utc):%Y-%m}.csv")


def append_rows(path, cols, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def read_rows(log_dir, kind, since):
    rows = []
    for path in sorted(glob.glob(os.path.join(log_dir, f"{kind}-*.csv"))):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = float(r.get("target_time") or r.get("obs_time") or 0)
                if t >= since:
                    rows.append(r)
    return rows


def num(x):
    try:
        v = float(x)
        return v if B.ok(v) else None
    except (TypeError, ValueError):
        return None


# ===========================================================================
# 1. LOG THE FORECAST
# ===========================================================================
def log_forecast(log_dir, cache, now):
    fetched = cache.get("fetched", 0)
    if now - fetched > 1800:
        print("History: the saved forecast is not from this run (download failed?); forecast not logged.")
        return 0
    hours, sun, info = B.prepare(cache["data"], cache["models"], cache.get("ens"), fetched)
    rows = []
    for h in hours:
        lead = (h["t"] - fetched) / 3600
        bucket = next((b for b in LEAD_BUCKETS_H if b <= lead < b + 1), None)
        if bucket is None:
            continue
        d = B.local(h["t"]).date()
        rise, sset = sun.get(d, (None, None))
        daylight = int(bool(rise) and min(h["t"] + 3600, sset) - max(h["t"], rise) >= 1800)
        level, _ = B.rate(h, B.LIMITS, False)
        row = {"logged_utc": iso(fetched), "logged_at": int(fetched), "target_utc": iso(h["t"]), "target_time": int(h["t"]),
               "lead_bucket_h": bucket, "lead_h": round(lead, 2), "daylight": daylight,
               "used_model": B.MODEL_NAMES.get(h.get("lead"), ""), "used_wind": rnd(h["wind"]), "used_gust": rnd(h["gust"]),
               "used_level": level, "gust_replaced": int(bool(h.get("gust_note"))), "rain_chance": rnd(h["prob"], 0),
               "disagree": int(bool(h["disagree"]))}
        for m, k in MODELS:
            pm = h["per"].get(m, {})
            row[f"{k}_wind"], row[f"{k}_gust"] = rnd(pm.get("w10")), rnd(pm.get("g"))
        rows.append(row)
    for path in sorted({month_path(log_dir, "forecasts", r["target_time"]) for r in rows}):
        append_rows(path, FC_COLS, [r for r in rows if month_path(log_dir, "forecasts", r["target_time"]) == path])
    print(f"History: logged {len(rows)} forecast rows.")
    return len(rows)


# ===========================================================================
# 2. LOG OBSERVATIONS (METAR)
# ===========================================================================
def fetch_metars(hours=3):
    q = urllib.parse.urlencode({"ids": ",".join(STATIONS), "format": "json", "hours": hours})
    req = urllib.request.Request("https://aviationweather.gov/api/data/metar?" + q,
                                 headers={"User-Agent": "membury-ppg-forecast history log (personal use)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def log_observations(log_dir, metars, now):
    seen = set()
    for r in read_rows(log_dir, "observations", now - 3 * 86400):
        seen.add((r["icao"], int(float(r["obs_time"]))))
    rows = []
    for o in metars if isinstance(metars, list) else []:
        t, icao = o.get("obsTime"), o.get("icaoId")
        if not B.ok(t) or not icao or (icao, int(t)) in seen:
            continue
        seen.add((icao, int(t)))
        vis = o.get("visib")
        rows.append({"icao": icao, "obs_utc": iso(t), "obs_time": int(t), "wind_kt": o.get("wspd", ""),
                     "gust_kt": o.get("wgst") or "", "dir": o.get("wdir", ""), "vis_sm": vis if vis is not None else "",
                     "temp_c": o.get("temp", ""), "dew_c": o.get("dewp", ""), "raw": o.get("rawOb", "")})
    for path in sorted({month_path(log_dir, "observations", r["obs_time"]) for r in rows}):
        append_rows(path, OBS_COLS, [r for r in rows if month_path(log_dir, "observations", r["obs_time"]) == path])
    print(f"History: logged {len(rows)} new observations.")
    return len(rows)


# ===========================================================================
# 3. REPORT
# ===========================================================================
def wind_level(wind, gust):
    """Rate using only wind, gusts and gust spread, so forecast and observation are judged alike."""
    h = {k: None for k in ("w100", "w100_m", "shear", "shear_m", "alpha", "alpha_m", "prob", "rain", "rain_m", "vis",
                           "vis_m", "base", "base_m", "base_any", "low", "cape", "cape_m", "gust_note")}
    h.update({"wind": wind, "wind_m": None, "gust": gust, "gust_m": None, "n_models": 1, "disagree": False, "each": []})
    return B.rate(h, B.LIMITS, False)[0]


def stats(pairs):
    """pairs: (forecast, observed). Returns n, bias (forecast minus observed), mean absolute error."""
    p = [(f, o) for f, o in pairs if f is not None and o is not None]
    if not p:
        return 0, None, None
    return len(p), sum(f - o for f, o in p) / len(p), sum(abs(f - o) for f, o in p) / len(p)


def fmt(v, signed=False):
    return "-" if v is None else (f"{v:+.1f}" if signed else f"{v:.1f}")


def build_report(log_dir, now):
    since = now - REPORT_DAYS * 86400
    fc = read_rows(log_dir, "forecasts", since)
    obs = [r for r in read_rows(log_dir, "observations", since - 3600) if r["icao"] == STATIONS[0]]
    obs_t = sorted((int(float(r["obs_time"])), r) for r in obs)

    def match(t):
        best = None
        for ot, r in obs_t:
            if abs(ot - t) <= MATCH_MINUTES * 60 and (best is None or abs(ot - t) < abs(best[0] - t)):
                best = (ot, r)
        return best[1] if best else None

    matched = []
    for r in fc:
        t = int(float(r["target_time"]))
        if t > now:
            continue
        o = match(t)
        if not o or num(o["wind_kt"]) is None:
            continue
        ow = num(o["wind_kt"]) * KT_TO_MPH
        og = (num(o["gust_kt"]) or num(o["wind_kt"])) * KT_TO_MPH   # METARs only report significant gusts
        matched.append((r, ow, og))

    L = []
    L.append("# Forecast history report\n")
    L.append(f"Private test report, not linked from the website. Generated {iso(now)}. "
             f"Covers the last {REPORT_DAYS} days.\n")
    L.append(f"**Observations:** METARs from {STATIONS[0]} (RAF Brize Norton, about 30 km from Membury). Forecasts are for "
             f"Membury, so some difference is expected from the distance alone. METAR wind is a 10-minute average at 10 m; "
             f"gusts are only reported when they are significant, otherwise the average wind is used as the gust.\n")
    L.append(f"**Forecast rows logged:** {len(fc)}. **Matched with an observation:** {len(matched)}. "
             f"**Observations from {STATIONS[0]}:** {len(obs)}.\n")
    if not matched:
        L.append("\nNo matches yet. Forecasts need time to come true: the 3-hour rows match after a few hours, "
                 "the 6-day rows after 6 days. Weekend and overnight gaps at military airfields are normal.\n")
    else:
        for label, idx in (("Average wind", 1), ("Gusts", 2)):
            L.append(f"\n## {label}: forecast error by distance ahead (mph)\n")
            L.append("Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. "
                     "n is the number of hours compared.\n")
            head = "| Ahead | Page used (bias / MAE / n) | " + " | ".join(f"{k.upper()} (bias / MAE / n)" for _, k in MODELS) + " |"
            L.append(head)
            L.append("|" + "---|" * (2 + len(MODELS)))
            for b in LEAD_BUCKETS_H:
                rows = [x for x in matched if int(float(x[0]["lead_bucket_h"])) == b]
                if not rows:
                    continue
                col = "wind" if idx == 1 else "gust"
                cells = []
                for key in ["used"] + [k for _, k in MODELS]:
                    n, bias, mae = stats([(num(r[f"{key}_{col}"]), x) for r, ow, og in rows for x in [(ow if idx == 1 else og)]])
                    cells.append(f"{fmt(bias, True)} / {fmt(mae)} / {n}")
                L.append(f"| {b} h | " + " | ".join(cells) + " |")

        L.append("\n## Ratings: did the page call it right? (daylight hours, wind and gust limits only)\n")
        L.append(f"Forecast and observation are both rated against wind {B.LIMITS['max_wind']} mph, gusts {B.LIMITS['max_gust']} mph "
                 f"and gust spread {B.LIMITS['max_spread']} mph. **Missed danger** = the page said Good but it was No-go. "
                 f"**Missed chance** = the page said No-go but it was Good.\n")
        L.append("| Ahead | Hours | Same rating | Missed danger | Missed chance |")
        L.append("|---|---|---|---|---|")
        for b in LEAD_BUCKETS_H:
            rows = [x for x in matched if int(float(x[0]["lead_bucket_h"])) == b and x[0]["daylight"] == "1"]
            if not rows:
                continue
            same = danger = chance = 0
            for r, ow, og in rows:
                fl = wind_level(num(r["used_wind"]), num(r["used_gust"]))
                ol = wind_level(ow, og)
                same += fl == ol
                danger += fl == 0 and ol == 2
                chance += fl == 2 and ol == 0
            n = len(rows)
            L.append(f"| {b} h | {n} | {100 * same / n:.0f}% | {danger} ({100 * danger / n:.0f}%) | {chance} ({100 * chance / n:.0f}%) |")

        L.append("\n## Recent hours, forecast made about a day ahead (mph)\n")
        L.append("| Hour (UTC) | Observed wind / gust | Page used | " + " | ".join(k.upper() for _, k in MODELS) + " | Models disagreed |")
        L.append("|---|---|---|" + "---|" * len(MODELS) + "---|")
        recent = sorted([x for x in matched if int(float(x[0]["lead_bucket_h"])) == 24], key=lambda x: x[0]["target_time"])[-48:]
        for r, ow, og in reversed(recent):
            cells = [f"{r[f'{k}_wind'] or '-'} / {r[f'{k}_gust'] or '-'}" for _, k in MODELS]
            used = f"{r['used_wind'] or '-'} / {r['used_gust'] or '-'} ({r['used_model']})"
            L.append(f"| {r['target_utc']} | {ow:.0f} / {og:.0f} | {used} | " + " | ".join(cells) + f" | {'yes' if r['disagree'] == '1' else ''} |")
    L.append("\n## Files\n")
    L.append("`forecasts-YYYY-MM.csv` and `observations-YYYY-MM.csv` hold the raw log and open in Excel or Google Sheets.\n")
    with open(os.path.join(log_dir, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"History: report written ({len(matched)} matched hours).")


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--cache", default="data/last.json", help="forecast saved by build.py this run")
    ap.add_argument("--now", type=float, help="pretend the current time is this unix time (testing)")
    ap.add_argument("--metar-sample", help="use a saved METAR JSON reply instead of downloading (testing)")
    a = ap.parse_args()
    now = a.now or time.time()
    os.makedirs(a.log_dir, exist_ok=True)
    try:
        with open(a.cache, encoding="utf-8") as f:
            log_forecast(a.log_dir, json.load(f), now)
    except Exception as ex:
        print(f"History: forecast not logged ({ex})", file=sys.stderr)
    try:
        if a.metar_sample:
            with open(a.metar_sample, encoding="utf-8") as f:
                metars = json.load(f)
        else:
            metars = fetch_metars()
        log_observations(a.log_dir, metars, now)
    except Exception as ex:
        print(f"History: observations not logged ({ex})", file=sys.stderr)
    build_report(a.log_dir, now)


if __name__ == "__main__":
    main()
