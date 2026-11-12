# How FloodGuard works

The full report is `00_project/FloodGuard_Project_Context_Master_v4.docx`. This page summarises it and
adds the real-time system built afterwards.

## Study area

The Mahanadi below Tikarpara gorge: Tikarpara (upstream input) → Naraj (head of the delta, near Cuttack)
→ Kishan Nagar → Tarpur. The mapped area is a 15 km buffer around Naraj, Kishan Nagar and Tarpur, clipped
to the CWC Mahanadi basin (about 2,190 km²), on a 100 m grid in UTM zone 45N.

| Gauge | Warning (m) | Danger (m) | Highest flood (m) | Note |
|---|---|---|---|---|
| Naraj | 25.41 | 26.41 | 27.61 (1982) | official CWC levels |
| Kishan Nagar | 14.36 | 15.13 | – | derived from Naraj |
| Tarpur | 9.78 | 10.58 | – | derived from Naraj |
| Tikarpara | – | – | 74.98 (1982) | input only |

Derived levels are the median same-day level at the gauge on days when Naraj was within ±0.25 m of its own
warning or danger level (2015–2025).

## Data

* **Water levels:** CWC manual daily series 2014–2025 (team Drive). The telemetry series was rejected:
  many readings sit near 1,000 m. Quality control is in `scripts/processing/fix_daily_water_level.py`;
  the most important fix nulls 16 Aug 2022, when at least two corridor gauges show the same false V-shaped dip of more than 0.8 m.
* **Rainfall:** CHIRPS v3 daily, averaged over four sub-catchments (above Hirakud, Hirakud–Tikarpara,
  Tikarpara–Naraj, delta floodplain).
* **Flood labels:** Sentinel-1 VV change detection in Earth Engine: drop of more than 3 dB against the same
  orbit's January–April median and below −16 dB, excluding permanent water and slopes above 5°.
  50 scenes in 2015–2025, 48 accepted after visual QC.
* **Static layers:** SRTM elevation and slope, height above the smoothed river surface, distance to the
  channel, JRC water history, ESA WorldCover land-cover fractions.

## Stage 1: when

For each gauge and issue day t the inputs are 7 days of mean and maximum level at the gauge and at
Tikarpara (Naraj too for the downstream gauges), 1–2 day changes, 3- and 7-day maxima, rain per sub-catchment
(today, three lags, 3/5/7/10-day totals) and season: 77 inputs at Naraj. Models predict the change in daily
maximum, which is added to today's maximum (tree models cannot extrapolate absolute levels).

The live forecast is the mean of Ridge, random forest and XGBoost. The models used live are retrained on
2014–2025 (`scripts/stage1/train_operational.py`); the 2014–2020 versions are kept for the method
comparison. The 80% range and the chance of crossing the warning level come from out-of-sample errors:
each year 2022–2025 forecast by models trained only on earlier years, kept separately for days within 2 m
of the warning level. Separate XGBoost models give the +48 h and +72 h outlook.

## Stage 2: where

An XGBoost classifier estimates, for every 100 m cell, the probability of river-connected flooding from the
static layers, the forecast levels (Naraj and the nearest gauge), a depth proxy (gauge level minus ground)
and 1/3/7-day rain. The model is monotone: the probability can only rise when a river level rises. A cell is
flood-likely when its probability reaches cut(L) = max(0.50, 0.90 − 0.05 × max(0, L − 25.41)), L being the
forecast Naraj level, and its patch touches the river (the definition of the training labels). Chosen with
leave-one-year-out tests on the 48 scenes (`scripts/stage2/improve_stage2.py`). SHAP values explain which
inputs push each flood-likely area up.

On the live map, water that the model also shows on ordinary monsoon days (the median conditions of 11
low-stage Sentinel-1 scenes) is drawn as normal monsoon water, and only risk above that reference is counted
as flooding. Settlements within 1 km of elevated or likely cells are listed, with the what-if Naraj level at
which flooding first reaches them.

## Alert rules

| Status | Rule on the +24 h forecast |
|---|---|
| Normal | more than 0.5 m below warning and the 80% upper bound below warning |
| Watch | within 0.5 m of warning, or the 80% upper bound above warning |
| Warning | at or above the warning level |
| Danger | at or above the danger level |

## The live system (final version, 24 October 2026)

* **Service:** `scripts/live/floodguard_server.py`, started by `START_FLOODGUARD.bat` (or at log-in after
  `INSTALL_AUTOSTART.bat`). Standard-library Python only, so any Python 3.8+ runs it.
* **Every 30 minutes** (`config/live.json`) it reads the CWC Flood Forecasting System hourly stage (HHS) for
  034-MDBURLA (Tikarpara), 036-MDBURLA (Naraj), 046-MDBURLA (Kishan Nagar), 047-MDBURLA (Tarpur), and
  Open-Meteo daily rain at 50 points in the four sub-catchments, multiplied per zone by
  k = CHIRPS ÷ Open-Meteo over June–October 2022–2025 (k = 0.956, 1.003, 0.855, 0.886).
* **Checks:** readings outside each gauge's plausible range and isolated single-hour spikes are removed. A gauge
  with no reading for 6 hours is late. If a forecast gauge is late the page shows *Data delayed* and no
  "All clear" is sent; if the upstream gauge is late its last daily value is carried forward for up to two
  days and the page says so. If CWC does not answer, the last readings received are kept and shown as late.
* **Stage 1** runs in the service (`scripts/live/fg_engine.py`, a line-by-line copy of the browser engine,
  reading the same model file `09_dashboard/engine_assets.json`). **Stage 2** runs in the dashboard
  (`scripts/realtime/engine.js`). Both are proven identical to the Python models (`tests/`).
* **Alerts:** the level is the highest of the three gauges. Rising level: alert at once. Falling level: alert
  after two updates in a row. Channels in `config/alerts.json`: computer pop-up, Telegram, e-mail, SMS; the open
  dashboard also pops up and beeps. The message names the gauges, the expected levels, the areas at risk and
  what to do.
* **Logs:** `08_results/live/run_log.csv` (every update), `alerts_log.json`, `cwc_hourly_archive.csv`
  (every reading received, so the system builds its own archive).
* **Demo mode:** replays the July–August or September 2026 flood, one day every 12 seconds, through the same
  code; alerts are marked DEMO.

## Limitations

* Record floods are still mapped a little small (2022 area ratio 0.81 end to end, was 0.58).
* Hirakud dam releases are not a direct input, which limits the +48 h and +72 h outlook.
* Kishan Nagar and Tarpur levels are derived, not official.
* OpenStreetMap lists 26 places in the study area, so one area name can cover several villages.
* Radar cannot see water between buildings, so built-up cells are rarely mapped as flooded.
* The current day's maximum uses readings so far; late-evening runs are the most reliable.

## Next steps

Add Hirakud outflow and inflow forecasts, feed forecast rainfall into Stage 1, use a bare-earth DEM (FABDEM)
with height above nearest drainage, add Sentinel-1 VH and the 2026 scenes to the labels, and widen the 80% range
with quantile or conformal methods (it held on 75–77% of 2026 days).
