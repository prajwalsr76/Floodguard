# 08_results: every result

* `stage1/`: all models and gauges (2022-2025 test), ablation, lead time, predictions, plots, logs;
  `operational_rolling_2022_2025.csv` and `operational_vs_frozen.csv` for the retrained live models.
* `stage2/`, `stage2_connected/`: map-model CV, ablation, SHAP, flood-mask QC sheets, case studies (previous rule).
* `stage2_improved/`: the Stage 2 improvement study (`variants_summary.csv`, `per_event.csv`, `summary.json`)
  and SHAP of the live map model.
* `realtime/`: the analysis run behind the dashboard (`dashboard_data.json`), 2026 Sentinel-1 scores
  (`s1_2026_scores.csv`), old-vs-new 2026 comparison (`stage1_2026_frozen_vs_operational.csv`), case-study map
  layers, model file, parity reference and the page.
* `live/`: written by the live service: `run_log.csv`, `alerts_log.json`, `cwc_hourly_archive.csv`, `latest.json`.

All numbers on one page: `docs/RESULTS.md`.
