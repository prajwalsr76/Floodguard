#!/usr/bin/env python3
"""
FloodGuard Stage 1 - train and evaluate next-day (+24 h) water-level models.

Models   : Persistence, Ridge (linear), Random Forest, XGBoost, LSTM, GRU
Target   : next-day daily max level; models predict the change dy and add it to
           today's max (tree models cannot extrapolate absolute levels).
Split    : chronological train / val / test from config (no shuffling).
Scaling  : fitted on TRAIN only.
Fairness : every model is scored on the SAME test rows (complete-case rows that
           also have a full LSTM look-back window).
Metrics  : MAE, RMSE, NSE, skill vs persistence, RMSE on high-flow days, and
           threshold-exceedance Precision/Recall/F1/CSI (Naraj = CWC warning level;
           other stations = provisional train-set 95th percentile until CWC levels
           are verified).
Ablation : XGBoost & Ridge on feature groups own -> +upstream -> +season -> +rain.

Outputs  : 08_results/stage1/metrics_<target>.csv, ablation_<target>.csv,
           predictions_<target>.csv, plots, and 07_models/stage1/<target>_*.
"""
import sys, json, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
import xgboost as xgb
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

warnings.filterwarnings("ignore")
CFG = load_config()
S1 = CFG["stage1"]
SEQ_LEN = 10
SEEDS = [0, 1, 2]
torch.set_num_threads(2)


def slug(s):
    return s.lower().replace(" ", "_")


# ----------------------------------------------------------------- metrics
def metrics(y, yhat, ypers, thr):
    e = yhat - y
    mse_p = np.mean((ypers - y) ** 2)
    hi = y > np.quantile(y, 0.95)
    out = dict(MAE=np.mean(np.abs(e)), RMSE=np.sqrt(np.mean(e ** 2)),
               NSE=1 - np.sum(e ** 2) / np.sum((y - y.mean()) ** 2),
               skill_vs_persistence=1 - np.mean(e ** 2) / mse_p,
               RMSE_top5pct=np.sqrt(np.mean(e[hi] ** 2)))
    obs, pred = y > thr, yhat > thr
    tp, fp, fn = np.sum(obs & pred), np.sum(~obs & pred), np.sum(obs & ~pred)
    out.update(threshold_m=thr, n_exceed_obs=int(obs.sum()),
               precision=tp / (tp + fp) if tp + fp else np.nan,
               recall=tp / (tp + fn) if tp + fn else np.nan,
               CSI=tp / (tp + fp + fn) if tp + fp + fn else np.nan)
    out["F1"] = (2 * out["precision"] * out["recall"] / (out["precision"] + out["recall"])
                 if out["precision"] and out["recall"] and not np.isnan(out["precision"]) else np.nan)
    return out


# ----------------------------------------------------------------- data
def load(target):
    X = pd.read_csv(p("06_features", f"stage1_{slug(target)}.csv"), parse_dates=["date"]).set_index("date")
    feats = [c for c in X.columns if "__" in c]
    return X, feats


def split_idx(index):
    sp = S1["split"]
    return {k: (index >= v[0]) & (index <= v[1]) for k, v in sp.items()}


def select(feats, groups):
    return [f for f in feats if f.split("__")[0] in groups]


# ----------------------------------------------------------------- sequence models
class RNN(nn.Module):
    def __init__(self, n_in, cell, hidden=32):
        super().__init__()
        self.rnn = (nn.LSTM if cell == "LSTM" else nn.GRU)(n_in, hidden, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x):
        o, _ = self.rnn(x)
        return self.head(o[:, -1]).squeeze(-1)


def seq_feature_cols(feats):
    """Per-day (lag-0) variables only; the RNN sees the history itself."""
    keep = []
    for f in feats:
        g, name = f.split("__", 1)
        if g == "season" or name.endswith(("mean_lag0", "max_lag0", "min_lag0", "_chg1", "_d0")):
            keep.append(f)
    return keep


def make_sequences(X, cols):
    A = X[cols].to_numpy(dtype=np.float32)
    idx = X.index
    full_day = pd.Series(pd.date_range(idx.min(), idx.max(), freq="D"))
    pos = pd.Series(np.arange(len(idx)), index=idx).reindex(full_day.values)
    seqs, ok = np.zeros((len(idx), SEQ_LEN, len(cols)), np.float32), np.zeros(len(idx), bool)
    posv = pos.to_numpy()
    for i, d in enumerate(idx):
        j = full_day.searchsorted(d)
        win = posv[j - SEQ_LEN + 1:j + 1] if j - SEQ_LEN + 1 >= 0 else []
        if len(win) == SEQ_LEN and not np.isnan(win).any():
            w = A[win.astype(int)]
            if not np.isnan(w).any():
                seqs[i], ok[i] = w, True
    return seqs, ok


def train_rnn(cell, Xs_tr, y_tr, Xs_va, y_va, seed):
    torch.manual_seed(seed); np.random.seed(seed)
    m = RNN(Xs_tr.shape[-1], cell)
    opt = torch.optim.Adam(m.parameters(), lr=2e-3, weight_decay=1e-5)
    lossf = nn.MSELoss()
    tr = torch.utils.data.TensorDataset(torch.tensor(Xs_tr), torch.tensor(y_tr, dtype=torch.float32))
    dl = torch.utils.data.DataLoader(tr, batch_size=64, shuffle=True)
    xv, yv = torch.tensor(Xs_va), torch.tensor(y_va, dtype=torch.float32)
    best, best_state, bad = 1e9, None, 0
    for ep in range(150):
        m.train()
        for xb, yb in dl:
            opt.zero_grad(); l = lossf(m(xb), yb); l.backward(); opt.step()
        m.eval()
        with torch.no_grad():
            v = lossf(m(xv), yv).item()
        if v < best - 1e-5:
            best, best_state, bad = v, {k: t.clone() for k, t in m.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= 15:
                break
    m.load_state_dict(best_state)
    return m


# ----------------------------------------------------------------- main per target
def run(target):
    X, feats = load(target)
    S = split_idx(X.index)
    thr_cfg = CFG["thresholds"].get(target, {}).get("warning")
    thr = thr_cfg if thr_cfg else float(X.loc[np.asarray(S["train"]), "y_next_max"].quantile(0.95))
    thr_src = ("CWC warning level" if target == "Naraj" else "Naraj-equivalent warning level (derived)") if thr_cfg else "provisional train 95th percentile"

    groups_full = ["own", "up", "season"] + (["rain"] if any(f.startswith("rain__") for f in feats) else [])
    full = select(feats, groups_full)
    seq_cols = seq_feature_cols(full)
    seqs, seq_ok = make_sequences(X, seq_cols)
    complete = X[full].notna().all(axis=1).to_numpy() & seq_ok
    print(f"\n=== {target} | threshold {thr:.2f} m ({thr_src}) | complete rows {complete.sum()} of {len(X)}")

    y, base = X["y_next_max"].to_numpy(), X["base_max"].to_numpy()
    dy = X["dy_next_max"].to_numpy()
    m_tr, m_va, m_te = (np.asarray(S[k]) & complete for k in ("train", "val", "test"))
    print(f"rows: train {m_tr.sum()}  val {m_va.sum()}  test {m_te.sum()}")
    preds = {"Persistence": base[m_te]}
    models = {}

    def fit_tab(name, est, cols):
        Xtr, Xte = X.loc[m_tr, cols].to_numpy(), X.loc[m_te, cols].to_numpy()
        est.fit(Xtr, dy[m_tr])
        return base[m_te] + est.predict(Xte), est

    preds["Ridge"], models["Ridge"] = fit_tab("Ridge", make_pipeline(StandardScaler(), Ridge(alpha=1.0)), full)
    preds["RandomForest"], models["RandomForest"] = fit_tab(
        "RF", RandomForestRegressor(n_estimators=400, min_samples_leaf=3, max_features=0.5,
                                    n_jobs=-1, random_state=0), full)
    xg = xgb.XGBRegressor(n_estimators=2000, learning_rate=0.03, max_depth=4, subsample=0.8,
                          colsample_bytree=0.7, min_child_weight=3, reg_lambda=1.0,
                          early_stopping_rounds=100, random_state=0)
    xg.fit(X.loc[m_tr, full], dy[m_tr], eval_set=[(X.loc[m_va, full], dy[m_va])], verbose=False)
    preds["XGBoost"], models["XGBoost"] = base[m_te] + xg.predict(X.loc[m_te, full]), xg

    # sequence models (scaler fit on train only)
    sc = StandardScaler().fit(seqs[m_tr].reshape(-1, len(seq_cols)))
    norm = lambda a: sc.transform(a.reshape(-1, len(seq_cols))).reshape(a.shape).astype(np.float32)
    ysc = dy[m_tr].std()
    for cell in ("LSTM", "GRU"):
        outs = []
        for seed in SEEDS:
            net = train_rnn(cell, norm(seqs[m_tr]), dy[m_tr] / ysc, norm(seqs[m_va]), dy[m_va] / ysc, seed)
            with torch.no_grad():
                outs.append(net(torch.tensor(norm(seqs[m_te]))).numpy() * ysc)
            models[f"{cell}_seed{seed}"] = net
        preds[cell] = base[m_te] + np.mean(outs, axis=0)

    rows = []
    for name, yh in preds.items():
        r = metrics(y[m_te], yh, base[m_te], thr); r["model"] = name; rows.append(r)
    res = pd.DataFrame(rows).set_index("model")

    # ablation (XGBoost and Ridge), same test rows
    abl = []
    steps = [("own", ["own"]), ("own+upstream", ["own", "up"]), ("own+upstream+season", ["own", "up", "season"])]
    if "rain" in groups_full:
        steps += [("rain_only+season", ["rain", "season"]), ("all (+rain)", groups_full)]
    for label, g in steps:
        cols = select(feats, g)
        if not cols:
            continue
        for nm, est in [("Ridge", make_pipeline(StandardScaler(), Ridge(alpha=1.0))),
                        ("XGBoost", xgb.XGBRegressor(n_estimators=xg.best_iteration + 1, learning_rate=0.03,
                                                     max_depth=4, subsample=0.8, colsample_bytree=0.7,
                                                     min_child_weight=3, random_state=0))]:
            est.fit(X.loc[m_tr, cols], dy[m_tr])
            r = metrics(y[m_te], base[m_te] + est.predict(X.loc[m_te, cols]), base[m_te], thr)
            r.update(model=nm, inputs=label, n_features=len(cols)); abl.append(r)
    abl = pd.DataFrame(abl)

    out = p("08_results", "stage1"); out.mkdir(parents=True, exist_ok=True)
    mdir = p("07_models", "stage1"); mdir.mkdir(parents=True, exist_ok=True)
    s = slug(target)
    res.round(4).to_csv(out / f"metrics_{s}.csv")
    abl.round(4).to_csv(out / f"ablation_{s}.csv", index=False)
    pr = pd.DataFrame({"date": X.index[m_te], "observed": y[m_te], **{k: v for k, v in preds.items()}})
    pr.to_csv(out / f"predictions_{s}.csv", index=False)
    joblib.dump({"xgb": xg, "ridge": models["Ridge"], "rf": models["RandomForest"], "features": full,
                 "threshold": thr, "threshold_source": thr_src}, mdir / f"{s}_tabular.joblib")
    for k, net in models.items():
        if k.startswith(("LSTM", "GRU")):
            torch.save(net.state_dict(), mdir / f"{s}_{k}.pt")
    joblib.dump({"scaler": sc, "seq_cols": seq_cols, "seq_len": SEQ_LEN, "dy_scale": ysc}, mdir / f"{s}_seq_meta.joblib")
    plot(target, pr, thr, out / f"forecast_{s}.png")
    print(res[["MAE", "RMSE", "NSE", "skill_vs_persistence", "RMSE_top5pct", "precision", "recall", "CSI"]].round(3).to_string())
    print(abl[["model", "inputs", "n_features", "RMSE", "NSE", "skill_vs_persistence", "CSI"]].round(3).to_string(index=False))
    return res, abl


def plot(target, pr, thr, path):
    pr = pr.set_index("date")
    years = sorted(set(pr.index.year))
    fig, axes = plt.subplots(len(years), 1, figsize=(11, 2.6 * len(years)), squeeze=False)
    for ax, yr in zip(axes[:, 0], years):
        q = pr[(pr.index.year == yr) & (pr.index.month.isin([6, 7, 8, 9, 10]))]
        if q.empty:
            ax.axis("off"); continue
        ax.plot(q.index, q.observed, color="#222", lw=1.6, label="Observed")
        ax.plot(q.index, q.XGBoost, color="#1f77b4", lw=1.1, label="XGBoost")
        ax.plot(q.index, q.LSTM, color="#d62728", lw=1.0, ls="--", label="LSTM")
        ax.plot(q.index, q.Persistence, color="#999", lw=0.8, ls=":", label="Persistence")
        ax.axhline(thr, color="#e69f00", lw=0.9, label="Threshold")
        ax.set_title(f"{target} - monsoon {yr} (next-day daily max, test period)", fontsize=9)
        ax.set_ylabel("m")
    axes[0, 0].legend(fontsize=7, ncol=5, loc="upper left")
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


if __name__ == "__main__":
    allres = []
    for t in S1["targets"]:
        r, _ = run(t); r["target"] = t; allres.append(r)
    pd.concat(allres).round(4).to_csv(p("08_results", "stage1", "metrics_all_targets.csv"))
