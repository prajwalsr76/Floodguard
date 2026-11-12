# Work log

What was done, in order, and the decisions that shaped the results.

## 1. Understanding the project (20 Oct 2026)

* Read the team Drive (`00_project` first) and the proposal deck. Goal: a two-stage system, Stage 1 = +24 h
  water level at the corridor gauges, Stage 2 = flood map with explanations and early warning.
* Issues found in the master context and fixed:
  * Telemetry water levels were unusable (readings near 1,000 m), so only the manual CWC series is used.
  * The daily table held corrupted single readings (for example Tarpur 356.32 m) and daily changes computed
    across multi-day gaps.
  * Station coordinates were unverified. Naraj and Tarpur were fixed from CWC/NWDP metadata in
    `02_reference`; Kishan Nagar and Tikarpara use OpenStreetMap positions.
  * No warning or danger levels existed for Kishan Nagar and Tarpur, so they were derived from Naraj.

## 2. Data preparation

* `fix_daily_water_level.py`: QC v2 on the daily table. An early version wrongly removed Tikarpara's real
  72.34 m flood peak. The rule now also requires a reading to be more than 2.5 m from the daily mean.
* A corridor-wide false dip on 16 Aug 2022 was found and nulled. This alone raised Naraj skill from 0.67 to 0.77.
* Study area, rain zones and 53 candidate events built (`scripts/aoi`).

## 3. Earth Engine

* One combined script (`floodguard_all_exports.js`) exports CHIRPS v3 zone rainfall, SRTM, WorldCover,
  JRC water and the Sentinel-1 masks directly on the 100 m grid. Run under project
  `secure-cipher-512219-j6` (non-commercial).
* Fix needed: `reduceResolution` failed without a default projection, so the script now sets a 10 m
  projection before averaging.
* 50 scenes exported; visual QC accepted 48 (`00_project/aoi/flood_events_qc.csv`,
  `08_results/stage2/flood_mask_qc/`). Spearman of flooded share vs river level: 0.58.

## 4. Stage 1

* Six models trained with a chronological split; ablation by input group; direct +48/+72 h models.
* Rainfall mostly helps at longer lead times (Naraj +72 h skill 0.38 → 0.54).

## 5. Stage 2

* All-pixel labels gave XGBoost F1 0.46, because isolated rain ponding is not driven by the river.
  River-connected labels raised it to 0.59.
* A cut-off calibrated on training years (0.90) fixed the 3–4× area over-prediction: F1 0.64, area ratio 1.16.
* A height-above-river wedge artefact was removed by smoothing the river surface.
* Four end-to-end case studies (2022–2025), each with the Stage 2 model retrained without that year.

## 6. Real-time system (21 Oct 2026)

* Found the CWC Flood Forecasting System API for all four gauges. Its values match the archive
  (97–99% of 2025 days have the identical daily maximum).
* Open-Meteo rainfall rescaled per zone to CHIRPS (CHIRPS arrives weeks late).
* Frozen models run on the whole of 2026 as an unseen season: Naraj skill 0.77, 20 of 22 warning days caught.
* A normal-monsoon reference was added to Stage 2 so ordinary river-side water is not reported as a flood.

## 7. Live dashboard v2 (21 Oct 2026, evening)

* Exact in-browser engine for all models (`engine.js`), with a parity test against Python.
* 2026 Sentinel-1 check on three overpasses (F1 0.78 / 0.73 / 0.78).
* Advance-notice analysis: a Watch at least 24 h ahead at all 17 warning onsets of 2026.
* What-if flood-growth animation; settlement trigger levels; plain-text bulletin.

## 8. Master folder (21 Oct 2026)

* Everything collected into this folder. The 262 MB Stage 2 training table is now stored as one file per
  scene (`06_features/stage2_events/`) so every file stays small enough to share. Results reproduce
  identically from this folder: the case-study maps and the live run were re-run from here.

## 9. Final version (24 Oct 2026)

* **Live river models retrained on 2014–2025** with the same model types, inputs and gauges
  (`train_operational.py`). Uncertainty from rolling-origin out-of-sample errors. On 2026: 74 of 81 warning
  days caught (old models 71), warning ≥ 24 h ahead at 13 of 17 onsets (old 9); Naraj error unchanged
  (0.166 m), Kishan Nagar all-year error up (0.169 → 0.183 m). Decision to retrain made before scoring 2026.
* **Stage 2 improved with the same data** (`improve_stage2.py`): nine rule variants scored with leave-one-year-out
  tests, all with the same average accuracy (F1 0.637–0.644). Chosen: monotone XGBoost + level-aware cut-off +
  river connectivity (physically consistent; best on big floods). 2022 record flood F1 0.60 → 0.68, area
  57% → 82%. 2026 radar scores unchanged (mean F1 0.76).
* **Live service** (`scripts/live/`): automatic updates every 30 min, reading checks, data-delayed protection,
  late-upstream handling (carry forward, tested: Naraj within 5 cm), last-good-reading fallback, alerts with
  hysteresis to desktop / browser / Telegram / e-mail / SMS, run and alert logs, demo replays of 2026 floods.
  Standard-library only; its Stage 1 engine is proven identical to Python.
* **Dashboard rebuilt** for a general reader: status band with plain advice, gauges, areas at risk named after
  the nearest place with search and map outline, homes and buildings within 500 m, alert history, data health,
  themes, responsive layout. The flood map is drawn in the page from the live forecast.
* **CWC API checked on 24 Oct 2026** (Naraj 21.59 m at 18:00 IST, same format).
* `docs/RESULTS.md` is now the single source of numbers, with 95% ranges for small samples.

## 10. Wind-up (24 Oct 2026, night)

* One-click Telegram set-up in the dashboard (paste a bot token; the service finds who pressed Start, saves
  `config/alerts.json`, sends a welcome). Tested end to end against a stand-in Telegram server.
* Fixed: live and demo alerts could overwrite each other's history (one shared log now); the Telegram button
  stayed disabled when the page opened directly on the Alerts tab.
* Project report v5 (`00_project/FloodGuard_Project_Context_Master_v5.docx`) with every final number;
  presentation deck (14 slides).
