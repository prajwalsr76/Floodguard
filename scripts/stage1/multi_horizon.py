#!/usr/bin/env python3
"""
FloodGuard Stage 1 extension - lead-time analysis (+24 h, +48 h, +72 h).

Direct strategy: one XGBoost model per horizon h, trained on the same issue-day
features as the +24 h model (build_features.py), target = daily max on day t+h,
learned as the change from today's max. Same chronological split and the same
complete-case test rows for every horizon. Reports how skill decays with lead time
(required by the evaluation plan: "report performance degradation with lead time").

Output: 08_results/stage1/lead_time_<target>.csv and lead_time_skill.png
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import xgboost as xgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

warnings.filterwarnings("ignore")
CFG = load_config()
S1 = CFG["stage1"]
HORIZONS = [1, 2, 3]


def slug(s):
    return s.lower().replace(" ", "_")


def run(target):
    X = pd.read_csv(p("06_features", f"stage1_{slug(target)}.csv"), parse_dates=["date"]).set_index("date")
    feats = [c for c in X.columns if "__" in c]
    d = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    s = d[d.station == target].set_index("date").daily_max_m.asfreq("D")
    thr = CFG["thresholds"][target]["warning"]
    sp = S1["split"]
    rows = []
    ok = X[feats].notna().all(axis=1)
    for h in HORIZONS:
        y = s.shift(-h).reindex(X.index)
        m = ok & y.notna()
        base = X["base_max"]
        tr = m & (X.index >= sp["train"][0]) & (X.index <= sp["train"][1])
        va = m & (X.index >= sp["val"][0]) & (X.index <= sp["val"][1])
        te = m & (X.index >= sp["test"][0]) & (X.index <= sp["test"][1])
        mdl = xgb.XGBRegressor(n_estimators=2000, learning_rate=0.03, max_depth=4, subsample=0.8,
                               colsample_bytree=0.7, min_child_weight=3, early_stopping_rounds=100, random_state=0)
        mdl.fit(X.loc[tr, feats], (y - base)[tr], eval_set=[(X.loc[va, feats], (y - base)[va])], verbose=False)
        yh = base[te] + mdl.predict(X.loc[te, feats])
        e, ep = yh - y[te], base[te] - y[te]
        obs, pred = y[te] > thr, yh > thr
        tp, fp, fn = (obs & pred).sum(), (~obs & pred).sum(), (obs & ~pred).sum()
        rows.append(dict(target=target, horizon_days=h, n_test=int(te.sum()), RMSE=np.sqrt((e ** 2).mean()),
                         RMSE_persistence=np.sqrt((ep ** 2).mean()),
                         skill_vs_persistence=1 - (e ** 2).mean() / (ep ** 2).mean(),
                         NSE=1 - (e ** 2).sum() / ((y[te] - y[te].mean()) ** 2).sum(),
                         CSI=tp / (tp + fp + fn) if tp + fp + fn else np.nan, n_exceed=int(obs.sum())))
    return pd.DataFrame(rows)


def main():
    res = pd.concat([run(t) for t in S1["targets"]], ignore_index=True).round(4)
    out = p("08_results", "stage1"); out.mkdir(parents=True, exist_ok=True)
    res.to_csv(out / "lead_time_all_targets.csv", index=False)
    print(res.to_string(index=False))
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.4))
    for t, g in res.groupby("target"):
        ax[0].plot(g.horizon_days * 24, g.RMSE, "o-", label=t)
        ax[1].plot(g.horizon_days * 24, g.skill_vs_persistence, "o-", label=t)
    ax[0].set(xlabel="Lead time (h)", ylabel="RMSE (m)", title="Error grows with lead time")
    ax[1].set(xlabel="Lead time (h)", ylabel="Skill vs persistence", title="Skill vs persistence")
    for a in ax:
        a.set_xticks([24, 48, 72]); a.grid(alpha=.3)
    ax[1].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "lead_time_skill.png", dpi=130)


if __name__ == "__main__":
    main()
