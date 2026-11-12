# How to run FloodGuard

Run every command from the project root (the folder that contains `README.md`), after
`pip install -r requirements.txt`. All outputs are already in this folder, so each step is optional;
re-running a step overwrites its outputs with the same results.

Paths and settings come from `config/floodguard.yaml`.

## 0. Water-level quality control

| Command | What it does |
|---|---|
| `python scripts/processing/fix_daily_water_level.py` | Cleans `05_processed/water_level/manual/daily_water_level_cleaned.csv` (from the Drive): removes isolated days, out-of-range and single-spike readings, the corrupted 16 Aug 2022 corridor-wide dip, and recomputes gap-aware daily change. Writes `daily_water_level_qc2.csv` and `qc2_log.csv`. **Every later step reads `qc2`.** |
| `python scripts/processing/process_water_level.py` | The same rules applied to raw sub-daily CWC CSVs (the 540 MB raw download kept in the Drive). Not needed to reproduce the results. |

## 1. Study area and flood events

```
python scripts/aoi/extract_station_coords.py   # station coordinates into the config (needs raw CWC CSVs)
python scripts/aoi/make_aoi.py                 # AOI (15 km around the corridor, clipped to the basin), rain zones
python scripts/aoi/make_flood_events.py        # 53 candidate events: 16 above warning, 26 moderate rises, 11 low-stage references
```
Outputs: `00_project/aoi/`.

## 2. Google Earth Engine exports

```
python scripts/gee/render_combined_script.py   # writes scripts/gee/floodguard_all_exports.js (AOI, zones, events and grid embedded)
```
Paste the script into the Earth Engine Code Editor, run it, start all tasks in the Tasks tab, download the
`FloodGuard_GEE` Drive folder, then:
```
python scripts/gee/import_gee_exports.py 01_raw/spatial/gee_downloads
```
The downloads used for the results are already in `01_raw/spatial/gee_downloads/`.
Sentinel-1 masks were checked by eye; decisions are in `00_project/aoi/flood_events_qc.csv` (48 of 50 accepted).

## 3. Stage 1 (when)

```
python scripts/stage1/prepare_chirps.py    # CHIRPS zone rainfall -> 06_features/chirps_daily_subcatchments.csv
python scripts/stage1/build_features.py    # 77 inputs per day per gauge -> 06_features/stage1_<gauge>.csv
python scripts/stage1/train_models.py      # Persistence, Ridge, RF, XGBoost, LSTM, GRU + ablation (about 10 min)
python scripts/stage1/multi_horizon.py     # +24/+48/+72 h lead-time test
```
Outputs: `07_models/stage1/`, `08_results/stage1/`.

## 4. Stage 2 (where)

```
python scripts/stage2/build_grid.py                         # 100 m grid and static layers -> 06_features/stage2_cells.parquet
python scripts/stage2/build_training_table.py               # cell x scene table -> 06_features/stage2_events/
python scripts/stage2/qc_flood_masks.py                     # flood-mask QC sheets -> 08_results/stage2/flood_mask_qc/
python scripts/stage2/train_spatial.py --label=connected    # leave-one-year-out CV, ablation, SHAP, final model (about 30 min)
python scripts/stage2/tune_threshold.py --label=connected   # calibrated cut-off (0.90) (about 10 min)
python scripts/stage2/case_study.py --label=connected E20220817 E20230916 E20240809 E20250907
python scripts/stage2/forecast_and_warn.py --date 2023-09-15   # one-off map and bulletin for any archive date
```
Without `--label=connected` the same scripts use all-pixel labels and write to `08_results/stage2/`.

## 5. Final improvements (24 Oct 2026)

```
python scripts/stage1/train_operational.py     # live river models retrained on 2014-2025 (about 3 min)
python scripts/stage2/improve_stage2.py        # Stage 2 map-rule study, final monotone model (about 4 min)
python scripts/stage2/shap_improved.py         # SHAP importance of the live map model
```
Outputs: `07_models/stage1_operational/`, `07_models/stage2_improved/`, `08_results/stage1/operational_*.csv`,
`08_results/stage2_improved/`.

## 6. Analysis run and dashboard build

```
python scripts/realtime/run_realtime.py         # 2026 test, maps, what-if, areas -> 08_results/realtime/
python scripts/realtime/compare_stage1_2026.py  # old vs retrained models on 2026
python scripts/realtime/verify_s1_2026.py       # score against the 2026 Sentinel-1 masks
python scripts/realtime/export_case_layers.py   # 2022-2025 case-study maps
python scripts/realtime/export_js_models.py     # models -> engine_assets.json (self-checks every model)
python scripts/realtime/parity_reference.py     # Python reference outputs
python scripts/realtime/build_dashboard.py      # page -> 08_results/realtime/ and 09_dashboard/
```
To use fresh inputs for the analysis run first: `python scripts/realtime/fetch_live_inputs.py`
(or `python scripts/realtime/update_dashboard.py`, which runs fetch, analysis and build).

## 7. Live service

```
START_FLOODGUARD.bat                            # Windows: double-click
python scripts/live/floodguard_server.py        # any system
```
Dashboard: http://localhost:8765. Settings: `config/live.json`. Alerts: `config/alerts.json`
(`docs/ALERTS_SETUP.md`). Autostart at log-in: `INSTALL_AUTOSTART.bat` (undo: `UNINSTALL_AUTOSTART.bat`).

## 8. Tests

```
node tests/engine_parity.mjs                    # dashboard engine == Python (ALL PARITY CHECKS PASSED)
python tests/server_engine_parity.py            # live server engine == Python
python tests/test_live_server.py                # service end to end on recorded live data, incl. demo alerts
```

## Rules that keep the evaluation honest

* Chronological splits only: for the method comparison Stage 1 trains on 2014–2020, tunes on 2021, tests on
  2022–2025. The live models use 2014–2025 and are tested on 2026. Stage 2 holds out one whole year at a time.
* Scalers, thresholds and the Stage 2 cut-off parameters are fitted without the held-out data.
* Stage 1 inputs use only the target gauge and gauges upstream of it, up to the end of the issue day.
* Stage 2 labels are paired with the river level on the Sentinel-1 date; rainfall totals end the day before.
* At forecast time Stage 2 receives Stage 1's predicted levels, never observed ones.
* 2026 data never touched training: every 2026 number is a true out-of-sample test.
