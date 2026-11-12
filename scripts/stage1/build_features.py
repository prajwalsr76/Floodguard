#!/usr/bin/env python3
"""
FloodGuard Stage 1 - build the daily modelling table (one row per forecast-issue day t).

Target   : y = daily_max at the target station on day t+1 (flood-relevant)
           (also stored: next-day daily_mean). Models learn dy = y(t+1) - max(t).
Features : only information available at the end of day t (no leakage):
  * water level  : mean/max/min, lags 0..L-1, 1-day changes, 3/7-day rolling max,
                   for the target station and every station UPSTREAM of it
  * rainfall     : (optional, when CHIRPS sub-catchment CSV exists) daily rain,
                   lags, 3/5/7/10-day antecedent totals per sub-catchment
  * season       : day-of-year sin/cos, monsoon flag
Each feature carries a group prefix so ablations can select modalities:
  own__*, up__*, rain__*, season__*

Output: 06_features/stage1_<target>.csv
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
S1 = CFG["stage1"]
DAILY = p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv")


def slug(s):
    return s.lower().replace(" ", "_")


def station_frame(d, station, L):
    g = d[d.station == station].set_index("date").sort_index()
    g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq="D"))   # explicit gaps -> NaN
    s = slug(station)
    f = pd.DataFrame(index=g.index)
    for k in range(L):
        f[f"{s}_mean_lag{k}"] = g.daily_mean_m.shift(k)
        f[f"{s}_max_lag{k}"] = g.daily_max_m.shift(k)
    f[f"{s}_min_lag0"] = g.daily_min_m
    f[f"{s}_chg1"] = g.daily_mean_m - g.daily_mean_m.shift(1)
    f[f"{s}_chg2"] = g.daily_mean_m - g.daily_mean_m.shift(2)
    f[f"{s}_maxchg1"] = g.daily_max_m - g.daily_max_m.shift(1)
    f[f"{s}_rmax3"] = g.daily_max_m.rolling(3, min_periods=3).max()
    f[f"{s}_rmax7"] = g.daily_max_m.rolling(7, min_periods=7).max()
    f[f"{s}_intraday_range"] = g.daily_max_m - g.daily_min_m
    return f, g


def rainfall_features(path, L):
    if not path.exists():
        return None
    r = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    f = pd.DataFrame(index=r.index)
    for c in [c for c in r.columns if c != "date"]:
        f[f"rain__{c}_d0"] = r[c]
        for k in range(1, min(L, 4)):
            f[f"rain__{c}_lag{k}"] = r[c].shift(k)
        for w in (3, 5, 7, 10):
            f[f"rain__{c}_sum{w}"] = r[c].rolling(w, min_periods=w).sum()
    return f


def build(target):
    L = S1["lags_days"]
    h = S1["horizon_days"]
    d = pd.read_csv(DAILY, parse_dates=["date"])
    tf, tg = station_frame(d, target, L)
    X = tf.add_prefix("own__")
    for up in S1["upstream_of"][target]:
        if up == target:
            continue
        uf, _ = station_frame(d, up, L)
        X = X.join(uf.add_prefix("up__"), how="left")

    rain = rainfall_features(p(S1["rainfall_csv"]), L)
    if rain is not None:
        X = X.join(rain, how="left")

    doy = X.index.dayofyear
    X["season__doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    X["season__doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    X["season__monsoon"] = X.index.month.isin([6, 7, 8, 9, 10]).astype(int)

    X["y_next_max"] = tg.daily_max_m.shift(-h).reindex(X.index)
    X["y_next_mean"] = tg.daily_mean_m.shift(-h).reindex(X.index)
    X["base_max"] = X[f"own__{slug(target)}_max_lag0"]
    X["dy_next_max"] = X["y_next_max"] - X["base_max"]
    X.index.name = "date"
    # unfiltered copy for operational forecasting (target may be unknown / missing)
    X[X.base_max.notna()].to_csv(p("06_features", f"stage1_{slug(target)}_forecast_inputs.csv"))
    X = X[X.y_next_max.notna() & X.base_max.notna()]

    out = p("06_features", f"stage1_{slug(target)}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    X.to_csv(out)
    groups = pd.Series([c.split("__")[0] for c in X.columns if "__" in c]).value_counts().to_dict()
    print(f"{target}: {len(X)} rows {X.index.min().date()}..{X.index.max().date()} | feature groups {groups} "
          f"| rainfall {'INCLUDED' if rain is not None else 'not yet available (CHIRPS pending)'}")
    return X


if __name__ == "__main__":
    for t in S1["targets"]:
        build(t)
