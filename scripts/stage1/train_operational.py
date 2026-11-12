#!/usr/bin/env python3
"""
FloodGuard Stage 1 - OPERATIONAL models (the ones the live system runs).

Why: the research models in 07_models/stage1/ were trained on 2014-2020 only (so that
2022-2025 could be an untouched test). That was right for the method comparison, but a
live system should learn from every year it has, including the 2022 record flood.
This script retrains the SAME model types with the SAME inputs on 2014-2025.
2026 is never used, so the 2026 season stays a genuine out-of-sample test.

Protocol (nothing is tuned on 2026)
  * Inputs, targets, gauges, rainfall: identical to build_features.py (77 inputs at Naraj).
  * Ridge (alpha 1, standardised), random forest (400 trees, leaf 3, max_features 0.5)
    and XGBoost (same hyper-parameters as train_models.py). Live forecast = their mean.
  * XGBoost tree count: early stopping with 2014-2024 as training and 2025 as validation,
    then refit on 2014-2025 with that many trees. Same for the +48 h / +72 h models.
  * Uncertainty (80% range, chance of crossing warning): residuals of honest
    rolling-origin forecasts. For each year Y in 2022..2025 the models are fitted on
    2014..Y-2, early-stopped on Y-1 and used to forecast Y. Every residual is therefore
    out-of-sample. Split by regime (today within 2 m of warning or not), as before.
  * Missing-input fill for Ridge/RF: medians over the 2014-2025 training rows.

Output
  07_models/stage1_operational/<gauge>.joblib
  08_results/stage1/operational_rolling_2022_2025.csv   (rolling-origin skill per year)
  08_results/stage1/operational_vs_frozen.csv           (same years, same rows: frozen-2020 vs rolling)
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

warnings.filterwarnings("ignore")
CFG = load_config()
S1 = CFG["stage1"]
THR = CFG["thresholds"]
TARGETS = S1["targets"]
REGIME_BAND_M = 2.0
LAST_TRAIN_YEAR = 2025
ROLL_YEARS = [2022, 2023, 2024, 2025]
XGB_KW = dict(learning_rate=0.03, max_depth=4, subsample=0.8, colsample_bytree=0.7,
              min_child_weight=3, reg_lambda=1.0, random_state=0, n_jobs=2)


def slug(s):
    return s.lower().replace(" ", "_")


def load(target):
    X = pd.read_csv(p("06_features", f"stage1_{slug(target)}.csv"), parse_dates=["date"]).set_index("date")
    feats = joblib.load(p("07_models", "stage1", f"{slug(target)}_tabular.joblib"))["features"]
    X = X[X.index.year <= LAST_TRAIN_YEAR]
    d = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    s = d[d.station == target].set_index("date").daily_max_m.asfreq("D")
    s = s[s.index.year <= LAST_TRAIN_YEAR]
    return X, feats, s


def target_h(X, s, h):
    """Daily max on day t+h (h=1 is the +24 h target already in the table)."""
    return X["y_next_max"] if h == 1 else s.shift(-h).reindex(X.index)


def fit_xgb_es(Xtr, ytr, Xva, yva):
    m = xgb.XGBRegressor(n_estimators=2000, early_stopping_rounds=100, **XGB_KW)
    m.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    return m.best_iteration + 1


def fit_xgb_n(Xtr, ytr, n):
    m = xgb.XGBRegressor(n_estimators=int(n), **XGB_KW)
    m.fit(Xtr, ytr, verbose=False)
    return m


def fit_members(X, F, dy, rows, fill):
    Xf = X.loc[rows, F].fillna(pd.Series(fill, index=F))
    ridge = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(Xf, dy[rows])
    rf = RandomForestRegressor(n_estimators=400, min_samples_leaf=3, max_features=0.5, n_jobs=2,
                               random_state=0).fit(Xf, dy[rows])
    return ridge, rf


def predict_ens(X, F, rows, fill, ridge, rf, xg):
    Xr = X.loc[rows, F]
    Xf = Xr.fillna(pd.Series(fill, index=F))
    base = X.loc[rows, "base_max"]
    return base + (ridge.predict(Xf) + rf.predict(Xf) + xg.predict(Xr)) / 3


def run(target):
    X, F, s = load(target)
    ok = X[F].notna().all(axis=1)
    yr = X.index.year
    w = THR[target]["warning"]
    out = {"features": F, "trained_years": "2014-2025", "h": {}}
    roll_rows = []

    # ---------------- +24 h ensemble -------------------------------------------------
    y1 = target_h(X, s, 1)
    dy1 = y1 - X.base_max
    m1 = ok & dy1.notna()
    fill = X.loc[m1, F].median().to_numpy(np.float64)
    out["fill"] = fill
    # rolling-origin residuals (out-of-sample)
    res_all = []
    for Y in ROLL_YEARS:
        tr = m1 & (yr <= Y - 2)
        va = m1 & (yr == Y - 1)
        te = m1 & (yr == Y)
        nb = fit_xgb_es(X.loc[tr, F], dy1[tr], X.loc[va, F], dy1[va])
        xg = fit_xgb_n(X.loc[tr | va, F], dy1[tr | va], nb)
        fill_y = X.loc[tr | va, F].median().to_numpy(np.float64)
        ridge, rf = fit_members(X, F, dy1, tr | va, fill_y)
        fc = predict_ens(X, F, te, fill_y, ridge, rf, xg)
        obs, base = y1[te], X.base_max[te]
        e, ep = fc - obs, base - obs
        o, f_ = obs > w, fc > w
        tp, fp, fn = int((o & f_).sum()), int((~o & f_).sum()), int((o & ~f_).sum())
        roll_rows.append(dict(target=target, year=Y, n=int(te.sum()), xgb_trees=nb,
                              RMSE=float(np.sqrt((e ** 2).mean())), RMSE_persistence=float(np.sqrt((ep ** 2).mean())),
                              skill_vs_persistence=float(1 - (e ** 2).mean() / (ep ** 2).mean()),
                              hits=tp, false_alarms=fp, misses=fn,
                              CSI=tp / (tp + fp + fn) if tp + fp + fn else np.nan))
        res_all.append(pd.DataFrame({"date": obs.index, "obs": obs.values, "fc": fc.values, "base": base.values}))
    R = pd.concat(res_all)
    res = (R.obs - R.fc).to_numpy()
    high = (R.base >= w - REGIME_BAND_M).to_numpy()
    out["res"] = {"normal": np.sort(np.round(res[~high], 4)), "high": np.sort(np.round(res[high], 4))}
    out["rolling_predictions"] = R
    # final fit on 2014-2025
    nb = fit_xgb_es(X.loc[m1 & (yr <= 2024), F], dy1[m1 & (yr <= 2024)], X.loc[m1 & (yr == 2025), F], dy1[m1 & (yr == 2025)])
    out["xgb"] = fit_xgb_n(X.loc[m1, F], dy1[m1], nb)
    out["xgb_trees"] = nb
    out["ridge"], out["rf"] = fit_members(X, F, dy1, m1, fill)
    out["n_train_rows"] = int(m1.sum())

    # ---------------- +48 h / +72 h direct XGBoost --------------------------------------
    for h in (2, 3):
        yh = target_h(X, s, h)
        dyh = yh - X.base_max
        mh = ok & dyh.notna()
        rr = []
        for Y in ROLL_YEARS:
            tr, va, te = mh & (yr <= Y - 2), mh & (yr == Y - 1), mh & (yr == Y)
            n_ = fit_xgb_es(X.loc[tr, F], dyh[tr], X.loc[va, F], dyh[va])
            m_ = fit_xgb_n(X.loc[tr | va, F], dyh[tr | va], n_)
            fc = X.base_max[te] + m_.predict(X.loc[te, F])
            rr.append(pd.DataFrame({"obs": yh[te].values, "fc": fc.values, "base": X.base_max[te].values}))
        Rh = pd.concat(rr)
        r_ = (Rh.obs - Rh.fc).to_numpy()
        hi = (Rh.base >= w - REGIME_BAND_M).to_numpy()
        n_ = fit_xgb_es(X.loc[mh & (yr <= 2024), F], dyh[mh & (yr <= 2024)], X.loc[mh & (yr == 2025), F], dyh[mh & (yr == 2025)])
        out["h"][h] = {"model": fit_xgb_n(X.loc[mh, F], dyh[mh], n_), "n_trees": n_,
                       "res": {"normal": np.sort(np.round(r_[~hi], 4)), "high": np.sort(np.round(r_[hi], 4))},
                       "rolling_skill": float(1 - ((Rh.fc - Rh.obs) ** 2).mean() / ((Rh.base - Rh.obs) ** 2).mean())}
    print(f"{target}: trained on {out['n_train_rows']} days (2014-2025), xgb trees {nb}, "
          f"+48h trees {out['h'][2]['n_trees']}, +72h trees {out['h'][3]['n_trees']}")
    return out, pd.DataFrame(roll_rows)


def main():
    od = p("07_models", "stage1_operational"); od.mkdir(parents=True, exist_ok=True)
    rows, cmp_rows = [], []
    for t in TARGETS:
        M, roll = run(t)
        joblib.dump({k: v for k, v in M.items() if k != "rolling_predictions"}, od / f"{slug(t)}.joblib")
        rows.append(roll)
        # frozen-2020 research ensemble on the same rows, for comparison
        pr = pd.read_csv(p("08_results", "stage1", f"predictions_{slug(t)}.csv"), parse_dates=["date"])
        pr["ens"] = (pr.Ridge + pr.RandomForest + pr.XGBoost) / 3
        R = M["rolling_predictions"].merge(pr[["date", "ens", "Persistence"]], on="date")
        w = THR[t]["warning"]
        for name, col in (("frozen 2014-2020 ensemble", "ens"), ("rolling-origin ensemble (operational protocol)", "fc")):
            e, ep = R[col] - R.obs, R.Persistence - R.obs
            o, f_ = R.obs > w, R[col] > w
            tp, fp, fn = int((o & f_).sum()), int((~o & f_).sum()), int((o & ~f_).sum())
            cmp_rows.append(dict(target=t, model=name, n=len(R), RMSE=round(float(np.sqrt((e ** 2).mean())), 4),
                                 skill_vs_persistence=round(float(1 - (e ** 2).mean() / (ep ** 2).mean()), 4),
                                 hits=tp, false_alarms=fp, misses=fn, CSI=round(tp / (tp + fp + fn), 4) if tp + fp + fn else None))
    roll = pd.concat(rows).round(4)
    roll.to_csv(p("08_results", "stage1", "operational_rolling_2022_2025.csv"), index=False)
    cmp = pd.DataFrame(cmp_rows)
    cmp.to_csv(p("08_results", "stage1", "operational_vs_frozen.csv"), index=False)
    print(roll.to_string(index=False))
    print(cmp.to_string(index=False))


if __name__ == "__main__":
    main()
