# FloodGuard results

This page is the one source for every number. The README, the dashboard and the report use these
values. Each table names the file in `08_results/` it comes from. Final version: 24 October 2026.

**What changed in the final version (24 Oct 2026)**

1. The live river-level models were retrained on 2014–2025 (they had been trained on 2014–2020 only).
   2026 is still never used for training, so the 2026 season remains a true test (section 1.3).
2. The flood-map rule was improved with the same data and inputs (section 2.1): a model whose flood
   probability can only rise with the river, a cut-off that eases above the Naraj warning level, and
   only river-connected patches counted. The 2022 record flood is now mapped at 82% of its real area
   (was 57%).
3. The system now runs by itself (section 4) and sends alerts.

Small samples: 2026 has 22 warning-level days at Naraj and 17 flood onsets over the three gauges, so
95% ranges (Wilson) are given for the key proportions.

---

## 1. Stage 1: tomorrow's highest river level

### 1.1 Method comparison, test years 2022–2025 (models trained 2014–2020, tuned on 2021)

| Gauge | Model | RMSE (m) | MAE (m) | Skill vs "tomorrow = today" | Warning-level CSI |
|---|---|---|---|---|---|
| Naraj | Tomorrow = today (persistence) | 0.234 | 0.091 | 0.00 | 0.45 |
| Naraj | Ridge | 0.134 | 0.067 | 0.67 | 0.82 |
| Naraj | Random forest | 0.107 | 0.043 | 0.79 | 0.88 |
| Naraj | XGBoost | 0.109 | 0.046 | 0.78 | 0.76 |
| Naraj | LSTM | 0.134 | 0.057 | 0.67 | 0.67 |
| Naraj | GRU | 0.130 | 0.057 | 0.69 | 0.67 |
| Naraj | **Ensemble (Ridge + RF + XGBoost)** | **0.107** | 0.047 | **0.79** | 0.82 |
| Kishan Nagar | Persistence | 0.256 | 0.171 | 0.00 | 0.50 |
| Kishan Nagar | Ensemble | 0.180 | 0.125 | 0.51 | 0.73 |
| Tarpur | Persistence | 0.280 | 0.171 | 0.00 | 0.54 |
| Tarpur | Ensemble | 0.171 | 0.110 | 0.63 | 0.79 |

Source: `08_results/stage1/metrics_all_targets.csv`; ensemble from `scripts/realtime/build_dashboard.py`.
Test days: Naraj 1,256, Kishan Nagar 1,143, Tarpur 826. The ensemble is used live because it is as
accurate as the best single model at Naraj and more stable across gauges.

### 1.2 The live models: same types, retrained on 2014–2025

`scripts/stage1/train_operational.py`. Same 77 inputs, same four gauges, same rainfall. XGBoost tree
count by early stopping on 2025, then refit on 2014–2025. The 80% range and the chance of crossing the
warning level come from honest out-of-sample errors: each year 2022–2025 forecast by models trained only
on the years before it ("rolling origin").

The rolling-origin forecasts also show that retraining each year does not lose accuracy on the same
2022–2025 days (`08_results/stage1/operational_vs_frozen.csv`):

| Gauge | Trained 2014–2020 (RMSE / skill / CSI) | Retrained each year (RMSE / skill / CSI) |
|---|---|---|
| Naraj | 0.107 m / 0.79 / 0.82 | 0.110 m / 0.78 / 0.82 |
| Kishan Nagar | 0.180 m / 0.51 / 0.73 | 0.172 m / 0.55 / 0.73 |
| Tarpur | 0.171 m / 0.63 / 0.79 | 0.163 m / 0.66 / 0.83 |

### 1.3 The 2026 monsoon: a season no model had seen

Every forecast issued one day ahead from the live CWC readings and rainfall, scored 1 Jan – 21 Oct 2026.
Live models trained on 2014–2025.

| Gauge | Days | RMSE (m) | Persistence RMSE (m) | Skill (Jun–Oct) | Warning days caught (95% range) | False alarms | CSI (persistence) | Inside 80% range |
|---|---|---|---|---|---|---|---|---|
| Naraj | 288 | 0.166 | 0.347 | 0.77 (0.87) | 20 of 22 (72–98%) | 2 | 0.83 (0.63) | 77% |
| Kishan Nagar | 287 | 0.183 | 0.274 | 0.55 (0.74) | 21 of 23 (73–98%) | 4 | 0.78 (0.53) | 77% |
| Tarpur | 288 | 0.154 | 0.286 | 0.71 (0.84) | 33 of 36 (78–97%) | 1 | 0.89 (0.76) | 75% |

The 2026 peak at Naraj was 26.61 m on 30 July, above the 26.41 m danger level.
Source: `08_results/realtime/dashboard_data.json` → `verification_2026`.

**Advance notice.** The three gauges crossed their warning levels 17 times in 2026. A Watch was issued
at least 24 h ahead all 17 times (95% range 82–100%), with a median of 72 h ahead. The forecast was
already above the warning level at least 24 h ahead 13 times out of 17 (53–90%).

**Retrained versus old models on 2026** (identical inputs; the decision to retrain was made before this
comparison; `08_results/realtime/stage1_2026_frozen_vs_operational.csv`):

| | Warning days caught (3 gauges) | Missed | False alarms | Warning ≥ 24 h ahead | Naraj RMSE |
|---|---|---|---|---|---|
| Old: trained 2014–2020 | 71 of 81 | 10 | 6 | 9 of 17 | 0.166 m |
| **Live: trained 2014–2025** | **74 of 81** | **7** | 7 | **13 of 17** | 0.166 m |

Monsoon skill rose at Kishan Nagar (0.72 → 0.74) and Tarpur (0.80 → 0.84); over the whole year Kishan
Nagar's error rose (0.169 → 0.183 m), mostly on low dry-season levels.

### 1.4 How far ahead (direct XGBoost models, test years 2022–2025)

| Gauge | +24 h skill | +48 h skill | +72 h skill | +24 / +48 / +72 h CSI |
|---|---|---|---|---|
| Naraj | 0.78 | 0.65 | 0.54 | 0.77 / 0.36 / 0.27 |
| Kishan Nagar | 0.51 | 0.53 | 0.50 | 0.71 / 0.42 / 0.25 |
| Tarpur | 0.64 | 0.67 | 0.59 | 0.81 / 0.52 / 0.28 |

Source: `08_results/stage1/lead_time_all_targets.csv`. Without rain inputs the Naraj +72 h skill is 0.38.

---

## 2. Stage 2: where it floods

### 2.1 Leave-one-year-out cross-validation, 48 Sentinel-1 scenes 2015–2025

Averaged over the 16 scenes with Naraj above the warning level; river-connected labels; area ratio is
the median. Each year is held out in turn, and cut-off parameters are chosen without the held-out year.

| Map model | F1 | IoU | Area ratio | 2022 record flood: F1 / area ratio |
|---|---|---|---|---|
| Bathtub (river level above ground) | 0.06 | 0.03 | 39.2 | – |
| Logistic regression, cut-off 0.5 | 0.53 | 0.38 | 4.03 | – |
| Random forest, cut-off 0.5 | 0.59 | 0.45 | 3.68 | – |
| XGBoost, cut-off 0.5 | 0.59 | 0.44 | 3.44 | – |
| Before: XGBoost, fixed cut-off 0.90 | 0.64 | 0.50 | 1.16 | 0.60 / 0.57 |
| **Live: monotone XGBoost, level-aware cut-off, river-connected** | **0.64** | **0.50** | 1.25 | **0.68 / 0.82** |

Sources: `08_results/stage2_connected/cv_summary.csv`, `08_results/stage2_improved/variants_summary.csv`,
`per_event.csv`, `summary.json`.

**The improvement** (`scripts/stage2/improve_stage2.py`, same data, stations and inputs):

* *Monotone model.* The same XGBoost with the same training sample, constrained so the flood probability
  can only rise when a river level rises. Trees otherwise make arbitrary jumps above the levels seen in
  training, which is where record floods are.
* *Level-aware cut-off.* cut(L) = max(0.50, 0.90 − 0.05 × max(0, L − 25.41)), L = forecast Naraj level.
  Equal to the old 0.90 up to the warning level, slightly lower for big floods.
* *River connectivity.* Only flood-likely patches touching the river count, the same rule that defines
  the training labels.

All nine combinations tested have the same average accuracy (F1 0.637–0.644). The live one was chosen
because it is the physically consistent one; it is also the best on the largest floods (2020: F1 0.58 →
0.62; 2022: 0.60 → 0.68). Because big-flood scores were part of that choice, the 2026 radar scenes,
never used in any choice, are the independent check (2.3).

Feature groups (`08_results/stage2_connected/ablation.csv`): terrain only F1 0.33 → + river proximity 0.56
→ + land cover 0.59 → + river levels 0.59 → + rainfall 0.59 (cut-off 0.5).
Most important inputs of the live model (mean |SHAP|): distance to river 3.11, past water frequency 1.14,
bare sand 0.68, tree cover 0.67, Naraj level 0.49 (`08_results/stage2_improved/shap_global_importance.csv`).

### 2.2 End-to-end case studies 2022–2025

Stage 1 forecast issued the day before (models trained 2014–2020, so these years are unseen by Stage 1),
then the map model retrained without that year (`08_results/realtime/case_layers.json`).

| Flood | Naraj forecast / observed (m) | F1 now (before) | IoU now (before) | Area ratio now (before) | ROC-AUC |
|---|---|---|---|---|---|
| 16 Aug 2022 (record) | 27.97 / 27.47 | **0.67** (0.60) | 0.51 (0.43) | **0.81** (0.58) | 0.965 |
| 16 Sep 2023 | 26.28 / 25.96 | 0.78 (0.80) | 0.63 (0.66) | 1.42 (1.15) | 0.997 |
| 10 Aug 2024 | 25.10 / 24.98 | 0.76 (0.76) | 0.61 (0.61) | 0.99 (0.99) | 0.995 |
| 7 Sep 2025 | 25.58 / 25.99 | 0.76 (0.75) | 0.61 (0.60) | 1.16 (1.15) | 0.994 |

### 2.3 2026 check against Sentinel-1 (unseen year)

The map FloodGuard would have issued the day before each overpass, from live data.

| Overpass | Area covered | Naraj forecast / observed (m) | Precision | Recall | F1 now (before) | IoU | Area ratio |
|---|---|---|---|---|---|---|---|
| 2 Aug 2026 | 50% | 25.53 / 25.89 | 0.74 | 0.81 | 0.77 (0.78) | 0.63 | 1.10 |
| 28 Aug 2026 | 89% | 25.64 / 25.73 | 0.62 | 0.88 | 0.73 (0.73) | 0.57 | 1.43 |
| 1 Oct 2026 | 50% | 26.00 / 26.27 | 0.78 | 0.79 | 0.78 (0.78) | 0.64 | 1.01 |

The 2026 floods peaked near the warning-to-danger range, where the new rule equals the old one, so the
2026 scores are unchanged (mean F1 0.76): the improvement for record floods costs nothing on ordinary
floods. Source: `08_results/realtime/s1_2026_scores.csv`.

---

## 3. Checks that the live system computes exactly what was validated

* **Dashboard engine** (`node tests/engine_parity.mjs`): on four issue days (21 Oct, 29 Jul, 27 Sep,
  27 Aug 2026) the in-page Stage 1 forecasts agree with Python within 0.00001 m, all 219,017 map cells
  get the identical risk class (including the level-aware cut-off and the river-connectivity rule),
  village classes and areas are identical, and SHAP drivers agree within 0.0015.
* **Live server engine** (`python tests/server_engine_parity.py`): the server's standard-library Stage 1
  engine gives the same forecasts, ranges, chances and levels as Python on the same four days.
* **Recorded live data** (`tests/test_live_server.py`): the server, fed the CWC and Open-Meteo answers
  recorded on 21 Oct 2026, forecasts Naraj 22.003 m, identical to the Python pipeline.

## 4. The live system

* Reads the four CWC gauges and the rainfall every 30 minutes, with no clicking
  (`scripts/live/floodguard_server.py`; start with `START_FLOODGUARD.bat` or at log-in).
* Checks every reading (plausible range, single-hour spikes); marks a gauge late after 6 hours without
  a reading; never shows "Normal" or sends "All clear" from late data.
* Alert levels Normal / Watch / Warning / Danger; an alert when the level rises, and when it falls after
  two updates in a row. Channels: pop-up on the computer, the browser, Telegram, e-mail, SMS.
* Demo mode replays the July–August or September 2026 flood through the same models and alerts. In the
  July replay FloodGuard raises Watch on 22 Jul, Warning on 24 Jul and Danger on the evening of 29 Jul,
  a day before the 26.61 m peak of 30 Jul.

## 5. Known weaknesses

* Record floods are still mapped a little small (2022 area ratio 0.81 end to end); only two training
  scenes are above Naraj 26.5 m.
* Small floods and low-stage scenes are over-mapped (some area ratios above 2), because thin
  river-side strips are hard to place at 100 m.
* The 80% range held on 75–77% of 2026 days, a little narrower than intended.
* Skill falls with lead time (Naraj +72 h skill 0.54); Hirakud dam releases are not a direct input.
* Kishan Nagar and Tarpur warning and danger levels are derived from Naraj, not published by CWC.
* Radar cannot see water between buildings, so built-up cells are rarely mapped as flooded; the page
  reports homes and buildings within 500 m of flood-likely land instead.
* Flood labels are our own Sentinel-1 masks and have not been compared with official NRSC/Bhuvan maps.
