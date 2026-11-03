# Forecast history report

Private test report, not linked from the website. Generated 2026-11-03T18:25Z. Covers the last 30 days.

**Observations:** METARs from EGVN (RAF Brize Norton, about 30 km from Membury). Forecasts are for Membury, so some difference is expected from the distance alone. METAR wind is a 10-minute average at 10 m; gusts are only reported when they are significant, otherwise the average wind is used as the gust.

**Forecast rows logged:** 56. **Matched with an observation:** 8. **Observations from EGVN:** 22.


## Average wind: forecast error by distance ahead (mph)

Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. n is the number of hours compared.

| Ahead | Page used (bias / MAE / n) | UKMO (bias / MAE / n) | ECMWF (bias / MAE / n) | GFS (bias / MAE / n) |
|---|---|---|---|---|
| 3 h | -1.4 / 1.4 / 4 | -1.4 / 1.4 / 4 | -1.4 / 1.4 / 4 | +1.0 / 1.0 / 4 |
| 12 h | -2.0 / 2.0 / 4 | -2.0 / 2.0 / 4 | +0.4 / 0.4 / 4 | -1.5 / 1.5 / 4 |

## Gusts: forecast error by distance ahead (mph)

Bias is forecast minus observed (positive = forecast too windy). MAE is the average size of the error. n is the number of hours compared.

| Ahead | Page used (bias / MAE / n) | UKMO (bias / MAE / n) | ECMWF (bias / MAE / n) | GFS (bias / MAE / n) |
|---|---|---|---|---|
| 3 h | +8.9 / 8.9 / 4 | +8.9 / 8.9 / 4 | +7.1 / 7.1 / 4 | +14.4 / 14.4 / 4 |
| 12 h | +10.0 / 10.0 / 4 | +10.0 / 10.0 / 4 | +13.6 / 13.6 / 4 | +8.7 / 8.7 / 4 |

## Ratings: did the page call it right? (daylight hours, wind and gust limits only)

Forecast and observation are both rated against wind 10 mph, gusts 15 mph and gust spread 5 mph. **Missed danger** = the page said Good but it was No-go. **Missed chance** = the page said No-go but it was Good.

| Ahead | Hours | Same rating | Missed danger | Missed chance |
|---|---|---|---|---|
| 12 h | 4 | 100% | 0 (0%) | 0 (0%) |

## Recent hours, forecast made about a day ahead (mph)

| Hour (UTC) | Observed wind / gust | Page used | UKMO | ECMWF | GFS | Models disagreed |
|---|---|---|---|---|---|---|

## Files

`forecasts-YYYY-MM.csv` and `observations-YYYY-MM.csv` hold the raw log and open in Excel or Google Sheets.

