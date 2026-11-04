# Forecast history report

Private test report, not linked from the website. Generated 2026-11-04T06:09Z. Covers the last 30 days.

**Observations:** METARs from EGVN (RAF Brize Norton, about 30 km from Membury). Forecasts are for Membury, so some difference is expected from the distance alone. METAR wind is a 10-minute average at 10 m; gusts are only reported when they are significant, otherwise the average wind is used as the gust.

**Forecast rows logged:** 71. **Matched with an observation:** 16. **Observations from EGVN:** 33.


## Average wind: forecast error by distance ahead (mph)

Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. n is the number of hours compared.

| Ahead | Page used (bias / MAE / n) | UKMO (bias / MAE / n) | ECMWF (bias / MAE / n) | GFS (bias / MAE / n) |
|---|---|---|---|---|
| 3 h | -1.4 / 1.6 / 6 | -1.4 / 1.6 / 6 | -1.0 / 1.2 / 6 | +1.2 / 1.2 / 6 |
| 12 h | -1.5 / 1.6 / 5 | -1.5 / 1.6 / 5 | +0.2 / 0.5 / 5 | -0.9 / 1.4 / 5 |
| 24 h | -1.5 / 1.5 / 5 | -1.5 / 1.5 / 5 | -0.8 / 0.8 / 5 | +1.0 / 1.0 / 5 |

## Gusts: forecast error by distance ahead (mph)

Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. n is the number of hours compared.

| Ahead | Page used (bias / MAE / n) | UKMO (bias / MAE / n) | ECMWF (bias / MAE / n) | GFS (bias / MAE / n) |
|---|---|---|---|---|
| 3 h | +9.1 / 9.1 / 6 | +9.1 / 9.1 / 6 | +7.5 / 7.5 / 6 | +15.3 / 15.3 / 6 |
| 12 h | +10.5 / 10.5 / 5 | +10.5 / 10.5 / 5 | +12.3 / 12.3 / 5 | +11.0 / 11.0 / 5 |
| 24 h | +9.9 / 9.9 / 5 | +9.9 / 9.9 / 5 | +7.8 / 7.8 / 5 | +16.7 / 16.7 / 5 |

## Ratings: did the page call it right? (daylight hours, wind and gust limits only)

Forecast and observation are both rated against wind 10 mph, gusts 15 mph and gust spread 5 mph. **Missed danger** = the page said Good but it was No-go. **Missed chance** = the page said No-go but it was Good.

| Ahead | Hours | Same rating | Missed danger | Missed chance |
|---|---|---|---|---|
| 12 h | 4 | 100% | 0 (0%) | 0 (0%) |

## Recent hours, forecast made about a day ahead (mph)

| Hour (UTC) | Observed wind / gust | Page used | UKMO | ECMWF | GFS | Models disagreed |
|---|---|---|---|---|---|---|
| 2026-11-04T03:00Z | 13 / 13 | 9.6 / 20.4 (UKMO) | 9.6 / 20.4 | 10.3 / 18.1 | 14.9 / 30.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.3 / 21.9 (UKMO) | 10.3 / 21.9 | 11.1 / 19.9 | 12.2 / 28.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.3 / 21.9 (UKMO) | 10.3 / 21.9 | 11.1 / 19.9 | 12.2 / 28.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.3 / 21.9 (UKMO) | 10.3 / 21.9 | 11.1 / 19.9 | 12.2 / 28.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.7 / 22.1 (UKMO) | 10.7 / 22.1 | 11.1 / 19.9 | 12.2 / 28.0 | yes |

## Files

`forecasts-YYYY-MM.csv` and `observations-YYYY-MM.csv` hold the raw log and open in Excel or Google Sheets.

