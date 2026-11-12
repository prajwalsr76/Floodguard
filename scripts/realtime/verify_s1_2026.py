#!/usr/bin/env python3
"""
Independent check on the 2026 monsoon (a year no model has seen): score the operational
FloodGuard chain against Sentinel-1 flood maps of three 2026 overpasses with Naraj above
its warning level.

Sentinel-1 masks were computed in Earth Engine with the exact recipe of the training labels
(scripts/gee/floodguard_all_exports.template.js: VV, same-orbit Jan-Apr median as reference,
50 m focal median, diff < -3 dB and post < -16 dB, JRC permanent water and slope > 5 deg
masked, mean of 10 m pixels on the 100 m grid) and saved as
01_raw/spatial/events_2026/s1_<date>.npy  (435 x 767 x [flood %, valid %]).

Chain scored, exactly as it would have run on the day before each overpass:
  Stage 1 ensemble forecast issued D-1 (live CWC levels)  ->  Stage 2 final model
  (trained on 2015-2025 scenes) with Open-Meteo rain up to D-1  ->  P >= calibrated cut-off.
Labels: river-connected flood patches, scored on valid non-channel cells, as in CV.

Out: 08_results/realtime/s1_2026.json (scores + map layers) and s1_2026_scores.csv
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import p  # noqa: E402
import realtime.run_realtime as R  # noqa: E402
from realtime.run_realtime import b64png  # noqa: E402
from stage2.train_spatial import scores  # noqa: E402

SCENES = {"2026-08-02": 48, "2026-08-28": 85, "2026-10-01": 48}


def main():
    R.P_LIKELY = float(pd.read_csv(p("08_results", "stage2_connected", "cv_tuned_summary.csv"), index_col=0)
                       .loc["mean_threshold", "value"])
    d, _, _ = R.load_levels()
    rain, _, _ = R.load_rain()
    _, series, _ = R.run_stage1(d, rain)
    s2 = R.Stage2()
    s2o = R.Stage2("original")                 # the map rule before the Stage 2 improvement
    cells, idx = s2.cells, s2.idx
    h, w = s2.h, s2.w
    river = cells.river.to_numpy() == 1
    rv = np.zeros((h, w), bool); rv[idx] = river
    out, rows = {}, []
    for D, orbit in SCENES.items():
        a = np.load(p("01_raw", "spatial", "events_2026", f"s1_{D}.npy"))
        flood, valid = a[..., 0][idx] / 100.0, a[..., 1][idx] / 100.0
        t = pd.Timestamp(D) - pd.Timedelta(days=1)
        lv = {st: float(series[st].set_index("issue").fc[t]) for st in R.TARGETS}
        obs = {st: float(series[st].set_index("issue").obs[t]) for st in R.TARGETS}
        rs = R.rain_sums(rain, t)
        prob, _ = s2.predict(lv, rs)
        pr = np.nan_to_num(prob)
        thr = s2.likely_cut(lv["Naraj"])
        pred = s2.raw_classes(prob, lv["Naraj"]) == 3
        prob_o, _ = s2o.predict(lv, rs)
        pred_o = np.nan_to_num(prob_o) >= R.P_LIKELY
        m = (valid >= 0.5) & ~river
        g = np.zeros((h, w), bool); g[idx] = (flood >= 0.5) & (valid >= 0.5) & ~river
        lab, _ = ndimage.label(g, structure=np.ones((3, 3)))
        touch = np.unique(lab[ndimage.binary_dilation(rv, iterations=3) & (lab > 0)])
        y = np.isin(lab, touch[touch > 0])[idx].astype(int)
        sc = scores(y[m], np.where(pred, 1.0, np.minimum(pr, thr * 0.999))[m], thr)   # binary scores of the final rule
        sc_raw = scores(y[m], pr[m], thr)
        sc["ROC_AUC"], sc["PR_AUC"] = sc_raw["ROC_AUC"], sc_raw["PR_AUC"]                  # ranking scores of the probability
        sc_o = scores(y[m], np.nan_to_num(prob_o)[m], R.P_LIKELY)

        def layer(mask, color):
            img = np.zeros((h, w, 4), "uint8"); img[idx[0][mask], idx[1][mask]] = color; return b64png(img)
        prob_rgba = np.zeros((h, w, 4), "uint8")
        for lo, c in [(0.30, (254, 217, 118, 150)), (0.60, (253, 141, 60, 205)), (thr, (189, 0, 38, 235))]:
            k = m & ((pr >= lo) if lo != thr else pred); prob_rgba[idx[0][k], idx[1][k]] = c
        agree = np.zeros((h, w, 4), "uint8")
        for k, c in [(m & pred & (y == 1), (26, 152, 80, 230)), (m & pred & (y == 0), (245, 158, 11, 230)),
                     (m & ~pred & (y == 1), (118, 42, 131, 230))]:
            agree[idx[0][k], idx[1][k]] = c
        key = "S" + D.replace("-", "")
        out[key] = dict(issue=str(t.date()), valid=D, unseen_year=True, orbit=orbit,
                        naraj_fc=round(lv["Naraj"], 2), naraj_obs=round(obs["Naraj"], 2),
                        levels_fc={k: round(v, 2) for k, v in lv.items()}, rain_mm={str(k): round(v, 1) for k, v in rs.items()},
                        coverage=round(float(m.mean()), 3), cut=round(thr, 3),
                        original={k: (None if pd.isna(v) else round(float(v), 3)) for k, v in sc_o.items()},
                        scores={k: (None if pd.isna(v) else round(float(v), 3)) for k, v in sc.items()},
                        observed_png=layer(m & (y == 1), (37, 99, 235, 220)), forecast_png=b64png(prob_rgba),
                        agreement_png=b64png(agree), nodata_png=layer(~m & ~river, (120, 120, 120, 90)))
        rows.append(dict(scene=D, orbit=orbit, naraj_fc=lv["Naraj"], naraj_obs=obs["Naraj"], coverage=m.mean(), cut=thr, **sc,
                         **{f"original_{k}": v for k, v in sc_o.items() if k in ("precision", "recall", "F1_Dice", "IoU_CSI", "area_ratio")}))
        print(D, {k: round(float(v), 3) for k, v in sc.items() if k in ("F1_Dice", "IoU_CSI", "precision", "recall", "area_ratio", "ROC_AUC")},
              "| original", {k: round(float(v), 3) for k, v in sc_o.items() if k in ("F1_Dice", "area_ratio")}, "obs cells", int(y[m].sum()))
    p("08_results", "realtime", "s1_2026.json").write_text(json.dumps(out, separators=(",", ":")))
    pd.DataFrame(rows).round(4).to_csv(p("08_results", "realtime", "s1_2026_scores.csv"), index=False)


if __name__ == "__main__":
    main()
