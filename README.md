# FloodGuard: real-time flood early warning for the Mahanadi delta

FloodGuard forecasts **when** the Mahanadi will flood and **where** the water will go, for the delta
reach Tikarpara → Naraj → Kishan Nagar → Tarpur (Cuttack district, Odisha), and **warns people by
itself**.

* **Stage 1 (when):** tomorrow's highest river level at the three CWC gauges Naraj, Kishan Nagar and
  Tarpur, with a 2 and 3-day outlook, an 80% range and the chance of crossing the warning level.
* **Stage 2 (where):** the chance of flooding for every 100 m of the floodplain (219,017 cells,
  about 2,190 km²), the areas at risk named after the nearest town or village, and why (SHAP).
* **Live system:** every 30 minutes it reads the CWC gauges and the rainfall, runs both stages, updates
  the dashboard and, when the alert level changes, sends an alert (computer pop-up, browser, Telegram,
  e-mail or SMS). Nobody has to click anything.

This is a research prototype, not an official CWC or OSDMA warning.

---

## Start here

1. **Run it live:** double-click **`START_FLOODGUARD.bat`**. A window opens (keep it open, you can
   minimise it) and the dashboard opens at **http://localhost:8765**. It updates by itself every 30
   minutes. Any Python 3.8+ works (Anaconda included); nothing needs installing.
2. **Start it with Windows (optional):** double-click `INSTALL_AUTOSTART.bat` once.
3. **Phone alerts (2 minutes):** in the dashboard open *Alerts and data* and paste a Telegram bot token
   (steps on the page and in `docs/ALERTS_SETUP.md`; free).
4. **Presenting:** `docs/PRESENTATION_GUIDE.md` has a 10-minute demo script, including the
   **Demo flood** button that replays the real July 2026 flood through the live system.
5. **Without the service:** open `09_dashboard/FloodGuard_Live.html` in Chrome or Edge to see the saved
   run and every validation result.

Report: `00_project/FloodGuard_Project_Context_Master_v5.docx` (and `.pdf`). Numbers: `docs/RESULTS.md`.
Method: `docs/HOW_IT_WORKS.md`. Every command: `docs/HOW_TO_RUN.md`.

## Headline results

| What | Result |
|---|---|
| Unseen 2026 monsoon, Naraj, next-day level | error 0.17 m vs 0.35 m for "tomorrow = today" (skill 0.77; 0.87 in Jun–Oct) |
| Warning-level days caught in 2026 | Naraj 20 of 22, Kishan Nagar 21 of 23, Tarpur 33 of 36 (2, 4 and 1 false alarms) |
| Advance notice in 2026 | Watch at least 24 h ahead at all 17 warning onsets (median 72 h); forecast above warning ≥ 24 h ahead at 13 of 17 |
| Test years 2022–2025, Naraj | error 0.107 m, skill 0.79, warning CSI 0.82 |
| Flood maps, 48 radar scenes 2015–2025 | F1 0.64, IoU 0.50 over the 16 floods; 2022 record flood F1 0.68 and 82% of its area (was 0.60 and 57%) |
| Flood maps, 2026 radar (unseen year) | F1 0.77, 0.73, 0.78 (2 Aug, 28 Aug, 1 Oct 2026) |
| Live check | the dashboard and the live server reproduce the Python models exactly (all 219,017 map cells) |

## Final improvements (24 October 2026)

* **Runs by itself.** `scripts/live/floodguard_server.py`: automatic updates every 30 minutes, reading
  checks, "data delayed" protection (never shows Normal from old data), late-gauge handling, run log,
  alerts, demo mode. Standard-library Python only.
* **Live river models retrained on 2014–2025** (they had been trained on 2014–2020): same model types,
  inputs and gauges. On 2026 they catch 74 of 81 warning-level days (was 71) and warn ≥ 24 h ahead at
  13 of 17 onsets (was 9).
* **Better flood maps for big floods, same data.** Probability that can only rise with the river, a
  cut-off that eases above the warning level, river-connected patches only.
* **New dashboard:** plain-language status for a normal reader, areas at risk by name with search,
  homes and buildings near flood-likely land, alert history, data health, light and dark themes,
  phone and projector layouts.
* **One set of numbers** with 95% ranges for the small samples (`docs/RESULTS.md`).

## Folder map

| Folder | What is inside |
|---|---|
| `START_FLOODGUARD.bat` | Starts the live service and opens the dashboard |
| `00_project/` | Project report (v5 final; v3, v4 kept), study area, flood event list, Sentinel-1 QC decisions |
| `01_raw/` | Inputs as downloaded: Earth Engine exports, 2026 Sentinel-1 masks, live CWC and Open-Meteo, OpenStreetMap places, CWC basin |
| `02_reference/` | Manual daily gauge rainfall for Odisha 2021–2025 (source of verified station coordinates) |
| `05_processed/` | Cleaned daily water levels (`daily_water_level_qc2.csv`) and QC log |
| `06_features/` | Model inputs: Stage 1 tables, CHIRPS per sub-catchment, 100 m grid, Stage 2 table per radar scene |
| `07_models/` | All trained models; `stage1_operational/` and `stage2_improved/` are used live |
| `08_results/` | Every result; `live/` holds the live service's run log, alert log and reading archive |
| `09_dashboard/` | The dashboard page, its model file and the live service's static data |
| `scripts/` | All code: `processing`, `aoi`, `gee`, `stage1`, `stage2`, `realtime`, `live` |
| `config/` | `floodguard.yaml` (stations, levels, splits), `live.json` (update interval), `alerts.example.json` |
| `tests/` | Engine parity (browser and server), live-server end-to-end test, recorded live data |
| `docs/` | `RESULTS.md`, `HOW_TO_RUN.md`, `HOW_IT_WORKS.md`, `ALERTS_SETUP.md`, `PRESENTATION_GUIDE.md`, `WORKLOG.md` |

## Install (only needed to re-train or re-run the analysis)

The live service needs only Python. To re-run training and analysis: Python 3.11+, then
`pip install -r requirements.txt`. Node.js 18+ is needed only for `tests/engine_parity.mjs`.

## Key terms

* **Persistence ("tomorrow = today"):** the baseline that tomorrow's highest level equals today's.
  Skill is the share of its squared error a model removes (1 = perfect, 0 = no better).
* **CSI:** hits ÷ (hits + misses + false alarms) for days above the warning level.
* **F1 / IoU:** overlap between the forecast and the radar flooded area (1 = identical).
* **Area ratio:** forecast flooded area ÷ radar flooded area (1 = right size).
* **River-connected:** flooded patches touching the river channel; isolated rain ponding is not counted.
* **Normal monsoon water:** water the map model shows on ordinary monsoon days (11 low-stage scenes);
  only flooding beyond it is reported.

## Data and credits

Water levels: Central Water Commission (manual archive 2014–2025; live from the CWC Flood Forecasting
System). Rainfall: CHIRPS v3 (UCSB-CHC) and Open-Meteo. Radar: Copernicus Sentinel-1 (ESA), processed
in Google Earth Engine. Terrain and land cover: SRTM, ESA WorldCover 2021, JRC Global Surface Water.
Places: © OpenStreetMap contributors.

## Data not included in this repository

To keep the repository small, these large generated files are not committed:

- `01_raw/spatial/events/flood_*.tif`: Sentinel-1 flood masks for each event (exported from Google Earth Engine with `scripts/gee/`)
- `06_features/stage2_events/*.parquet`: per-event Stage 2 feature tables (rebuilt with `scripts/stage2/build_training_table.py`)

`config/alerts.json` is also excluded because it holds private alert keys. Copy `config/alerts.example.json` to `config/alerts.json` and fill in your own values.
