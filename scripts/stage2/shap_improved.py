#!/usr/bin/env python3
"""Global SHAP importance of the live Stage-2 model (monotone XGBoost, improve_stage2.py),
on the same 5,000-row sample protocol as train_spatial.py.
Out: 08_results/stage2_improved/shap_global_importance.csv"""
import sys
from pathlib import Path
import numpy as np, pandas as pd, joblib, shap
sys.argv += ["--label=connected"]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage2.train_spatial as T  # noqa: E402
from common.config import p  # noqa: E402

M = joblib.load(p("07_models", "stage2_improved", "xgb_final.joblib"))
df = T.load_events()
pool = T.sample_train(df)
sub = pool.sample(min(5000, len(pool)), random_state=0)
sv = shap.TreeExplainer(M["model"]).shap_values(sub[M["features"]])
imp = pd.Series(np.abs(sv).mean(0), index=M["features"]).sort_values(ascending=False)
imp.round(4).to_csv(p("08_results", "stage2_improved", "shap_global_importance.csv"), header=["mean_abs_shap"])
print(imp.round(3).head(10).to_string())
