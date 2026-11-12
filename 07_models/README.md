# 07_models: trained models (Python joblib / PyTorch)

Used live:
* `stage1_operational/<gauge>.joblib`: Ridge, random forest and XGBoost (+24 h) and XGBoost +48 h / +72 h,
  trained on 2014-2025, with input list, missing-input fill values and out-of-sample error banks.
* `stage2_improved/xgb_final.joblib`: the flood-map model (monotone XGBoost, river-connected labels) with its
  cut-off rule and connectivity setting.

Kept for the method comparison:
* `stage1/<gauge>_tabular.joblib`: Ridge, random forest, XGBoost trained 2014-2020; `_LSTM_seed*.pt`,
  `_GRU_seed*.pt`, `_seq_meta.joblib`: sequence baselines.
* `stage1_multi/<gauge>_h23.joblib`: +48 h and +72 h XGBoost trained 2014-2020.
* `stage2_connected/xgb_final.joblib`: the previous map model (cut-off 0.90); `stage2/`: all-pixel labels.

The live service and the dashboard read the same models exported to `09_dashboard/engine_assets.json`.
