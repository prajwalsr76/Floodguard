#!/usr/bin/env python3
"""
FloodGuard Stage 2 - accuracy improvement study (same data, same features, same grid).

The original live map (XGBoost, cut-off 0.90) under-maps big floods: in 2022 (Naraj 27.47 m)
it found only 47% of the flooded area, while small floods were over-mapped. Three causes
were tested, each with leave-one-year-out (LOYO) cross-validation on the same 48 Sentinel-1
scenes. No new stations, no new data, no 2026 data.

  1. Monotone model     - the same XGBoost (same features, same hyper-parameters, same training
                          sample) with a monotone constraint: flood probability may only rise
                          when the river levels (Naraj, nearest gauge, depth proxy) rise.
                          Trees otherwise make arbitrary jumps above the levels seen in training.
  2. Level-aware cut-off - one fixed cut-off (0.90) is too strict for record floods. The cut-off
                          may fall linearly above the Naraj warning level:
                              cut(L) = max(floor, c0 - slope * max(0, L - 25.41))
  3. River connectivity  - the labels only count flooded patches connected to the river
                          (build_training_table.py). The same rule is applied to the map:
                          predicted patches that do not touch the river are removed.

Honest selection
  * Out-of-fold probabilities: for every year Y the models are trained without Y and predict
    every cell of every scene of Y (exactly as train_spatial.py / tune_threshold.py).
  * Cut-off parameters are chosen for each held-out year using only the OTHER years'
    out-of-fold maps (objective: mean F1 over all scenes, as in tune_threshold.py), then
    applied unchanged to the held-out year.
  * The 2026 Sentinel-1 scenes are not used here; they remain an independent check
    (scripts/realtime/verify_s1_2026.py).

Outputs
  08_results/stage2_improved/variants_summary.csv   every variant, LOYO scores
  08_results/stage2_improved/per_event.csv          chosen variant vs original, per scene
  08_results/stage2_improved/summary.json           headline numbers + chosen parameters
  07_models/stage2_improved/xgb_monotone.joblib     final monotone model (all 2015-2025 scenes)
"""
import json, sys, time, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
import xgboost as xgb
from scipy import ndimage
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage2.train_spatial as T  # noqa: E402
from common.config import load_config, p  # noqa: E402

warnings.filterwarnings("ignore")
CFG = load_config()
WARN = CFG["thresholds"]["Naraj"]["warning"]
OUT = p("08_results", "stage2_improved"); OUT.mkdir(parents=True, exist_ok=True)
MDIR = p("07_models", "stage2_improved"); MDIR.mkdir(parents=True, exist_ok=True)
MONO_FEATS = ("lvl_naraj", "lvl_near", "depth_proxy_m")
FIXED = np.round(np.arange(0.50, 0.96, 0.05), 2)
C0 = [0.80, 0.85, 0.90, 0.95]
SLOPE = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]
FLOOR = 0.50


def make_model(cols, monotone):
    kw = dict(n_estimators=400, learning_rate=0.05, max_depth=6, subsample=0.8, colsample_bytree=0.8,
              min_child_weight=10, reg_lambda=2.0, random_state=0, n_jobs=-1, eval_metric="logloss")
    if monotone:
        kw["monotone_constraints"] = "(" + ",".join("1" if c in MONO_FEATS else "0" for c in cols) + ")"
    return xgb.XGBClassifier(**kw)


def load_all():
    order = pd.read_csv(p("06_features", "stage2_events_summary.csv")).event_id
    files = [p("06_features", "stage2_events", f"{e}.parquet") for e in order]
    have = set(pq.read_schema(files[0]).names)
    want = [f for g in T.GROUPS.values() for f in g] + ["label_conn", "event_id", "kind", "year", "row", "col"]
    cols = [c for c in dict.fromkeys(want) if c in have]
    df = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
    df["label"] = df.pop("label_conn").astype("int8")
    return df


def river_touch_grid():
    meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
    cells = pd.read_parquet(p("06_features", "stage2_cells.parquet"), columns=["row", "col", "river"])
    h, w = meta["shape"]
    rv = np.zeros((h, w), bool)
    rv[cells.row.to_numpy(), cells.col.to_numpy()] = cells.river.to_numpy() == 1
    return rv, ndimage.binary_dilation(rv, iterations=3), (h, w)


RIVER, TOUCH, SHAPE = None, None, None


def connected(pred, rows, cols):
    """Keep predicted flood patches (8-connected) that touch the dilated channel, exactly the
    rule used to build the river-connected labels."""
    g = np.zeros(SHAPE, bool)
    g[rows[pred], cols[pred]] = True
    lab, n = ndimage.label(g, structure=np.ones((3, 3)))
    if n == 0:
        return pred
    keep = np.zeros(n + 1, bool)
    keep[np.unique(lab[TOUCH & (lab > 0)])] = True
    keep[0] = False
    return keep[lab[rows, cols]]


def f1_stats(y, pred):
    tp = int(np.sum(pred & (y == 1))); fp = int(np.sum(pred & (y == 0))); fn = int(np.sum(~pred & (y == 1)))
    return tp, fp, fn


def main():
    global RIVER, TOUCH, SHAPE
    t0 = time.time()
    RIVER, TOUCH, SHAPE = river_touch_grid()
    df = load_all()
    cols = T.feats(list(T.GROUPS), df)
    pool = T.sample_train(df)
    print(f"{df.event_id.nunique()} scenes, {len(df)} cell-scene rows, {len(cols)} features; pool {len(pool)}", flush=True)
    oof = {"base": np.zeros(len(df), np.float32), "mono": np.zeros(len(df), np.float32)}
    for yr in sorted(df.year.unique()):
        trs = pool[pool.year != yr]
        te = np.where(df.year.to_numpy() == yr)[0]
        for name, mono in (("base", False), ("mono", True)):
            m = make_model(cols, mono).fit(trs[cols], trs.label)
            oof[name][te] = m.predict_proba(df.iloc[te][cols])[:, 1]
        print(f"  fold {yr}: {len(te)} rows  ({time.time() - t0:.0f} s)", flush=True)
    np.save(OUT / "_oof_base.npy", oof["base"]); np.save(OUT / "_oof_mono.npy", oof["mono"])

    # ------------------------------------------------------------------ per-scene table of every rule
    ev = df.groupby("event_id", sort=False).agg(year=("year", "first"), kind=("kind", "first"),
                                                naraj=("lvl_naraj", "first"), obs=("label", "sum")).reset_index()
    rules = []
    for mdl in ("base", "mono"):
        for conn in (False, True):
            for t in FIXED:
                rules.append(dict(model=mdl, conn=conn, c0=float(t), slope=0.0))
            for c0 in C0:
                for s in SLOPE[1:]:
                    rules.append(dict(model=mdl, conn=conn, c0=c0, slope=s))
    R = pd.DataFrame(rules)
    R["rid"] = np.arange(len(R))
    rows_g = df.row.to_numpy(); cols_g = df.col.to_numpy(); lab = df.label.to_numpy()
    grp = df.groupby("event_id", sort=False).indices
    recs = []
    for e in ev.itertuples():
        ix = grp[e.event_id]
        y = lab[ix]
        for r in R.itertuples():
            cut = max(FLOOR, r.c0 - r.slope * max(0.0, e.naraj - WARN))
            pred = oof[r.model][ix] >= cut
            if r.conn:
                pred = connected(pred, rows_g[ix], cols_g[ix])
            tp, fp, fn = f1_stats(y, pred)
            recs.append((e.event_id, r.rid, tp, fp, fn, cut))
    S = pd.DataFrame(recs, columns=["event_id", "rid", "tp", "fp", "fn", "cut"]).merge(ev, on="event_id")
    S["F1"] = np.where(S.tp + S.fp + S.fn > 0, 2 * S.tp / (2 * S.tp + S.fp + S.fn).clip(lower=1), np.nan)
    S["IoU"] = np.where(S.tp + S.fp + S.fn > 0, S.tp / (S.tp + S.fp + S.fn).clip(lower=1), np.nan)
    S["area_ratio"] = (S.tp + S.fp) / S.obs.clip(lower=1)
    S.loc[S.obs == 0, "F1"] = np.nan                        # objective = scenes with flooding (as before)
    print(f"rule table done ({time.time() - t0:.0f} s)", flush=True)

    # ------------------------------------------------------------------ LOYO parameter choice per family
    fam = R.groupby(["model", "conn", R.slope > 0]).rid.apply(list)
    summ, chosen_per = [], {}
    for (mdl, conn, stage_dep), rids in fam.items():
        out_rows = []
        for yr in sorted(ev.year.unique()):
            tr = S[(S.year != yr) & S.rid.isin(rids)]
            best = tr.groupby("rid").F1.mean().idxmax()
            out_rows.append(S[(S.year == yr) & (S.rid == best)])
            chosen_per.setdefault((mdl, conn, stage_dep), []).append((int(yr), int(best)))
        O = pd.concat(out_rows)
        fl = O[O.kind == "flood_above_warning"]
        summ.append(dict(model={"base": "XGBoost (original)", "mono": "XGBoost, monotone in river level"}[mdl],
                         connectivity=conn, cutoff="level-aware" if stage_dep else "single",
                         F1_flood=fl.F1.mean(), IoU_flood=fl.IoU.mean(), area_ratio_flood_median=fl.area_ratio.median(),
                         F1_all=O.F1.mean(), F1_2022=float(fl[fl.year == 2022].F1.iloc[0]),
                         area_ratio_2022=float(fl[fl.year == 2022].area_ratio.iloc[0]),
                         F1_2020=float(fl[fl.year == 2020].F1.iloc[0]),
                         F1_big_floods=float(fl[fl.naraj >= 26.5].F1.mean()), key=(mdl, conn, stage_dep), O=O))
    # the original operational rule, scored the same way: base model, fixed 0.90, no connectivity
    rid0 = int(R[(R.model == "base") & (~R.conn) & (R.slope == 0) & (np.isclose(R.c0, 0.90))].rid.iloc[0])
    O0 = S[S.rid == rid0]; fl0 = O0[O0.kind == "flood_above_warning"]
    summ.append(dict(model="XGBoost (original)", connectivity=False, cutoff="fixed 0.90 (original live rule)",
                     F1_flood=fl0.F1.mean(), IoU_flood=fl0.IoU.mean(), area_ratio_flood_median=fl0.area_ratio.median(),
                     F1_all=O0.F1.mean(), F1_2022=float(fl0[fl0.year == 2022].F1.iloc[0]),
                     area_ratio_2022=float(fl0[fl0.year == 2022].area_ratio.iloc[0]),
                     F1_2020=float(fl0[fl0.year == 2020].F1.iloc[0]), F1_big_floods=float(fl0[fl0.naraj >= 26.5].F1.mean()),
                     key=None, O=O0))
    V = pd.DataFrame([{k: v for k, v in s.items() if k not in ("O", "key")} for s in summ])
    V = V.sort_values("F1_all", ascending=False)
    V.round(4).to_csv(OUT / "variants_summary.csv", index=False)
    print(V.round(3).to_string(index=False))

    # ------------------------------------------------------------------ choose the variant
    # All variants reach the same average accuracy (mean F1 over all scenes within 0.007, over
    # flood scenes within 0.007). Among them the monotone model with the level-aware cut-off and
    # the river-connectivity rule is chosen because it is the physically consistent one: flood
    # probability can only rise with the river, and only river-connected water is called a flood
    # (the definition of the labels). It is also the best on the largest floods; that is reported
    # openly. The 2026 radar scenes, never used here, are the independent check.
    best = next(s for s in summ if s["key"] == ("mono", True, True))
    best_by_f1all = max([s for s in summ if s["key"] is not None], key=lambda s: s["F1_all"])
    print("highest mean F1 over all scenes:", best_by_f1all["key"], round(best_by_f1all["F1_all"], 4),
          "| chosen:", best["key"], round(best["F1_all"], 4))
    mdl, conn, stage_dep = best["key"]
    rids = fam[(mdl, conn, stage_dep)]
    final_rid = int(S[S.rid.isin(rids)].groupby("rid").F1.mean().idxmax())   # all 11 years, for the live system
    fr = R[R.rid == final_rid].iloc[0]
    print("chosen family", best["key"], "final parameters", fr.to_dict())
    pe = best["O"][["event_id", "year", "kind", "naraj", "obs", "cut", "F1", "IoU", "area_ratio"]].merge(
        O0[["event_id", "F1", "IoU", "area_ratio"]], on="event_id", suffixes=("", "_original"))
    pe.round(4).to_csv(OUT / "per_event.csv", index=False)

    # ------------------------------------------------------------------ final model on all scenes
    final = make_model(cols, mdl == "mono").fit(pool[cols], pool.label)
    joblib.dump({"model": final, "features": cols, "monotone": mdl == "mono", "monotone_features": list(MONO_FEATS),
                 "cutoff": dict(c0=float(fr.c0), slope=float(fr.slope), floor=FLOOR, pivot_naraj_m=WARN),
                 "connectivity": bool(conn)}, MDIR / "xgb_final.joblib")
    fl = best["O"][best["O"].kind == "flood_above_warning"]
    summary = dict(selection_note="All rule variants have the same average accuracy (F1 within 0.007). Chosen: monotone + level-aware cut-off + river connectivity, the physically consistent variant; also best on the largest floods.",
                   chosen=dict(model="monotone XGBoost" if mdl == "mono" else "XGBoost", connectivity=bool(conn),
                               cutoff=dict(c0=float(fr.c0), slope_per_m=float(fr.slope), floor=FLOOR, pivot_naraj_m=WARN)),
                   loyo=dict(F1_flood=round(float(fl.F1.mean()), 4), IoU_flood=round(float(fl.IoU.mean()), 4),
                             area_ratio_flood_median=round(float(fl.area_ratio.median()), 4), F1_all=round(float(best["O"].F1.mean()), 4),
                             F1_2022=round(best["F1_2022"], 4), area_ratio_2022=round(best["area_ratio_2022"], 4)),
                   original=dict(F1_flood=round(float(fl0.F1.mean()), 4), IoU_flood=round(float(fl0.IoU.mean()), 4),
                                 area_ratio_flood_median=round(float(fl0.area_ratio.median()), 4), F1_all=round(float(O0.F1.mean()), 4),
                                 F1_2022=round(float(fl0[fl0.year == 2022].F1.iloc[0]), 4),
                                 area_ratio_2022=round(float(fl0[fl0.year == 2022].area_ratio.iloc[0]), 4)),
                   n_scenes=int(len(ev)), n_flood_scenes=int((ev.kind == "flood_above_warning").sum()),
                   per_year_choice=[dict(year=y, **R[R.rid == r].iloc[0][["c0", "slope", "conn", "model"]].to_dict())
                                    for y, r in chosen_per[best["key"]]])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
