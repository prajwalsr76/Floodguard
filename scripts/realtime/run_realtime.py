#!/usr/bin/env python3
"""
FloodGuard real-time engine: live CWC gauge levels + live rainfall -> the SAME trained
Stage-1 and Stage-2 models -> +24 h warnings, flood-probability maps and a JSON bundle
for the dashboard.

Inputs (written by fetch_live_inputs.py, or by the browser fetch used on 2026-10-21):
  01_raw/live/cwc_ffs_daily.csv          station,date,daily_mean_m,daily_max_m,daily_min_m,n_obs
  01_raw/live/cwc_ffs_hourly.csv         station,time,level_m          (last ~7 days)
  01_raw/live/openmeteo_zone_daily_hist.csv   date + 4 zone columns (mm/day, 2022-01-01..today)
  01_raw/live/openmeteo_zone_daily_fc.csv     date + 4 zone columns (past 14 d + 7-day forecast)
  01_raw/live/osm_places.json            [name, place, lat, lon, population]

What is live and what is frozen
  * Models: frozen. Stage-1 tabular models (Ridge, RF, XGBoost; trained 2014-2020) and the
    Stage-2 XGBoost (river-connected labels, decision threshold from cv_tuned_summary.csv).
    Nothing here is re-fitted on 2026 data, so 2026 is a genuinely unseen season.
  * Water levels: CWC FFS hourly HHS series = the same source as the manual archive
    (identical daily max on 296-355 overlapping days in 2025, see levels_check in output).
  * Rainfall: CHIRPS v3 until 2025-12-31 (training product); from 2026-01-01 Open-Meteo
    daily precipitation, averaged over the same 4 sub-catchment zones and rescaled per zone
    to the CHIRPS climatology (JJASO 2022-2025 ratio). CHIRPS itself arrives weeks late,
    so it cannot drive a real-time system.

Stage 1 operational forecast = mean of Ridge + RF + XGBoost (lowest test RMSE at 2 of 3
stations). Uncertainty = empirical residual quantiles of that ensemble on the 2022-2025 test
period, split by flow regime (today's max within 2 m of the warning level or not).
P(exceed warning) = share of those residuals that would push the forecast over the level.

Output: 08_results/realtime/dashboard_data.json  + map PNGs (base64 inside the JSON)
"""
import base64, io, json, sys, warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
import xgboost as xgb
from PIL import Image
from pyproj import Transformer
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402
from stage1.build_features import station_frame, rainfall_features  # noqa: E402
from stage2.build_training_table import add_dynamic  # noqa: E402

warnings.filterwarnings("ignore")
CFG = load_config()
S1 = CFG["stage1"]
THR = CFG["thresholds"]
TARGETS = ["Naraj", "Kishan Nagar", "Tarpur"]
ALL_ST = ["Tikarpara", "Naraj", "Kishan Nagar", "Tarpur"]
ZONES = ["z0_above_hirakud", "z1_hirakud_tikarpara", "z2_tikarpara_naraj", "z3_floodplain"]
LIVE = p("01_raw", "live")
OUT = p("08_results", "realtime")
CUTOVER = pd.Timestamp("2026-01-01")      # first day served by live feeds
REGIME_BAND_M = 2.0
P_LIKELY = None                            # set from cv_tuned_summary.csv in main()


def slug(s):
    return s.lower().replace(" ", "_")


def r2(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 2)


# ----------------------------------------------------------------------------- inputs
def load_levels():
    hist = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    live = pd.read_csv(LIVE / "cwc_ffs_daily.csv", parse_dates=["date"])
    # consistency check on the overlap with the QC'd archive (same CWC source)
    ov = live.merge(hist, on=["station", "date"], suffixes=("_live", "_arch"))
    check = {st: dict(days=int(len(g)), mean_abs_diff_max_m=round(float((g.daily_max_m_live - g.daily_max_m_arch).abs().mean()), 3),
                      share_identical=round(float(((g.daily_max_m_live - g.daily_max_m_arch).abs() < 0.005).mean()), 3))
             for st, g in ov.groupby("station")}
    # plausibility QC on live days (same idea as fix_daily_water_level.py): bounds from the archive
    qc_log = []
    live = live[live.date >= CUTOVER].copy()
    for st in ALL_ST:
        g = hist[hist.station == st]
        lo, hi = g.daily_min_m.quantile(0.001) - 1.0, g.daily_max_m.quantile(0.999) + 1.5
        m = (live.station == st) & ((live.daily_min_m < lo) | (live.daily_max_m > hi))
        for _, r in live[m].iterrows():
            qc_log.append(f"{st} {r.date.date()}: outside plausible range [{lo:.2f}, {hi:.2f}] -> removed")
        live.loc[m, ["daily_mean_m", "daily_max_m", "daily_min_m"]] = np.nan
    live = live.rename(columns={"n_obs": "n_observations"})
    d = pd.concat([hist[hist.date < CUTOVER], live], ignore_index=True)
    return d.sort_values(["station", "date"]), check, qc_log


def qc_bounds(d):
    """Plausible daily range per gauge from the QC'd archive (same rule as load_levels)."""
    hist = d[d.date < CUTOVER]
    return {st: [round(float(g.daily_min_m.quantile(0.001) - 1.0), 2), round(float(g.daily_max_m.quantile(0.999) + 1.5), 2)]
            for st, g in hist.groupby("station")}


def zone_points_cached():
    from realtime.fetch_live_inputs import zone_points
    return {z: [[a, b] for a, b in pts] for z, pts in zone_points().items()}


def load_rain():
    ch = pd.read_csv(p("06_features", "chirps_daily_subcatchments.csv"), parse_dates=["date"]).set_index("date")
    om = pd.read_csv(LIVE / "openmeteo_zone_daily_hist.csv", parse_dates=["date"]).set_index("date")
    fc = pd.read_csv(LIVE / "openmeteo_zone_daily_fc.csv", parse_dates=["date"]).set_index("date")
    j = om.join(ch, rsuffix="_ch", how="inner")
    mon = j.index.month.isin([6, 7, 8, 9, 10])
    scale, corr = {}, {}
    for z in ZONES:
        scale[z] = float(j.loc[mon, z + "_ch"].sum() / j.loc[mon, z].sum())
        corr[z] = round(float(j[z].rolling(7).sum().corr(j[z + "_ch"].rolling(7).sum())), 3)
    live = om[om.index >= CUTOVER].copy()
    # the forecast endpoint carries the freshest values for the last days; prefer it there
    recent = fc[fc.index <= live.index.max()]
    live.update(recent)
    for z in ZONES:
        live[z] = live[z] * scale[z]
    rain = pd.concat([ch[ch.index < CUTOVER], live]).sort_index()
    today = live.index.max()
    fut = fc[fc.index > today].copy()
    for z in ZONES:
        fut[z] = fut[z] * scale[z]
    return rain, fut, dict(scale=scale, corr_7day_vs_chirps=corr, overlap="2022-01-01..2025-12-31")


# ----------------------------------------------------------------------------- Stage 1
def build_X(target, d, rain):
    L = S1["lags_days"]
    tf, tg = station_frame(d, target, L)
    X = tf.add_prefix("own__")
    for up in S1["upstream_of"][target]:
        if up != target:
            uf, _ = station_frame(d, up, L)
            X = X.join(uf.add_prefix("up__"), how="left")
    tmp = OUT / "_rain_tmp.csv"
    rain.reset_index().rename(columns={"index": "date"}).to_csv(tmp, index=False)
    X = X.join(rainfall_features(tmp, L), how="left")
    tmp.unlink()
    doy = X.index.dayofyear
    X["season__doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    X["season__doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    X["season__monsoon"] = X.index.month.isin([6, 7, 8, 9, 10]).astype(int)
    X["base_max"] = X[f"own__{slug(target)}_max_lag0"]
    return X, tg.daily_max_m


STAGE1_SET = __import__("os").environ.get("FG_STAGE1", "operational")   # "frozen" = research models (2014-2020)


def load_stage1(target):
    """Uniform view of the Stage-1 models used live.
    operational (default): Ridge + RF + XGBoost retrained on 2014-2025 (train_operational.py),
                           residual banks from rolling-origin 2022-2025 forecasts.
    frozen               : the research models trained on 2014-2020 (train_models.py)."""
    if STAGE1_SET == "operational":
        M = joblib.load(p("07_models", "stage1_operational", f"{slug(target)}.joblib"))
        return dict(features=M["features"], xgb=M["xgb"], rf=M["rf"], ridge=M["ridge"], fill=np.asarray(M["fill"], np.float64),
                    bank=M["res"], h={h: {"model": M["h"][h]["model"], "res": M["h"][h]["res"]} for h in (2, 3)},
                    xgb_trees=M["xgb_trees"], h_trees={h: M["h"][h]["n_trees"] for h in (2, 3)}, trained="2014-2025")
    M = joblib.load(p("07_models", "stage1", f"{slug(target)}_tabular.joblib"))
    mh = multi_horizon_models(target, None)
    return dict(features=M["features"], xgb=M["xgb"], rf=M["rf"], ridge=M["ridge"], fill=train_fill(target),
                bank=residual_bank(target), h={h: mh[h] for h in (2, 3)}, xgb_trees=M["xgb"].best_iteration + 1,
                h_trees={h: mh[h]["model"].best_iteration + 1 for h in (2, 3)}, trained="2014-2020")


def train_fill(target):
    """Median of each Stage-1 input over the 2014-2020 training rows: the value Ridge and RF
    use when an input is missing (XGBoost handles missing inputs natively)."""
    M = joblib.load(p("07_models", "stage1", f"{slug(target)}_tabular.joblib"))
    tab = pd.read_csv(p("06_features", f"stage1_{slug(target)}.csv"), parse_dates=["date"]).set_index("date")
    sp = S1["split"]
    return tab[(tab.index >= sp["train"][0]) & (tab.index <= sp["train"][1])][M["features"]].median().to_numpy(np.float64)


def km2(count):
    """Cell count (100 m cells) -> km2 rounded to 0.1, half up (identical rule in the browser)."""
    return float(np.floor(count / 10 + 0.5) / 10)


def residual_bank(target):
    """Ensemble residuals (observed - forecast) on the 2022-2025 test period, by regime."""
    pr = pd.read_csv(p("08_results", "stage1", f"predictions_{slug(target)}.csv"))
    ens = (pr.Ridge + pr.RandomForest + pr.XGBoost) / 3
    res = (pr.observed - ens).to_numpy()
    high = (pr.Persistence >= THR[target]["warning"] - REGIME_BAND_M).to_numpy()
    # rounded to 0.1 mm so the browser engine can carry the identical bank as integers
    return {"normal": np.sort(np.round(res[~high], 4)), "high": np.sort(np.round(res[high], 4))}


def multi_horizon_models(target, d):
    """Direct +48 h / +72 h XGBoost models, same protocol as multi_horizon.py (train 2014-20,
    early stopping on 2021). Trained on the archive only (CHIRPS era), then frozen."""
    f = p("07_models", "stage1_multi", f"{slug(target)}_h23.joblib")
    if f.exists():
        return joblib.load(f)
    X = pd.read_csv(p("06_features", f"stage1_{slug(target)}.csv"), parse_dates=["date"]).set_index("date")
    feats = joblib.load(p("07_models", "stage1", f"{slug(target)}_tabular.joblib"))["features"]
    s = d[(d.station == target) & (d.date < CUTOVER)].set_index("date").daily_max_m.asfreq("D")
    sp, ok, out = S1["split"], X[feats].notna().all(axis=1), {"features": feats}
    for h in (2, 3):
        y = s.shift(-h).reindex(X.index)
        m = ok & y.notna()
        tr = m & (X.index >= sp["train"][0]) & (X.index <= sp["train"][1])
        va = m & (X.index >= sp["val"][0]) & (X.index <= sp["val"][1])
        te = m & (X.index >= sp["test"][0]) & (X.index <= sp["test"][1])
        mdl = xgb.XGBRegressor(n_estimators=2000, learning_rate=0.03, max_depth=4, subsample=0.8, colsample_bytree=0.7,
                               min_child_weight=3, early_stopping_rounds=100, random_state=0)
        base = X["base_max"]
        mdl.fit(X.loc[tr, feats], (y - base)[tr], eval_set=[(X.loc[va, feats], (y - base)[va])], verbose=False)
        res = (y[te] - (base[te] + mdl.predict(X.loc[te, feats]))).to_numpy()
        hi = (base[te] >= THR[target]["warning"] - REGIME_BAND_M).to_numpy()
        out[h] = {"model": mdl, "res": {"normal": np.sort(res[~hi]), "high": np.sort(res[hi])}}
    f.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(out, f)
    return out


def interval(pred, base, thr_w, bank):
    reg = "high" if base >= thr_w - REGIME_BAND_M else "normal"
    r = bank[reg] if len(bank[reg]) >= 30 else np.concatenate([bank["normal"], bank["high"]])
    lo, hi = np.quantile(r, [0.1, 0.9])
    return pred + lo, pred + hi, float(np.mean(pred + r >= thr_w)), reg


def wilson(k, n, z=1.96):
    """95% Wilson interval for a proportion k/n (small samples: 22 warning days, 17 onsets)."""
    if n == 0:
        return None, None
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return round(float(c - h), 3), round(float(c + h), 3)


def contingency(obs, fc, thr):
    o, f = obs >= thr, fc >= thr
    tp, fp, fn = int((o & f).sum()), int((~o & f).sum()), int((o & ~f).sum())
    return dict(hits=tp, false_alarms=fp, misses=fn,
                POD=r2(tp / (tp + fn)) if tp + fn else None, FAR=r2(fp / (tp + fp)) if tp + fp else None,
                CSI=r2(tp / (tp + fp + fn)) if tp + fp + fn else None)


def run_stage1(d, rain):
    res, series, verif = {}, {}, {}
    for st in TARGETS:
        M = load_stage1(st)
        X, ymax = build_X(st, d, rain)
        F = M["features"]
        ok = X.base_max.notna()
        Xs = X[ok]
        Xf = Xs[F].fillna(pd.Series(M["fill"], index=F))          # fixed training medians (same in browser)
        members = {"XGBoost": Xs.base_max + M["xgb"].predict(Xs[F]),
                   "RandomForest": Xs.base_max + M["rf"].predict(Xf),
                   "Ridge": Xs.base_max + M["ridge"].predict(Xf)}
        ens = (members["XGBoost"] + members["RandomForest"] + members["Ridge"]) / 3
        bank = {k: np.sort(np.round(v, 4)) for k, v in M["bank"].items()}
        mh = dict(M["h"]); mh["features"] = F
        for h_ in (2, 3):
            mh[h_]["res"] = {k: np.sort(np.round(v, 4)) for k, v in mh[h_]["res"].items()}
        w, dg = THR[st]["warning"], THR[st]["danger"]
        rows = []
        fch = {h_: pd.Series(Xs.base_max + mh[h_]["model"].predict(Xs[mh["features"]]), index=Xs.index) for h_ in (2, 3)}
        for t in Xs.index[Xs.index >= CUTOVER - pd.Timedelta(days=1)]:
            lo, hi, pex, reg = interval(ens[t], Xs.base_max[t], w, bank)
            obs = ymax.get(t + pd.Timedelta(days=1), np.nan)
            rows.append(dict(issue=t, valid=t + pd.Timedelta(days=1), base=Xs.base_max[t], fc=ens[t], lo=lo, hi=hi,
                             p_warn=pex, obs=obs, pers=Xs.base_max[t], fc48=fch[2][t], fc72=fch[3][t],
                             spread=max(m[t] for m in members.values()) - min(m[t] for m in members.values())))
        S = pd.DataFrame(rows).set_index("issue")
        # verification on 2026 (unseen season): pairs with an observation
        V = S[S.obs.notna() & (S.valid >= CUTOVER)]
        e, ep = V.fc - V.obs, V.pers - V.obs
        mon = V.valid.dt.month.isin([6, 7, 8, 9, 10])
        verif[st] = dict(n_days=int(len(V)), MAE=r2(e.abs().mean()) if len(V) else None,
                         RMSE=round(float(np.sqrt((e ** 2).mean())), 3), RMSE_persistence=round(float(np.sqrt((ep ** 2).mean())), 3),
                         skill_vs_persistence=r2(1 - (e ** 2).mean() / (ep ** 2).mean()),
                         RMSE_monsoon=round(float(np.sqrt((e[mon] ** 2).mean())), 3),
                         RMSE_persistence_monsoon=round(float(np.sqrt((ep[mon] ** 2).mean())), 3),
                         skill_monsoon=r2(1 - (e[mon] ** 2).mean() / (ep[mon] ** 2).mean()),
                         inside_80pct_band=r2(((V.obs >= V.lo) & (V.obs <= V.hi)).mean()),
                         warning=contingency(V.obs, V.fc, w), warning_persistence=contingency(V.obs, V.pers, w),
                         warning_ci=dict(zip(("pod_lo", "pod_hi"), wilson(int(((V.obs >= w) & (V.fc >= w)).sum()), int((V.obs >= w).sum())))),
                         stage1_models=load_stage1(st)["trained"],
                         days_above_warning_obs=int((V.obs >= w).sum()), days_above_danger_obs=int((V.obs >= dg).sum()),
                         peak_obs=r2(V.obs.max()), peak_obs_date=str(V.obs.idxmax().date() + pd.Timedelta(days=1)) if len(V) else None)
        # latest issue = last day with a level (may be a partial day: flagged in meta)
        t0 = Xs.index.max()
        last = S.loc[t0]
        ext = {}
        for h in (2, 3):
            row = Xs.loc[[t0], mh["features"]]
            pr = float(Xs.base_max[t0] + mh[h]["model"].predict(row)[0])
            lo, hi, pex, _ = interval(pr, Xs.base_max[t0], w, mh[h]["res"])
            ext[f"+{24 * h}h"] = dict(valid=str((t0 + pd.Timedelta(days=h)).date()), fc=r2(pr), lo=r2(lo), hi=r2(hi), p_warn=round(pex, 3))
        res[st] = dict(issue=str(t0.date()), valid=str(last.valid.date()), today_max=r2(last.base), fc=r2(last.fc), lo=r2(last.lo),
                       hi=r2(last.hi), p_warn=round(float(last.p_warn), 3), regime=interval(last.fc, last.base, w, bank)[3],
                       members={k: r2(v[t0]) for k, v in members.items()}, outlook=ext,
                       status=status(last.fc, last.hi, st))
        series[st] = S.reset_index()
        verif[st]["lead_time"] = lead_times(S, ymax, w)
    return res, series, verif


def lead_times(S, ymax, w):
    """Warning onsets of 2026 (observed daily max crosses the warning level after a day below)
    and how early FloodGuard's +24/+48/+72 h forecasts for that day were already above it."""
    obs = ymax[ymax.index >= CUTOVER]
    out = []
    for D in obs.index[1:]:
        prev = obs.get(D - pd.Timedelta(days=1), np.nan)
        if not (obs[D] >= w and prev < w):
            continue
        lead = watch = 0
        for h_, col in ((3, "fc72"), (2, "fc48"), (1, "fc")):
            t = D - pd.Timedelta(days=h_)
            if t in S.index and np.isfinite(S.loc[t, col]) and S.loc[t, col] >= w:
                lead = 24 * h_
                break
        for h_, col in ((3, "fc72"), (2, "fc48"), (1, "fc")):     # Watch: within 0.5 m (or +24 h range above)
            t = D - pd.Timedelta(days=h_)
            if t in S.index and np.isfinite(S.loc[t, col]) and (S.loc[t, col] >= w - 0.5 or (h_ == 1 and S.loc[t, "hi"] >= w)):
                watch = 24 * h_
                break
        ep = obs[(obs.index >= D) & (obs.index <= D + pd.Timedelta(days=10))]
        end = ep[ep < w].index.min() if (ep < w).any() else ep.index.max() + pd.Timedelta(days=1)
        peak = obs[(obs.index >= D) & (obs.index < end)]
        out.append(dict(onset=str(D.date()), peak=r2(peak.max()), peak_date=str(peak.idxmax().date()),
                        days_above=int(len(peak)), lead_h=lead, watch_h=watch))
    return out


def status(fc, hi, st):
    w, dg = THR[st]["warning"], THR[st]["danger"]
    if fc >= dg:
        return "RED"
    if fc >= w:
        return "ORANGE"
    if hi >= w or fc >= w - 0.5:
        return "YELLOW"
    return "GREEN"


# ----------------------------------------------------------------------------- Stage 2
STAGE2_SET = __import__("os").environ.get("FG_STAGE2", "improved")   # "original" = cut-off 0.90 model


class Stage2:
    """Stage-2 map model and the rules that turn probabilities into risk classes.
    improved (default): monotone XGBoost, level-aware cut-off, river-connectivity rule
                        (scripts/stage2/improve_stage2.py). original: XGBoost, fixed cut-off."""

    def __init__(self, which=None):
        which = which or STAGE2_SET
        self.which = which
        self.meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
        self.h, self.w = self.meta["shape"]
        self.cells = pd.read_parquet(p("06_features", "stage2_cells.parquet"))
        if which == "improved":
            self.M = joblib.load(p("07_models", "stage2_improved", "xgb_final.joblib"))
            self.cut = self.M["cutoff"]
            self.conn = bool(self.M["connectivity"])
        else:
            self.M = joblib.load(p("07_models", "stage2_connected", "xgb_final.joblib"))
            self.cut = dict(c0=P_LIKELY, slope=0.0, floor=P_LIKELY, pivot_naraj_m=THR["Naraj"]["warning"])
            self.conn = False
        self.river = self.cells.river.to_numpy() == 1
        self.idx = (self.cells.row.to_numpy(), self.cells.col.to_numpy())
        rv = np.zeros((self.h, self.w), bool); rv[self.idx] = self.river
        self.touch_grid = ndimage.binary_dilation(rv, iterations=3)       # same rule as the labels
        self.touch = self.touch_grid[self.idx]
        import shap
        self.explainer = shap.TreeExplainer(self.M["model"])
        self.ref_cls = np.zeros(len(self.cells), np.uint8)

    def likely_cut(self, naraj):
        c = self.cut
        L = c["pivot_naraj_m"] if naraj is None or not np.isfinite(naraj) else naraj
        return float(max(c["floor"], c["c0"] - c["slope"] * max(0.0, L - c["pivot_naraj_m"])))

    def predict(self, lv, rain):
        df = add_dynamic(self.cells.copy(), {"Tikarpara": np.nan, **lv},
                         {f"rain_chirps_sum{n}": rain[n] for n in (1, 3, 7)})
        prob = self.M["model"].predict_proba(df[self.M["features"]])[:, 1].astype("float32")
        prob[self.river] = np.nan
        self.last_naraj = float(lv["Naraj"])
        return prob, df

    def raw_classes(self, prob, naraj):
        pl = self.likely_cut(naraj)
        pr = np.nan_to_num(prob).astype(np.float64)
        c = np.digitize(pr, [0.30, min(0.60, pl), pl]).astype(np.uint8)
        c[self.river] = 0
        if self.conn:
            g = np.zeros((self.h, self.w), bool); g[self.idx] = c == 3
            lab, n = ndimage.label(g, structure=np.ones((3, 3)))
            keep = np.zeros(n + 1, bool); keep[np.unique(lab[self.touch_grid & (lab > 0)])] = True; keep[0] = False
            c[(c == 3) & ~keep[lab[self.idx]]] = 2
        return c

    def set_reference(self, lv, rain):
        """Normal monsoon water: what the model maps at the median of the 11 low-stage reference
        scenes. Removed from 'flood'."""
        self.ref, _ = self.predict(lv, rain)
        self.ref_cls = self.raw_classes(self.ref, lv["Naraj"])
        self.ref_wet = self.ref_cls == 3
        np.save(OUT / "ref_class.npy", self.ref_cls)
        return km2(self.ref_wet.sum())

    def classes(self, prob, naraj=None):
        """Risk class above the normal-monsoon reference: 0 none, 1 watch, 2 elevated, 3 likely."""
        naraj = self.last_naraj if naraj is None else naraj
        cv = self.raw_classes(prob, naraj)
        c = np.where(cv > self.ref_cls, cv, 0).astype(np.uint8)
        c[self.river] = 0
        return c

    def summary(self, prob, df, with_shap=True):
        c = self.classes(prob, float(df.lvl_naraj.iloc[0]))
        out = dict(km2_likely=km2((c == 3).sum()), km2_elevated=km2((c == 2).sum()), km2_watch=km2((c == 1).sum()),
                   likely_cut=round(self.likely_cut(float(df.lvl_naraj.iloc[0])), 4))
        hi_idx = np.where(c == 3)[0]
        out["shap"] = []
        if with_shap and len(hi_idx) >= 20:
            step = int(np.ceil(len(hi_idx) / 300))                 # deterministic sample, <= 300 cells
            sub = df.iloc[hi_idx[::step]][self.M["features"]]
            sv = pd.Series(self.explainer.shap_values(sub).mean(0), index=self.M["features"])
            out["shap"] = [dict(feature=k, value=round(float(x), 3)) for k, x in sv.sort_values(key=abs, ascending=False).head(7).items()]
        return out

    def png(self, prob, naraj=None):
        rgba = np.zeros((self.h, self.w, 4), "uint8")
        cg = np.zeros((self.h, self.w), np.uint8); cg[self.idx] = self.classes(prob, naraj)
        for k, col in [(1, (254, 217, 118, 150)), (2, (253, 141, 60, 205)), (3, (189, 0, 38, 235))]:
            rgba[cg == k] = col                          # only risk ABOVE the normal-monsoon reference
        ref = np.zeros((self.h, self.w), bool); ref[self.idx] = self.ref_wet & ~self.river
        rgba[ref & (cg == 0)] = (96, 165, 250, 170)      # normal monsoon water, not a flood
        return b64png(rgba)

    def place_risk(self, prob, places, naraj=None):
        """Highest risk class within ~1 km (11 x 11 cells) of each settlement point."""
        g = np.zeros((self.h, self.w), "uint8")
        g[self.idx] = self.classes(prob, naraj)
        mx = ndimage.maximum_filter(g, size=11)
        return {pl["name"]: int(mx[pl["row"], pl["col"]]) for pl in places}

    def area_stats(self, prob, area_of_cell, n_areas, naraj=None):
        """Flood-likely / elevated km2 per named area, and built-up / cropland km2 in flood-likely cells."""
        c = self.classes(prob, naraj)
        lik, ele = c == 3, c == 2
        a_l = np.bincount(area_of_cell[lik], minlength=n_areas)
        a_e = np.bincount(area_of_cell[ele], minlength=n_areas)
        built = float(self.cells.lc_built_frac.to_numpy()[lik].sum()) / 100
        g = np.zeros((self.h, self.w), np.uint8); g[self.idx] = lik
        near = ndimage.maximum_filter(g, size=11)[self.idx] == 1           # within 500 m (5 cells)
        built_near = float(np.nan_to_num(self.cells.lc_built_frac.to_numpy())[near].sum()) / 100
        crop = float(self.cells.lc_crop_frac.to_numpy()[lik].sum()) / 100
        return dict(area_km2=[round(x / 100, 2) for x in a_l], area_elev_km2=[round(x / 100, 2) for x in a_e],
                    km2_likely=km2(lik.sum()), built_km2=round(built, 2), built_near_km2=round(built_near, 2), crop_km2=round(crop, 2))


def b64png(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def base_layers(s2):
    """Hillshade-free, theme-neutral base: AOI footprint, height-above-river shading, river."""
    h, w, idx, c = s2.h, s2.w, s2.idx, s2.cells
    aoi = np.zeros((h, w, 4), "uint8")
    har = np.clip(c.har_m.to_numpy(), -2, 25)
    t = (har + 2) / 27.0                          # 0 = low ground near river level, 1 = high ground
    shade = (60 + 140 * t).astype("uint8")
    aoi[idx] = np.stack([shade, shade, shade, np.full_like(shade, 70)], axis=1)
    riv = np.zeros((h, w, 4), "uint8")
    riv[(idx[0][s2.river], idx[1][s2.river])] = (37, 99, 235, 255)
    return b64png(aoi), b64png(riv)


def to_pix(s2, lat, lon):
    tr = Transformer.from_crs("EPSG:4326", s2.meta["crs"], always_xy=True)
    x, y = tr.transform(lon, lat)
    a = s2.meta["transform"]
    return int((y - a[5]) / a[4]), int((x - a[2]) / a[0])


def rain_sums(rain, t):
    r = rain["z3_floodplain"]
    return {n: float(r.loc[t - pd.Timedelta(days=n - 1):t].sum()) for n in (1, 3, 7)}


# ----------------------------------------------------------------------------- main
def main():
    global P_LIKELY
    OUT.mkdir(parents=True, exist_ok=True)
    P_LIKELY = float(pd.read_csv(p("08_results", "stage2_connected", "cv_tuned_summary.csv"), index_col=0)
                     .loc["mean_threshold", "value"])
    d, lcheck, qc_log = load_levels()
    rain, rain_fc, rain_cal = load_rain()
    print("levels overlap check:", lcheck)
    print("rain calibration:", rain_cal)
    s1, series, verif = run_stage1(d, rain)
    for st in TARGETS:
        print(st, s1[st]["issue"], "->", s1[st]["valid"], s1[st]["fc"], s1[st]["status"], "| 2026 verif", verif[st]["RMSE"],
              verif[st]["skill_vs_persistence"], verif[st]["warning"])

    s2 = Stage2()
    # reference = the 11 'low-stage reference' Sentinel-1 scenes: monsoon days with the river
    # well below warning. Water the model maps there is the normal monsoon river, not a flood.
    hist0 = d[d.date < CUTOVER].pivot_table(index="date", columns="station", values="daily_max_m")
    evs = pd.read_csv(p("06_features", "stage2_events_summary.csv"), parse_dates=["s1_date"])
    ref_days = evs[evs.kind == "low_stage_reference"].s1_date
    ref_lv = {st: float(hist0[st].reindex(ref_days).median()) for st in TARGETS}
    ref_rain = {n: float(np.median([rain_sums(rain, t - pd.Timedelta(days=1))[n] for t in ref_days])) for n in (1, 3, 7)}
    ref_km2 = s2.set_reference(ref_lv, ref_rain)
    print("reference low-flow water", ref_lv, ref_km2, "km2")
    places = []
    for nm, kind, lat, lon, pop in json.load(open(LIVE / "osm_places.json")):
        r, c = to_pix(s2, lat, lon)
        if 0 <= r < s2.h and 0 <= c < s2.w and kind in ("village", "town", "city"):
            places.append(dict(name=nm, kind=kind, lat=lat, lon=lon, row=r, col=c))
    # keep places that are inside the AOI footprint
    inside = np.zeros((s2.h, s2.w), bool); inside[s2.idx] = True
    places = [pl for pl in places if inside[pl["row"], pl["col"]]]
    names = [pl["name"] for pl in places]
    for pl in places:                                    # e.g. Jagatpur is both a town and a village
        if names.count(pl["name"]) > 1:
            pl["name"] = f"{pl['name']} ({pl['kind']})"
    # named areas: every 100 m cell belongs to its nearest settlement ("the Salepur area")
    from scipy.spatial import cKDTree
    _, area_of_cell = cKDTree(np.array([[pl["row"], pl["col"]] for pl in places])).query(
        np.c_[s2.idx[0], s2.idx[1]])
    area_of_cell = area_of_cell.astype(np.int64)
    area_names = [pl["name"] for pl in places]
    NA = len(area_names)
    stations = {st: dict(zip(("row", "col"), to_pix(s2, CFG["stations"][st]["lat"], CFG["stations"][st]["lon"]))) for st in TARGETS}

    maps = {}
    # (a) live map: today's issue -> tomorrow
    t0 = pd.Timestamp(s1["Naraj"]["issue"])
    lv = {st: s1[st]["fc"] for st in TARGETS}
    rs = rain_sums(rain, t0)
    prob, df = s2.predict(lv, rs)
    maps["live"] = dict(label=f"Live forecast for {s1['Naraj']['valid']}", issue=str(t0.date()), valid=s1["Naraj"]["valid"],
                        levels=lv, rain_mm={str(k): round(v, 1) for k, v in rs.items()}, png=s2.png(prob),
                        places=s2.place_risk(prob, places), areas=s2.area_stats(prob, area_of_cell, NA), **s2.summary(prob, df))

    # (b) replays of the 2026 peaks, issued the day before with the frozen models
    S = series["Naraj"].set_index("issue")
    mon26 = S[(S.valid >= "2026-06-01")]
    peaks = []
    cand = mon26.obs.dropna()
    for _ in range(3):                                   # three largest separated peaks
        if cand.empty:
            break
        t = cand.idxmax()
        if cand[t] < THR["Naraj"]["warning"] - 0.5:
            break
        peaks.append(t)
        cand = cand[(cand.index < t - pd.Timedelta(days=10)) | (cand.index > t + pd.Timedelta(days=10))]
    for t in sorted(peaks):
        lv = {st: float(series[st].set_index("issue").fc.get(t, np.nan)) for st in TARGETS}
        lv_obs = {st: float(series[st].set_index("issue").obs.get(t, np.nan)) for st in TARGETS}
        rs = rain_sums(rain, t)
        prob, df = s2.predict(lv, rs)
        key = f"replay_{(t + pd.Timedelta(days=1)):%Y%m%d}"
        maps[key] = dict(label=f"2026 flood replay: valid {(t + pd.Timedelta(days=1)).date()}", issue=str(t.date()),
                         valid=str((t + pd.Timedelta(days=1)).date()), levels={k: r2(v) for k, v in lv.items()},
                         levels_observed={k: r2(v) for k, v in lv_obs.items()},
                         rain_mm={str(k): round(v, 1) for k, v in rs.items()}, png=s2.png(prob),
                         places=s2.place_risk(prob, places), areas=s2.area_stats(prob, area_of_cell, NA), **s2.summary(prob, df))

    # (c) what-if ladder: Naraj level -> downstream levels from the archive relation
    hist = d[d.date < CUTOVER].pivot_table(index="date", columns="station", values="daily_max_m")
    hist = hist.dropna(subset=["Naraj", "Kishan Nagar", "Tarpur"])
    ev = pd.read_csv(p("06_features", "stage2_events_summary.csv"), parse_dates=["s1_date"])
    flood_days = ev[ev.kind == "flood_above_warning"].s1_date - pd.Timedelta(days=1)
    rmed = {n: float(np.median([rain_sums(rain, t)[n] for t in flood_days])) for n in (1, 3, 7)}
    ladder, env = [], None
    for L in np.round(np.arange(23.0, 28.01, 0.5), 2):
        near = hist[(hist.Naraj - L).abs() <= 0.3]
        if len(near) < 5:
            near = hist.iloc[(hist.Naraj - L).abs().argsort()[:15]]
        lv = {"Naraj": float(L), "Kishan Nagar": float(near["Kishan Nagar"].median()), "Tarpur": float(near["Tarpur"].median())}
        prob, df = s2.predict(lv, rmed)
        # monotone envelope: a cell flooded at a lower stage stays flooded at a higher one
        env = prob if env is None else np.fmax(env, prob)
        prob = env.copy()
        sm = s2.summary(prob, df)
        ladder.append(dict(naraj=float(L), levels={k: r2(v) for k, v in lv.items()}, png=s2.png(prob),
                           places=s2.place_risk(prob, places), areas=s2.area_stats(prob, area_of_cell, NA), **sm))
        print(f"what-if Naraj {L:.1f}: likely {sm['km2_likely']} km2")
    # village trigger level = lowest ladder step with P >= P_LIKELY within 1.1 km
    triggers = {}
    for pl in places:
        lvl = next((s["naraj"] for s in ladder if s["places"][pl["name"]] >= 3), None)
        triggers[pl["name"]] = lvl

    # 2026 daily predicted inundated area (Stage 1 forecast -> Stage 2), only where it can matter
    area26 = []
    for st_ in [series["Naraj"]]:
        for _, r in st_[st_.valid >= CUTOVER].iterrows():
            t = r.issue
            if not np.isfinite(r.fc):
                continue
            if r.fc < 23.0:
                area26.append(dict(valid=str(r.valid.date()), km2=0.0)); continue
            lv = {s: float(series[s].set_index("issue").fc.get(t, np.nan)) for s in TARGETS}
            prob, df = s2.predict(lv, rain_sums(rain, t))
            area26.append(dict(valid=str(r.valid.date()), km2=s2.summary(prob, df, with_shap=False)["km2_likely"],
                               obs_naraj=r2(r.obs), fc_naraj=r2(r.fc)))

    # ---------------------------------------------------------------- package
    hourly = pd.read_csv(LIVE / "cwc_ffs_hourly.csv", parse_dates=["time"])
    cwc_meta = json.loads((LIVE / "cwc_meta.json").read_text())
    om_meta = json.loads((LIVE / "openmeteo_meta.json").read_text())
    aoi_png, riv_png = base_layers(s2)
    daily = d[(d.date >= "2026-05-01")]
    out = dict(
        generated_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sources=dict(cwc=cwc_meta, openmeteo=om_meta, rain_calibration=rain_cal, levels_overlap_check=lcheck, live_qc_log=qc_log),
        thresholds={st: {k: THR[st][k] for k in ("warning", "danger", "hfl")} for st in ALL_ST},
        qc_bounds=qc_bounds(d), rain_scale=rain_cal["scale"], zone_points=zone_points_cached(),
        p_likely=P_LIKELY, reference=dict(levels={k: r2(v) for k, v in ref_lv.items()}, rain_mm={str(k): round(v, 1) for k, v in ref_rain.items()}, km2=ref_km2, n_scenes=int(len(ref_days))),
        latest_obs={st: dict(time=str(g.time.max()), level=float(g.sort_values("time").level_m.iloc[-1]),
                             change_24h=r2(g.sort_values("time").level_m.iloc[-1] - g.set_index("time").level_m.asof(g.time.max() - pd.Timedelta(hours=24))))
                    for st, g in hourly.groupby("station")},
        hourly={st: [[t.strftime("%Y-%m-%dT%H:00"), float(v)] for t, v in zip(g.time, g.level_m)] for st, g in hourly.groupby("station")},
        daily={st: [[str(r.date.date()), r2(r.daily_max_m)] for r in g.itertuples()] for st, g in daily.groupby("station")},
        stage1=s1,
        series={st: [[str(r.valid.date()), r2(r.fc), r2(r.lo), r2(r.hi), r2(r.obs)] for r in S_.itertuples() if r.valid >= pd.Timestamp("2026-05-01")]
                for st, S_ in series.items()},
        verification_2026=verif,
        rain=dict(recent={z: [[str(i.date()), round(float(v), 1)] for i, v in rain[z].iloc[-21:].items()] for z in ZONES},
                  forecast={z: [[str(i.date()), round(float(v), 1)] for i, v in rain_fc[z].items()] for z in ZONES}),
        grid=dict(w=s2.w, h=s2.h, aoi_png=aoi_png, river_png=riv_png, stations=stations,
                  places=[{k: pl[k] for k in ("name", "kind", "row", "col")} for pl in places],
                  area_names=area_names,
                  mask_bits=base64.b64encode(np.packbits(inside.ravel())).decode(),
                  area_of_cell=base64.b64encode(area_of_cell.astype(np.uint8).tobytes()).decode(),
                  built_u8=base64.b64encode(np.round(s2.cells.lc_built_frac.fillna(0).to_numpy() * 255).astype(np.uint8).tobytes()).decode(),
                  crop_u8=base64.b64encode(np.round(s2.cells.lc_crop_frac.fillna(0).to_numpy() * 255).astype(np.uint8).tobytes()).decode()),
        stage2_rule=dict(model="monotone XGBoost" if s2.which == "improved" else "XGBoost", cut=s2.cut, connectivity=s2.conn),
        stage1_models={st: dict(trained=load_stage1(st)["trained"]) for st in TARGETS},
        maps=maps, ladder=ladder, ladder_rain_mm={str(k): round(v, 1) for k, v in rmed.items()},
        village_trigger_naraj_m=triggers, area_2026=area26)
    (OUT / "dashboard_data.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
    write_live_static(out, d, rain, ladder, area_names)
    print("wrote", OUT / "dashboard_data.json", round((OUT / "dashboard_data.json").stat().st_size / 1e6, 2), "MB")


DEMOS = {"jul2026": ("July-August 2026 flood (peak 26.61 m at Naraj, above danger)", "2026-07-18", "2026-08-08"),
         "sep2026": ("September 2026 flood (peak 26.44 m at Naraj, above danger)", "2026-09-20", "2026-10-06")}


def write_live_static(out, d, rain, ladder, area_names):
    """Small file the live server needs (09_dashboard/live_static.json): thresholds, QC bounds,
    rain points and scale, the what-if area table, and the 2026 flood replays for demo mode."""
    demo = {}
    for key, (label, a, b) in DEMOS.items():
        lo = (pd.Timestamp(a) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        hi = (pd.Timestamp(b) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        g = d[(d.date >= lo) & (d.date <= hi)]
        daily = {st: {str(r.date.date()): [float(r.daily_mean_m), float(r.daily_max_m), float(r.daily_min_m)]
                      for r in gg.itertuples() if np.isfinite(r.daily_max_m)} for st, gg in g.groupby("station")}
        rr = rain.loc[lo:hi]
        demo[key] = dict(label=label, days=[str(t.date()) for t in pd.date_range(a, b)], daily=daily,
                         rain={z: {str(i.date()): round(float(v), 3) for i, v in rr[z].items()} for z in ZONES})
    steps = [dict(naraj=s["naraj"], km2_likely=s["areas"]["km2_likely"], area_km2=s["areas"]["area_km2"],
                  built_km2=s["areas"]["built_near_km2"], crop_km2=s["areas"]["crop_km2"]) for s in ladder]
    info = {st: dict(stage1_trained=load_stage1(st)["trained"]) for st in TARGETS}
    live = dict(thresholds=out["thresholds"], qc_bounds=out["qc_bounds"], rain_scale=out["rain_scale"],
                zone_points=out["zone_points"], area_ladder=dict(areas=area_names, steps=steps), demo=demo,
                model_info=dict(stage1=info, stage2=out["stage2_rule"]), generated_utc=out["generated_utc"])
    (OUT / "live_static.json").write_text(json.dumps(live, separators=(",", ":"), default=str))
    print("wrote", OUT / "live_static.json")


if __name__ == "__main__":
    main()
