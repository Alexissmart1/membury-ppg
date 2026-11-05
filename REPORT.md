# Forecast history report

Private test report, not linked from the website. Generated 2026-11-05T09:26Z. Covers the last 30 days.

**Observations:** METARs from EGVN (RAF Brize Norton, about 30 km from Membury). Forecasts are for Membury, so some difference is expected from the distance alone. METAR wind is a 10-minute average at 10 m; gusts are only reported when they are significant, otherwise the average wind is used as the gust.

**Forecast rows logged:** 103. **Matched with an observation:** 27. **Observations from EGVN:** 54.


## Average wind: forecast error by distance ahead (mph)

Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. n is the number of hours compared.

| Ahead | Page used (bias / MAE / n) | UKMO (bias / MAE / n) | ECMWF (bias / MAE / n) | GFS (bias / MAE / n) |
|---|---|---|---|---|
| 3 h | -1.7 / 1.8 / 8 | -1.7 / 1.8 / 8 | -1.4 / 1.6 / 8 | +1.2 / 1.5 / 8 |
| 12 h | -2.1 / 2.1 / 7 | -2.1 / 2.1 / 7 | -0.1 / 0.6 / 7 | -0.2 / 1.6 / 7 |
| 24 h | -1.9 / 1.9 / 8 | -1.9 / 1.9 / 8 | -1.2 / 1.2 / 8 | +0.8 / 1.1 / 8 |
| 48 h | -3.8 / 3.8 / 4 | -5.5 / 5.5 / 4 | -3.8 / 3.8 / 4 | -0.7 / 0.7 / 4 |

## Gusts: forecast error by distance ahead (mph)

Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. n is the number of hours compared.

| Ahead | Page used (bias / MAE / n) | UKMO (bias / MAE / n) | ECMWF (bias / MAE / n) | GFS (bias / MAE / n) |
|---|---|---|---|---|
| 3 h | +9.2 / 9.2 / 8 | +9.2 / 9.2 / 8 | +7.2 / 7.2 / 8 | +14.3 / 14.3 / 8 |
| 12 h | +10.2 / 10.2 / 7 | +10.2 / 10.2 / 7 | +12.2 / 12.2 / 7 | +12.1 / 12.1 / 7 |
| 24 h | +9.3 / 9.3 / 8 | +9.3 / 9.3 / 8 | +7.6 / 7.6 / 8 | +15.5 / 15.5 / 8 |
| 48 h | +5.8 / 5.8 / 4 | +2.3 / 2.3 / 4 | +5.8 / 5.8 / 4 | +10.7 / 10.7 / 4 |

## Ratings: did the page call it right? (daylight hours, wind and gust limits only)

Forecast and observation are both rated against wind 10 mph, gusts 15 mph and gust spread 5 mph. **Missed danger** = the page said Good but it was No-go. **Missed chance** = the page said No-go but it was Good.

| Ahead | Hours | Same rating | Missed danger | Missed chance |
|---|---|---|---|---|
| 12 h | 5 | 100% | 0 (0%) | 0 (0%) |
| 24 h | 1 | 100% | 0 (0%) | 0 (0%) |

## Recent hours, forecast made about a day ahead (mph)

| Hour (UTC) | Observed wind / gust | Page used | UKMO | ECMWF | GFS | Models disagreed |
|---|---|---|---|---|---|---|
| 2026-11-05T07:00Z | 14 / 14 | 10.3 / 20.4 (UKMO) | 10.3 / 20.4 | 10.7 / 19.5 | 12.8 / 24.8 |  |
| 2026-11-05T00:00Z | 13 / 13 | 10.3 / 20.1 (UKMO) | 10.3 / 20.1 | 10.5 / 18.3 | 14.1 / 26.2 |  |
| 2026-11-04T19:00Z | 14 / 14 | 12.1 / 24.8 (UKMO) | 12.1 / 24.8 | 13.8 / 23.9 | 15.1 / 30.0 |  |
| 2026-11-04T03:00Z | 13 / 13 | 9.6 / 20.4 (UKMO) | 9.6 / 20.4 | 10.3 / 18.1 | 14.9 / 30.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.3 / 21.9 (UKMO) | 10.3 / 21.9 | 11.1 / 19.9 | 12.2 / 28.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.3 / 21.9 (UKMO) | 10.3 / 21.9 | 11.1 / 19.9 | 12.2 / 28.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.3 / 21.9 (UKMO) | 10.3 / 21.9 | 11.1 / 19.9 | 12.2 / 28.0 | yes |
| 2026-11-03T23:00Z | 12 / 12 | 10.7 / 22.1 (UKMO) | 10.7 / 22.1 | 11.1 / 19.9 | 12.2 / 28.0 | yes |

## Files

`forecasts-YYYY-MM.csv` and `observations-YYYY-MM.csv` hold the raw log and open in Excel or Google Sheets.

