#!/usr/bin/env python3
"""
Re-run the four test-period case studies exactly as scripts/stage2/case_study.py does
(Stage 1 issued the day before, Stage 2 retrained WITHOUT the event's year, threshold from
cv_tuned_summary.csv) and export the map layers as transparent PNGs for the dashboard:
observed Sentinel-1 river-connected flood, forecast probability, hit / false alarm / miss.

Out: 08_results/realtime/case_layers.json
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from scipy import ndimage

sys.argv += ["--label=connected"]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import p  # noqa: E402
from stage2.build_training_table import add_dynamic, levels_on, resample_band  # noqa: E402
from stage2.train_spatial import GROUPS, feats, sample_train, make_model, scores, load_events  # noqa: E402
from stage2.case_study import stage1_forecast, tuned_threshold  # noqa: E402
from realtime.run_realtime import b64png  # noqa: E402
import stage2.improve_stage2 as IMP  # noqa: E402
import joblib  # noqa: E402

EVENTS = ["E20220817", "E20230916", "E20240809", "E20250907"]


def main():
    thr0 = tuned_threshold()
    rule = joblib.load(p("07_models", "stage2_improved", "xgb_final.joblib"))
    cut = rule["cutoff"]
    meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
    tr, (h, w) = Affine(*meta["transform"]), meta["shape"]
    cells = pd.read_parquet(p("06_features", "stage2_cells.parquet"))
    idx = (cells.row.to_numpy(), cells.col.to_numpy())
    dates = pd.read_csv(p("01_raw", "spatial", "events", "floodguard_s1_event_dates.csv")).set_index("event_id")
    daily = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    df_all = load_events()
    full = feats(list(GROUPS), df_all)
    pool = sample_train(df_all)
    del df_all
    out = {}
    for eid in EVENTS:
        D = pd.Timestamp(dates.loc[eid, "s1_date"]); issue = D - pd.Timedelta(days=1)
        trs = pool[pool.year != D.year]
        model0 = make_model("XGBoost", trs.label.mean()).fit(trs[full], trs.label)           # before the improvement
        model = IMP.make_model(full, True).fit(trs[full], trs.label)                          # monotone, same sample
        with rasterio.open(p("01_raw", "spatial", "events", f"flood_{eid}.tif")) as src:
            b = {n: i + 1 for i, n in enumerate(src.descriptions)}
            flood = resample_band(src, b["flood"], tr, (h, w), meta["crs"], Resampling.average)[idx]
            valid = resample_band(src, b["valid"], tr, (h, w), meta["crs"], Resampling.average)[idx]
            rain = {f"rain_{k}": resample_band(src, b[k], tr, (h, w), meta["crs"], Resampling.bilinear)[idx]
                    for k in ("chirps_sum1", "chirps_sum3", "chirps_sum7")}
        fc = stage1_forecast(issue)
        obs_lv = levels_on(D, daily)
        d = add_dynamic(cells.copy(), {"Tikarpara": np.nan, **fc}, rain)
        pr = model.predict_proba(d[full])[:, 1]
        pr0 = model0.predict_proba(d[full])[:, 1]
        thr = max(cut["floor"], cut["c0"] - cut["slope"] * max(0.0, fc["Naraj"] - cut["pivot_naraj_m"]))
        m = (valid >= 0.5) & (cells.river.to_numpy() == 0)
        g = np.zeros((h, w), bool); g[idx] = (flood >= 0.5) & (valid >= 0.5) & (cells.river.to_numpy() == 0)
        rv = np.zeros((h, w), bool); rv[idx] = cells.river.to_numpy() == 1
        lab, _ = ndimage.label(g, structure=np.ones((3, 3)))
        touch = np.unique(lab[ndimage.binary_dilation(rv, iterations=3) & (lab > 0)])
        y = np.isin(lab, touch[touch > 0])[idx].astype(int)
        rv_t = ndimage.binary_dilation(rv, iterations=3)
        raw = (pr >= thr) & (cells.river.to_numpy() == 0)
        gg = np.zeros((h, w), bool); gg[idx] = raw
        lab2, n2 = ndimage.label(gg, structure=np.ones((3, 3)))
        keep = np.zeros(n2 + 1, bool); keep[np.unique(lab2[rv_t & (lab2 > 0)])] = True; keep[0] = False
        pred = raw & keep[lab2[idx]]
        sc = scores(y[m], np.where(pred, 1.0, np.minimum(pr, thr * 0.999))[m], thr)
        sc_raw = scores(y[m], pr[m], thr); sc["ROC_AUC"], sc["PR_AUC"] = sc_raw["ROC_AUC"], sc_raw["PR_AUC"]
        sc0 = scores(y[m], pr0[m], thr0)

        def layer(mask, color):
            a = np.zeros((h, w, 4), "uint8"); a[idx[0][mask], idx[1][mask]] = color; return b64png(a)
        prob_rgba = np.zeros((h, w, 4), "uint8")
        for lo, c in [(0.30, (254, 217, 118, 150)), (0.60, (253, 141, 60, 205)), (thr, (189, 0, 38, 235))]:
            k = m & ((pr >= lo) if lo != thr else pred); prob_rgba[idx[0][k], idx[1][k]] = c
        agree = np.zeros((h, w, 4), "uint8")
        for k, c in [(m & pred & (y == 1), (26, 152, 80, 230)), (m & pred & (y == 0), (245, 158, 11, 230)),
                     (m & ~pred & (y == 1), (118, 42, 131, 230))]:
            agree[idx[0][k], idx[1][k]] = c
        out[eid] = dict(issue=str(issue.date()), valid=str(D.date()),
                        naraj_fc=round(fc["Naraj"], 2), naraj_obs=round(obs_lv["Naraj"], 2),
                        levels_fc={k: round(v, 2) for k, v in fc.items()}, cut=round(thr, 3),
                        original={k: (None if pd.isna(v) else round(float(v), 3)) for k, v in sc0.items()},
                        scores={k: (None if pd.isna(v) else round(float(v), 3)) for k, v in sc.items()},
                        observed_png=layer(m & (y == 1), (37, 99, 235, 220)), forecast_png=b64png(prob_rgba),
                        agreement_png=b64png(agree), nodata_png=layer(~m & (cells.river.to_numpy() == 0), (120, 120, 120, 90)))
        print(eid, "now", out[eid]["scores"]["F1_Dice"], out[eid]["scores"]["area_ratio"], "| before", out[eid]["original"]["F1_Dice"], out[eid]["original"]["area_ratio"])
    p("08_results", "realtime", "case_layers.json").write_text(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main()
