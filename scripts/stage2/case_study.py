#!/usr/bin/env python3
"""
FloodGuard - end-to-end case studies on TEST-PERIOD floods (step 7).

For an event with a Sentinel-1 observation on day D:
  1. Stage 1 (trained 2014-2020 only) is issued on day D-1 and forecasts the daily max
     at Naraj / Kishan Nagar / Tarpur for day D.
  2. Stage 2 is trained on all accepted events EXCEPT those of the event's year
     (same protocol as leave-one-year-out CV) and maps flood probability for day D from
     the forecast levels + CHIRPS rainfall up to D-1 + static layers.
  3. The map is scored against the Sentinel-1 flood mask actually observed on day D.
     The same Stage-2 model is also run with OBSERVED levels, which separates the error
     contributed by Stage 1 from the error of the spatial model itself.
  4. SHAP explains the high-risk cells; a warning bulletin is written.

Usage: python scripts/stage2/case_study.py E20220817 E20230803 E20240809
Outputs: 08_results/stage2/case_studies/<event>/{case_study.png, metrics.json, warning.md}
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import shap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402
from stage2.build_training_table import add_dynamic, levels_on, resample_band  # noqa: E402
from stage2.train_spatial import GROUPS, feats, sample_train, make_model, scores, load_events, LABEL  # noqa: E402
from scipy import ndimage  # noqa: E402

CFG = load_config()
STATIONS = ["Naraj", "Kishan Nagar", "Tarpur"]


def slug(s):
    return s.lower().replace(" ", "_")


def stage1_forecast(issue_day):
    out = {}
    for st in STATIONS:
        X = pd.read_csv(p("06_features", f"stage1_{slug(st)}_forecast_inputs.csv"), parse_dates=["date"]).set_index("date")
        m = joblib.load(p("07_models", "stage1", f"{slug(st)}_tabular.joblib"))
        if issue_day not in X.index or X.loc[[issue_day], m["features"]].isna().all(axis=1).iloc[0]:
            out[st] = np.nan; continue
        out[st] = float(X.loc[issue_day, "base_max"] + m["xgb"].predict(X.loc[[issue_day], m["features"]])[0])
    return out


def tuned_threshold():
    f = p("08_results", "stage2" + ("" if LABEL == "all" else "_connected"), "cv_tuned_summary.csv")
    if f.exists():
        return float(pd.read_csv(f, index_col=0).loc["mean_threshold", "value"])
    return 0.5


def main(event_ids):
    THR = tuned_threshold()
    print(f"label={LABEL}, decision threshold={THR:.2f}")
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
    summary = []

    for eid in event_ids:
        D = pd.Timestamp(dates.loc[eid, "s1_date"]); issue = D - pd.Timedelta(days=1)
        out = p("08_results", "stage2" + ("" if LABEL == "all" else "_connected"), "case_studies", eid); out.mkdir(parents=True, exist_ok=True)
        trs = pool[pool.year != D.year]
        model = make_model("XGBoost", trs.label.mean()).fit(trs[full], trs.label)

        with rasterio.open(p("01_raw", "spatial", "events", f"flood_{eid}.tif")) as src:
            b = {n: i + 1 for i, n in enumerate(src.descriptions)}
            flood = resample_band(src, b["flood"], tr, (h, w), meta["crs"], Resampling.average)[idx]
            valid = resample_band(src, b["valid"], tr, (h, w), meta["crs"], Resampling.average)[idx]
            rain = {f"rain_{k}": resample_band(src, b[k], tr, (h, w), meta["crs"], Resampling.bilinear)[idx]
                    for k in ("chirps_sum1", "chirps_sum3", "chirps_sum7")}

        fc = stage1_forecast(issue)
        obs_lv = levels_on(D, daily)
        res = {}
        probs = {}
        for name, lv in [("forecast levels (operational)", {**{"Tikarpara": np.nan}, **fc}),
                         ("observed levels (Stage-2 only)", obs_lv)]:
            d = add_dynamic(cells.copy(), lv, rain)
            pr = model.predict_proba(d[full])[:, 1]
            m = (valid >= 0.5) & (d.river.to_numpy() == 0)
            y = (flood >= 0.5).astype(int)
            if LABEL == "connected":
                g = np.zeros((h, w), bool); g[idx] = (flood >= 0.5) & (valid >= 0.5) & (cells.river.to_numpy() == 0)
                rv = np.zeros((h, w), bool); rv[idx] = cells.river.to_numpy() == 1
                lab, _ = ndimage.label(g, structure=np.ones((3, 3)))
                touch = np.unique(lab[ndimage.binary_dilation(rv, iterations=3) & (lab > 0)])
                y = np.isin(lab, touch[touch > 0])[idx].astype(int)
            r = scores(y[m], pr[m], THR); r["naraj_m"] = lv["Naraj"]
            res[name] = {k: (None if pd.isna(v) else round(float(v), 3)) for k, v in r.items()}
            probs[name] = (pr, m, y, d)

        pr, m, y, d = probs["forecast levels (operational)"]
        hi = d[(pr >= THR) & m]
        drivers = []
        if len(hi):
            sv = shap.TreeExplainer(model).shap_values(hi.sample(min(3000, len(hi)), random_state=0)[full])
            drivers = pd.Series(sv.mean(0), index=full).sort_values(ascending=False).head(5).round(2).to_dict()

        def grid(v):
            g = np.full((h, w), np.nan); g[idx] = v; return g
        river = grid(cells.river.to_numpy()) == 1
        fig, ax = plt.subplots(1, 3, figsize=(17, 4.6))
        obs_img = np.where(m, y, np.nan).astype(float)
        ax[0].imshow(grid(obs_img), cmap=ListedColormap(["#f7f7f7", "#d7301f"]), interpolation="nearest")
        ax[0].set_title(f"Observed Sentinel-1 {'river-connected ' if LABEL == 'connected' else ''}flood, {D.date()}\nNaraj observed {obs_lv['Naraj']:.2f} m", fontsize=9)
        im = ax[1].imshow(grid(np.where(d.river == 1, np.nan, pr)), cmap="YlOrRd", vmin=0, vmax=1)
        ax[1].set_title(f"FloodGuard forecast issued {issue.date()} (P flood)\nNaraj forecast {fc['Naraj']:.2f} m",
                        fontsize=9)
        plt.colorbar(im, ax=ax[1], shrink=.7)
        pred = (pr >= THR)
        cmp_ = np.full(len(pr), np.nan)
        cmp_[m & pred & (y == 1)] = 0; cmp_[m & pred & (y == 0)] = 1; cmp_[m & ~pred & (y == 1)] = 2
        ax[2].imshow(grid(cmp_), cmap=ListedColormap(["#1a9850", "#fdae61", "#762a83"]), vmin=-.5, vmax=2.5,
                     interpolation="nearest")
        for c, n in zip(["#1a9850", "#fdae61", "#762a83"], ["hit", "false alarm", "miss"]):
            ax[2].plot([], [], "s", color=c, label=n)
        ax[2].legend(fontsize=8, loc="lower right")
        f = res["forecast levels (operational)"]
        ax[2].set_title(f"Agreement: F1 {f['F1_Dice']:.2f}, IoU {f['IoU_CSI']:.2f}, area ratio {f['area_ratio']:.2f}",
                        fontsize=9)
        for a in ax:
            a.imshow(np.where(river, 1, np.nan), cmap=ListedColormap(["#2171b5"]), interpolation="nearest")
            a.set_xticks([]); a.set_yticks([])
        fig.suptitle(f"Case study {eid}: +24 h forecast vs Sentinel-1 observation (Stage-2 model never saw {D.year})")
        fig.tight_layout(); fig.savefig(out / "case_study.png", dpi=120); plt.close(fig)

        json.dump({"event": eid, "issue_day": str(issue.date()), "valid_day": str(D.date()),
                   "stage1_forecast_m": {k: round(v, 2) for k, v in fc.items()},
                   "observed_m": {k: (None if np.isnan(v) else round(v, 2)) for k, v in obs_lv.items()},
                   "scores": res, "top_shap_drivers_high_risk": drivers}, open(out / "metrics.json", "w"), indent=1)
        area = {lab: round(float(((pr >= lo) & (pr < hi_) & m).sum()) / 100, 1)
                for lab, lo, hi_ in [("Watch (0.5 x thr - thr)", THR / 2, THR), ("Warning (thr - 0.9)", THR, .9),
                                     ("Severe (>= 0.9)", .9, 1.01)]}
        thr = CFG["thresholds"]
        md = [f"# FloodGuard warning (hindcast) - valid {D.date()}, issued {issue.date()}", "",
              "| Station | Forecast max (m) | Warning level (m) | Status |", "|---|---|---|---|"]
        for st in STATIONS:
            wl = thr[st]["warning"]
            md.append(f"| {st} | {fc[st]:.2f} | {wl} | {'ABOVE WARNING' if fc[st] >= wl else 'below'} |")
        md += ["", "| Risk class | Area (km²) |", "|---|---|"] + [f"| {k} | {v} |" for k, v in area.items()]
        md += ["", "Main factors raising risk in high-risk cells (mean SHAP, log-odds): " +
               ", ".join(f"{k} {v:+.2f}" for k, v in drivers.items()), "",
               f"Verification against Sentinel-1 ({D.date()}): F1 {f['F1_Dice']}, IoU {f['IoU_CSI']}, "
               f"ROC-AUC {f['ROC_AUC']}", "", "_Research prototype - not an official CWC/OSDMA warning._"]
        (out / "warning.md").write_text("\n".join(md), encoding="utf-8")
        summary.append({"event": eid, "issue": str(issue.date()), "naraj_fc": round(fc["Naraj"], 2),
                        "naraj_obs": round(obs_lv["Naraj"], 2), **{f"{k[:8]}_{m_}": v[m_] for k, v in res.items()
                                                                   for m_ in ("F1_Dice", "IoU_CSI", "area_ratio", "ROC_AUC")}})
        print(json.dumps(summary[-1]))
    pd.DataFrame(summary).to_csv(p("08_results", "stage2" + ("" if LABEL == "all" else "_connected"),
                                  "case_studies", "summary.csv"), index=False)


if __name__ == "__main__":
    main([a for a in sys.argv[1:] if not a.startswith("--")])
