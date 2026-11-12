#!/usr/bin/env python3
"""
FloodGuard Stage 2 - spatial flood-probability model, evaluation, ablation, SHAP.

Evaluation: leave-one-YEAR-out (all events of the held-out year are unseen,
            so no information from the same flood season leaks into training).
            Each held-out event is scored on ALL its valid cells.
Models    : 'bathtub' physical baseline (flood if depth_proxy > c, c tuned on train),
            Logistic Regression, Random Forest, XGBoost.
Metrics   : Precision, Recall, F1 (= Dice), IoU (= CSI), predicted/observed flooded
            area ratio, ROC-AUC, PR-AUC. Reported per event and averaged over
            flood events (kind = flood_above_warning) and over all events.
Ablation  : XGBoost with feature groups terrain -> +river proximity -> +LULC
            -> +hydrological state -> +rainfall.
Outputs   : 08_results/stage2/*.csv, shap_*.png, 07_models/stage2/xgb_final.joblib
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score, average_precision_score
import xgboost as xgb
import shap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

warnings.filterwarnings("ignore")
CFG = load_config()
RNG = np.random.default_rng(0)
# label definition: "all" = every S1 flood pixel; "connected" = river-connected floods only
LABEL = next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--label=")), "all")

GROUPS = {
    "terrain": ["elev_m", "slope_deg", "har_m"],
    "river": ["dist_river_m", "jrc_occurrence"],
    "lulc": ["lc_tree_frac", "lc_grass_frac", "lc_crop_frac", "lc_built_frac", "lc_water_frac", "lc_wetland_frac",
             "lc_bare_frac", "lc_shrub_frac"],
    "hydro": ["lvl_naraj", "lvl_near", "depth_proxy_m"],
    "rain": ["rain_chirps_sum1", "rain_chirps_sum3", "rain_chirps_sum7"],
}
ABLATION = [("terrain", ["terrain"]), ("+river", ["terrain", "river"]), ("+lulc", ["terrain", "river", "lulc"]),
            ("+hydro", ["terrain", "river", "lulc", "hydro"]),
            ("+rain (full)", ["terrain", "river", "lulc", "hydro", "rain"])]


def feats(groups, df):
    return [f for g in groups for f in GROUPS[g] if f in df.columns]


def sample_train(df, neg_ratio=3, cap=15000):
    out = []
    for _, g in df.groupby("event_id"):
        pos, neg = g[g.label == 1], g[g.label == 0]
        nneg = min(len(neg), max(neg_ratio * len(pos), 3000))
        s = pd.concat([pos, neg.sample(nneg, random_state=0)])
        out.append(s.sample(min(len(s), cap), random_state=0))
    return pd.concat(out)


def scores(y, prob, thr=0.5):
    pred = prob >= thr
    tp, fp, fn = np.sum(pred & (y == 1)), np.sum(pred & (y == 0)), np.sum(~pred & (y == 1))
    prec = tp / (tp + fp) if tp + fp else np.nan
    rec = tp / (tp + fn) if tp + fn else np.nan
    return dict(precision=prec, recall=rec, F1_Dice=2 * tp / (2 * tp + fp + fn) if tp + fp + fn else np.nan,
                IoU_CSI=tp / (tp + fp + fn) if tp + fp + fn else np.nan,
                area_ratio=pred.sum() / max(y.sum(), 1), obs_flooded_cells=int(y.sum()),
                ROC_AUC=roc_auc_score(y, prob) if 0 < y.sum() < len(y) else np.nan,
                PR_AUC=average_precision_score(y, prob) if 0 < y.sum() < len(y) else np.nan)


def make_model(name, n_pos_ratio):
    if name == "LogReg":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    if name == "RandomForest":
        return make_pipeline(SimpleImputer(strategy="median"),
                             RandomForestClassifier(n_estimators=100, min_samples_leaf=20, max_samples=0.3, n_jobs=-1, random_state=0))
    return xgb.XGBClassifier(n_estimators=400, learning_rate=0.05, max_depth=6, subsample=0.8, colsample_bytree=0.8,
                             min_child_weight=10, reg_lambda=2.0, random_state=0, n_jobs=-1,
                             eval_metric="logloss")


class Bathtub:
    """Physical baseline: flooded if nearest-gauge level exceeds ground by more than c metres."""
    def fit(self, X, y):
        cand = np.linspace(-6, 6, 121)
        f1 = [scores(y, (X["depth_proxy_m"].to_numpy() > c).astype(float))["F1_Dice"] for c in cand]
        self.c = cand[int(np.nanargmax(f1))]
        return self

    def predict_proba(self, X):
        z = X["depth_proxy_m"].to_numpy() - self.c
        p1 = 1 / (1 + np.exp(-3 * z))
        return np.c_[1 - p1, p1]


_POOL = {}


def cv(df, model_name, cols):
    """Leave-one-year-out. The (sampled) training pool is drawn once per event and
    reused across folds, so the full ~10^7-row table is never copied."""
    if "pool" not in _POOL:
        _POOL["pool"] = sample_train(df)
    pool = _POOL["pool"]
    rows = []
    for yr in sorted(df.year.unique()):
        trs = pool[pool.year != yr]
        te = df[df.year == yr]
        if trs.label.sum() == 0 or te.empty:
            continue
        m = Bathtub() if model_name == "Bathtub" else make_model(model_name, trs.label.mean())
        m.fit(trs[cols], trs.label.to_numpy())
        for ev, g in te.groupby("event_id"):
            prob = m.predict_proba(g[cols])[:, 1]
            r = scores(g.label.to_numpy(), prob); r.update(event_id=ev, kind=g.kind.iloc[0], year=yr,
                                                          naraj_m=g.lvl_naraj.iloc[0])
            rows.append(r)
    return pd.DataFrame(rows)


def summarize(res):
    num = ["precision", "recall", "F1_Dice", "IoU_CSI", "area_ratio", "ROC_AUC", "PR_AUC"]
    fl = res[res.kind == "flood_above_warning"]
    return pd.Series({**{f"{k}_flood_events": fl[k].mean() for k in num},
                      **{f"{k}_all_events": res[k].mean() for k in ["ROC_AUC", "PR_AUC", "F1_Dice"]},
                      "n_flood_events": len(fl), "n_events": len(res)})


def load_events():
    """Load only the needed columns as float32 (the full table is ~10^7 rows)."""
    import pyarrow.parquet as pq
    # one parquet file per event in 06_features/stage2_events/, read in the original
    # processing order (stage2_events_summary.csv) so every sample is reproducible
    order = pd.read_csv(p("06_features", "stage2_events_summary.csv")).event_id
    files = [p("06_features", "stage2_events", f"{e}.parquet") for e in order]
    files = [f for f in files if f.exists()]
    have = set(pq.read_schema(files[0]).names)
    want = [f for g in GROUPS.values() for f in g] + ["label", "label_conn", "event_id", "kind", "year", "lvl_naraj"]
    cols = [c for c in dict.fromkeys(want) if c in have]
    df = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
    df["label"] = df["label"].astype("int8")
    if LABEL == "connected":
        df["label"] = df["label_conn"].astype("int8")
    return df


def main():
    suffix = "" if LABEL == "all" else "_connected"
    out = p("08_results", "stage2" + suffix); out.mkdir(parents=True, exist_ok=True)
    mdir = p("07_models", "stage2" + suffix); mdir.mkdir(parents=True, exist_ok=True)
    print(f"label definition: {LABEL}")
    df = load_events()
    full = feats([g for g in GROUPS], df)
    print(f"{df.event_id.nunique()} events, {df.year.nunique()} years, {len(df)} rows, features {len(full)}")

    allres, summ = [], {}
    for name, cols in [("Bathtub", ["depth_proxy_m"]), ("LogReg", full), ("RandomForest", full), ("XGBoost", full)]:
        r = cv(df, name, cols); r["model"] = name; allres.append(r); summ[name] = summarize(r)
        print(f"{name:12s}", summ[name][["F1_Dice_flood_events", "IoU_CSI_flood_events", "area_ratio_flood_events",
                                        "ROC_AUC_all_events"]].round(3).to_dict())
    pd.concat(allres).round(4).to_csv(out / "cv_per_event.csv", index=False)
    pd.DataFrame(summ).T.round(4).to_csv(out / "cv_summary.csv")

    abl = {}
    for label, groups in ABLATION:
        cols = feats(groups, df)
        abl[label] = summarize(cv(df, "XGBoost", cols))
        print(f"ablation {label:14s}", abl[label][["F1_Dice_flood_events", "IoU_CSI_flood_events",
                                                    "ROC_AUC_all_events"]].round(3).to_dict())
    pd.DataFrame(abl).T.round(4).to_csv(out / "ablation.csv")

    # final model on all events + SHAP
    trs = _POOL.get("pool", None)
    trs = sample_train(df) if trs is None else trs
    final = make_model("XGBoost", trs.label.mean()).fit(trs[full], trs.label)
    joblib.dump({"model": final, "features": full}, mdir / "xgb_final.joblib")
    sub = trs.sample(min(5000, len(trs)), random_state=0)
    ex = shap.TreeExplainer(final)
    sv = ex.shap_values(sub[full])
    imp = pd.Series(np.abs(sv).mean(0), index=full).sort_values(ascending=False)
    imp.round(4).to_csv(out / "shap_global_importance.csv", header=["mean_abs_shap"])
    plt.figure(); shap.summary_plot(sv, sub[full], show=False, max_display=15)
    plt.tight_layout(); plt.savefig(out / "shap_summary.png", dpi=130); plt.close()
    print("\nSHAP global importance:\n", imp.round(3).head(10).to_string())


if __name__ == "__main__":
    main()
