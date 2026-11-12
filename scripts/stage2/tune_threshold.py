#!/usr/bin/env python3
"""
FloodGuard Stage 2 - decision-threshold calibration under leave-one-year-out CV.

The training sample is rebalanced (all flooded cells + 3x dry cells per event), which
inflates predicted probabilities. Here the warning threshold is chosen on the TRAINING
years only (maximising mean per-event F1 on an unbiased random sample of their cells)
and then applied unchanged to the held-out year. Also reports how well the predicted
flooded AREA per event tracks the observed area (event-level skill).

Usage: python scripts/stage2/tune_threshold.py [--label=connected]
Out  : 08_results/stage2[_connected]/cv_tuned_per_event.csv, cv_tuned_summary.csv
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage2.train_spatial as T  # noqa: E402
from common.config import p  # noqa: E402

GRID = np.round(np.arange(0.05, 0.96, 0.05), 2)


def main():
    df = T.load_events()
    cols = T.feats(list(T.GROUPS), df)
    pool = T.sample_train(df)
    # unbiased per-event sample for threshold selection (random cells, true prevalence)
    shuf = df.sample(frac=1.0, random_state=1)
    calib = shuf[shuf.groupby("event_id").cumcount() < 20000]
    del shuf
    rows = []
    for yr in sorted(df.year.unique()):
        trs, te = pool[pool.year != yr], df[df.year == yr]
        m = T.make_model("XGBoost", trs.label.mean()).fit(trs[cols], trs.label)
        cal = calib[calib.year != yr]
        pc = m.predict_proba(cal[cols])[:, 1]
        f1 = []
        for t in GRID:
            f1.append(np.nanmean([T.scores(g.label.to_numpy(), pc[cal.index.get_indexer(g.index)], t)["F1_Dice"]
                                  for _, g in cal.groupby("event_id") if g.label.sum() > 0]))
        thr = float(GRID[int(np.nanargmax(f1))])
        for ev, g in te.groupby("event_id"):
            prob = m.predict_proba(g[cols])[:, 1]
            r = T.scores(g.label.to_numpy(), prob, thr)
            r.update(event_id=ev, kind=g.kind.iloc[0], year=yr, naraj_m=g.lvl_naraj.iloc[0], threshold=thr,
                     pred_cells=int((prob >= thr).sum()))
            rows.append(r)
        print(f"{yr}: threshold {thr:.2f}")
    res = pd.DataFrame(rows)
    out = p("08_results", "stage2" + ("" if T.LABEL == "all" else "_connected"))
    res.round(4).to_csv(out / "cv_tuned_per_event.csv", index=False)
    fl = res[res.kind == "flood_above_warning"]
    s = pd.Series({"label": T.LABEL, "F1_flood_events": fl.F1_Dice.mean(), "IoU_flood_events": fl.IoU_CSI.mean(),
                   "area_ratio_flood_events_median": fl.area_ratio.median(),
                   "precision_flood_events": fl.precision.mean(), "recall_flood_events": fl.recall.mean(),
                   "F1_all_events": res.F1_Dice.mean(),
                   "spearman_pred_vs_obs_area": spearmanr(res.pred_cells, res.obs_flooded_cells).correlation,
                   "spearman_pred_area_vs_naraj": spearmanr(res.pred_cells, res.naraj_m, nan_policy="omit").correlation,
                   "mean_threshold": res.threshold.mean()})
    s.to_frame("value").to_csv(out / "cv_tuned_summary.csv")
    print(s.to_string())


if __name__ == "__main__":
    main()
