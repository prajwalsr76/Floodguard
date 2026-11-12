#!/usr/bin/env python3
"""
Export the frozen FloodGuard models and static grid into a compact, exact form that the
dashboard's in-browser engine (engine.js) evaluates. Every exported model is re-evaluated
here with plain numpy using the SAME arithmetic the browser uses and compared with the
library prediction; the script stops if any difference exceeds the tolerance.

  Stage 1 (per gauge): Ridge (folded scaler), Random forest, XGBoost (+24 h),
                       XGBoost +48 h / +72 h, residual banks, NaN fill values
  Stage 2            : XGBoost classifier with every split expressed as a bin index,
                       per-cell static feature bins, elevation, nearest gauge,
                       normal-monsoon reference probability class, grid mask

Arithmetic contract (mirrored in engine.js)
  * XGBoost: x32 = fround(x); go left iff x32 < cond (cond is float32); NaN -> default_left.
    Output = base_margin + sum(leaf) of the first best_iteration+1 trees.
  * sklearn RF: x32 = fround(x); go left iff x32 <= thr, thr stored as the largest
    float32 <= the float64 threshold (exactly equivalent for float32 inputs).
  * Ridge: y = b + sum(w_j * x_j) in float64 with w = coef/scale, b folded.

Out: 08_results/realtime/engine_assets.json  (arrays base64, large ones gzip+base64)
"""
import base64, gzip, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import joblib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
TARGETS = ["Naraj", "Kishan Nagar", "Tarpur"]


def slug(s):
    return s.lower().replace(" ", "_")


def b64(a, gz=False):
    raw = np.ascontiguousarray(a).tobytes()
    if gz:
        raw = gzip.compress(raw, 9, mtime=0)
    return base64.b64encode(raw).decode()


def f32_floor(x):
    """Largest float32 <= x (elementwise)."""
    t = np.asarray(x, np.float64).astype(np.float32)
    up = t.astype(np.float64) > np.asarray(x, np.float64)
    t[up] = np.nextafter(t[up], np.float32(-np.inf))
    return t


# ----------------------------------------------------------------------------- XGBoost
def xgb_trees(booster, n_trees=None):
    j = json.loads(booster.save_raw("json"))
    trees = j["learner"]["gradient_booster"]["model"]["trees"]
    if n_trees is not None:
        trees = trees[:n_trees]
    base = float(j["learner"]["learner_model_param"]["base_score"].strip("[]"))
    obj = j["learner"]["objective"]["name"]
    margin = np.log(base / (1 - base)) if obj == "binary:logistic" else base
    L, R, F, C, DL, V, H, start = [], [], [], [], [], [], [], [0]
    for t in trees:
        n = len(t["left_children"])
        L += t["left_children"]; R += t["right_children"]; F += t["split_indices"]
        C += t["split_conditions"]; DL += t["default_left"]; H += t["sum_hessian"]
        start.append(start[-1] + n)
    T = dict(left=np.array(L, np.int32), right=np.array(R, np.int32), feat=np.array(F, np.int32),
             cond=np.array(C, np.float32), dleft=np.array(DL, np.uint8), hess=np.array(H, np.float32),
             start=np.array(start, np.int32))
    return T, float(margin), obj


def xgb_eval(T, margin, X):
    """Reference evaluator with the browser's rules. X: float64 2-D (NaN = missing)."""
    X32 = X.astype(np.float32)
    out = np.full(len(X), margin, np.float64)
    for k in range(len(T["start"]) - 1):
        s = T["start"][k]
        node = np.zeros(len(X), np.int64)
        active = np.ones(len(X), bool)
        while active.any():
            gi = s + node[active]
            leaf = T["left"][gi] == -1
            idx = np.where(active)[0]
            li = idx[leaf]
            out[li] += T["cond"][gi[leaf]]
            active[li] = False
            idx, gi = idx[~leaf], gi[~leaf]
            x = X32[idx, T["feat"][gi]]
            go_left = np.where(np.isnan(x), T["dleft"][gi] == 1, x < T["cond"][gi])
            node[idx] = np.where(go_left, T["left"][gi], T["right"][gi])
    return out


def pack_xgb(T, margin, n_feat):
    return dict(margin=margin, n_trees=int(len(T["start"]) - 1), n_feat=n_feat,
                left=b64(T["left"], True), right=b64(T["right"], True), feat=b64(T["feat"].astype(np.int16), True),
                cond=b64(T["cond"], True), dleft=b64(T["dleft"], True), start=b64(T["start"], True))


# ----------------------------------------------------------------------------- RF / Ridge
def pack_rf(rf):
    """sklearn depth-first trees: an internal node's left child is the next node, so a node
    is (feature uint8, 255 = leaf; value float32 = threshold or leaf value; right offset uint16)."""
    FE, VA, RO, start = [], [], [], [0]
    for e in rf.estimators_:
        t = e.tree_
        n = t.node_count
        internal = t.children_left != -1
        assert (t.children_left[internal] == np.where(internal)[0] + 1).all()
        off = np.where(internal, t.children_right - np.arange(n), 0)
        assert off.max() < 65535 and t.feature.max() < 255
        FE.append(np.where(internal, t.feature, 255).astype(np.uint8))
        VA.append(np.where(internal, f32_floor(t.threshold), t.value[:, 0, 0].astype(np.float32)).astype(np.float32))
        RO.append(off.astype(np.uint16))
        start.append(start[-1] + n)
    return dict(feat=np.concatenate(FE), val=np.concatenate(VA), roff=np.concatenate(RO), start=np.array(start, np.int32))


def rf_eval(A, X):
    X32 = X.astype(np.float32)
    acc = np.zeros(len(X))
    nt = len(A["start"]) - 1
    rows = np.arange(len(X))
    for k in range(nt):
        s = A["start"][k]
        node = np.zeros(len(X), np.int64)
        while True:
            gi = s + node
            leaf = A["feat"][gi] == 255
            if leaf.all():
                break
            x = X32[rows, np.where(leaf, 0, A["feat"][gi])]
            nxt = np.where(x <= A["val"][gi], node + 1, node + A["roff"][gi])
            node = np.where(leaf, node, nxt)
        acc += A["val"][s + node].astype(np.float64)
    return acc / nt


def ridge_fold(pipe):
    sc, rg = pipe.steps[0][1], pipe.steps[1][1]
    w = rg.coef_ / sc.scale_
    b = rg.intercept_ - np.sum(rg.coef_ * sc.mean_ / sc.scale_)
    return w.astype(np.float64), float(b)


# ----------------------------------------------------------------------------- main
def main():
    out = {"stage1": {}, "contract": "x32<cond (xgb), x32<=floor32(thr) (rf)"}
    sp = CFG["stage1"]["split"]
    for st in TARGETS:
        s = slug(st)
        from realtime.run_realtime import load_stage1
        M = load_stage1(st)
        F = M["features"]
        tab = pd.read_csv(p("06_features", f"stage1_{s}.csv"), parse_dates=["date"]).set_index("date")
        fill = np.asarray(M["fill"], np.float64)
        test = tab[(tab.index >= sp["test"][0])][F].to_numpy(np.float64)
        test_f = np.where(np.isnan(test), fill, test)
        # XGBoost +24 h, first best_iteration+1 trees
        nb = M["xgb_trees"]
        T, mg, _ = xgb_trees(M["xgb"].get_booster(), nb)
        ref = M["xgb"].predict(pd.DataFrame(test, columns=F))
        mine = xgb_eval(T, mg, test)
        e1 = np.abs(ref - mine).max()
        # RF
        A = pack_rf(M["rf"])
        ref_rf = M["rf"].predict(pd.DataFrame(test_f, columns=F))
        e2 = np.abs(ref_rf - rf_eval(A, test_f)).max()
        # Ridge
        w, b = ridge_fold(M["ridge"])
        e3 = np.abs(M["ridge"].predict(pd.DataFrame(test_f, columns=F)) - (test_f @ w + b)).max()
        mh = M["h"]
        hz = {}
        for h in (2, 3):
            mdl = mh[h]["model"]; nbh = M["h_trees"][h]
            Th, mgh, _ = xgb_trees(mdl.get_booster(), nbh)
            eh = np.abs(mdl.predict(pd.DataFrame(test, columns=F)) - xgb_eval(Th, mgh, test)).max()
            assert eh < 2e-5, (st, h, eh)
            hz[str(h)] = dict(xgb=pack_xgb(Th, mgh, len(F)),
                              res_normal=b64(np.round(np.sort(np.round(mh[h]["res"]["normal"], 4)) * 1e4).astype(np.int32)),
                              res_high=b64(np.round(np.sort(np.round(mh[h]["res"]["high"], 4)) * 1e4).astype(np.int32)))
        print(f"{st}: xgb max|diff| {e1:.2e}  rf {e2:.2e}  ridge {e3:.2e}  ({nb} xgb trees, {len(A['start'])-1} rf trees)")
        assert e1 < 2e-5 and e2 < 1e-6 and e3 < 1e-8, (e1, e2, e3)
        bank = {k: np.sort(np.round(np.asarray(v), 4)) for k, v in M["bank"].items()}
        out["stage1"][st] = dict(features=F, fill=fill.tolist(), ridge=dict(w=w.tolist(), b=b), xgb=pack_xgb(T, mg, len(F)),
                                 rf=dict(n_trees=int(len(A["start"]) - 1), feat=b64(A["feat"], True), val=b64(A["val"], True),
                                         roff=b64(A["roff"], True), start=b64(A["start"])),
                                 res_normal=b64(np.round(bank["normal"] * 1e4).astype(np.int32)),
                                 res_high=b64(np.round(bank["high"] * 1e4).astype(np.int32)),
                                 h=hz)

    # ---------------------------------------------------------------- Stage 2
    meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
    h, wdt = meta["shape"]
    cells = pd.read_parquet(p("06_features", "stage2_cells.parquet"))
    assert (np.diff(cells.cell_id) > 0).all() and (cells.cell_id == cells.row * wdt + cells.col).all()
    import realtime.run_realtime as RR
    RR.P_LIKELY = float(pd.read_csv(p("08_results", "stage2_connected", "cv_tuned_summary.csv"), index_col=0).loc["mean_threshold", "value"])
    S2 = RR.Stage2()
    M2 = S2.M
    F2 = M2["features"]
    T2, mg2, obj = xgb_trees(M2["model"].get_booster())
    assert obj == "binary:logistic"
    leaf = T2["left"] == -1
    # bins: per feature, sorted unique split conditions; bin(x) = #conds <= x ; left iff bin <= k
    conds = {f: np.unique(T2["cond"][(~leaf) & (T2["feat"] == i)]) for i, f in enumerate(F2)}
    kidx = np.full(len(T2["cond"]), -1, np.int32)
    for i, f in enumerate(F2):
        m = (~leaf) & (T2["feat"] == i)
        kidx[m] = np.searchsorted(conds[f], T2["cond"][m])
    leafval = np.where(leaf, T2["cond"], 0).astype(np.float32)
    static = ["elev_m", "slope_deg", "har_m", "dist_river_m", "jrc_occurrence"] + [f for f in F2 if f.startswith("lc_")]
    dyn = [f for f in F2 if f not in static]
    print("stage 2 static", static, "\n        dynamic", dyn)
    bins = {}
    for f in static:
        x = cells[f].to_numpy(np.float32)
        bn = np.searchsorted(conds[f], x, side="right").astype(np.int32)
        bn[np.isnan(x)] = 65535
        bins[f] = bn
        assert len(conds[f]) < 65535
    mask = np.zeros(h * wdt, np.uint8); mask[cells.cell_id.to_numpy()] = 1
    rt = json.loads(p("08_results", "realtime", "dashboard_data.json").read_text())
    out["stage2"] = dict(features=F2, static=static, dynamic=dyn, margin=mg2, n_trees=int(len(T2["start"]) - 1),
                         conds={f: conds[f].tolist() for f in F2},
                         left=b64(T2["left"], True), right=b64(T2["right"], True), feat=b64(T2["feat"].astype(np.int16), True),
                         kidx=b64(kidx, True), leaf=b64(leafval, True), dleft=b64(T2["dleft"], True),
                         hess=b64(T2["hess"], True), start=b64(T2["start"], True),
                         bins={f: dict(dtype="u16" if len(conds[f]) >= 255 else "u8",
                                       data=b64(np.where(bins[f] == 65535, 65535 if len(conds[f]) >= 255 else 255, bins[f]).astype(
                                           np.uint16 if len(conds[f]) >= 255 else np.uint8), True)) for f in static},
                         elev=b64(cells.elev_m.to_numpy(np.float32), True),
                         near=b64(cells.near_station.to_numpy().astype(np.uint8), True),
                         river=b64((cells.river.to_numpy() == 1).astype(np.uint8), True),
                         mask=b64(mask, True), refcls=b64(np.load(p("08_results", "realtime", "ref_class.npy")).astype(np.uint8), True),
                         w=wdt, h=h, res_km=meta["res"] / 1000, p_likely=S2.likely_cut(None),
                         cut=dict(c0=S2.cut["c0"], slope=S2.cut["slope"], floor=S2.cut["floor"], pivot=S2.cut["pivot_naraj_m"]),
                         connectivity=S2.conn, touch=b64(S2.touch.astype(np.uint8), True))
    # check the bin formulation against the library on a realistic flood input
    from stage2.build_training_table import add_dynamic
    lv = {"Tikarpara": np.nan, "Naraj": 26.4, "Kishan Nagar": 14.9, "Tarpur": 10.6}
    df = add_dynamic(cells.copy(), lv, {"rain_chirps_sum1": 7.0, "rain_chirps_sum3": 60.0, "rain_chirps_sum7": 150.0})
    X = df[F2].to_numpy(np.float64)
    ref = M2["model"].get_booster().predict(__import__("xgboost").DMatrix(df[F2]), output_margin=True)
    mine = xgb_eval(T2, mg2, X)
    e = np.abs(ref - mine).max()
    print(f"stage 2 margin max|diff| {e:.2e} over {len(X)} cells")
    assert e < 1e-4
    # bin formulation == threshold formulation
    Xb = np.zeros((len(X), len(F2)), np.int64)
    for i, f in enumerate(F2):
        x = X[:, i].astype(np.float32)
        Xb[:, i] = np.where(np.isnan(x), -1, np.searchsorted(conds[f], x, side="right"))
    sub = np.arange(0, len(X), 37)
    mb = np.full(len(sub), mg2)
    for k in range(len(T2["start"]) - 1):
        s = T2["start"][k]; node = np.zeros(len(sub), np.int64); act = np.ones(len(sub), bool)
        while act.any():
            gi = s + node[act]; lf = T2["left"][gi] == -1; ix = np.where(act)[0]
            mb[ix[lf]] += leafval[gi[lf]]; act[ix[lf]] = False
            ix, gi = ix[~lf], gi[~lf]
            bv = Xb[sub[ix], T2["feat"][gi]]
            gl = np.where(bv < 0, T2["dleft"][gi] == 1, bv <= kidx[gi])
            node[ix] = np.where(gl, T2["left"][gi], T2["right"][gi])
    eb = np.abs(mb - mine[sub]).max()
    print(f"stage 2 bin-form max|diff| {eb:.2e}")
    assert eb < 1e-9
    out["stage2"]["selftest"] = dict(levels={k: v for k, v in lv.items() if k != "Tikarpara"}, rain={"1": 7.0, "3": 60.0, "7": 150.0},
                                     margin_sample_idx=sub[:200].tolist(), margin_sample=[float(x) for x in mine[sub[:200]]])
    p("08_results", "realtime", "engine_assets.json").write_text(json.dumps(out, separators=(",", ":")))
    print("wrote engine_assets.json", round(p("08_results", "realtime", "engine_assets.json").stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
