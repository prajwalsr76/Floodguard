#!/usr/bin/env python3
"""
FloodGuard - QC v2 on the already-processed DAILY manual water-level table.

Why this exists
---------------
The v1 pipeline (audit -> review -> process_water_level.py) left a few corrupted
single readings in the daily table, e.g. Tarpur 356.32 m (2020-09-23) and 71.1 m
(2019-07-12) where the station normally sits at 5-12 m, and low-side typos such as
Naraj 2.28 m (2019-08-04). It also computed daily_change across multi-day gaps.

This script fixes the daily table without needing the raw sub-daily data:
  1. Isolated days (no neighbour within N days) are dropped.
  2. A daily max/min outside the station's plausible range, or an intraday spike
     (range > R m and extreme > S m from the mean, except exempt stations), is
     treated as ONE corrupted reading: the stat is set to NaN and the daily mean
     is corrected exactly as mean' = (mean*n - bad)/(n-1).
  3. daily_change / rise_rate are recomputed only between consecutive calendar days.
  4. Every action is logged.

The permanent fix is in process_water_level.py (v2), which applies the same rules
at observation level. Use this script when only the daily table is available.

Input : 05_processed/water_level/manual/daily_water_level_cleaned.csv
Output: 05_processed/water_level/manual/daily_water_level_qc2.csv
        05_processed/water_level/manual/qc2_log.csv
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()["qc"]
IN = p("05_processed", "water_level", "manual", "daily_water_level_cleaned.csv")
OUT = p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv")
LOG = p("05_processed", "water_level", "manual", "qc2_log.csv")


def station_bounds(g):
    lo = min(g.daily_min_m.quantile(0.001), g.daily_mean_m.quantile(0.001))
    hi = max(g.daily_max_m.quantile(0.999), g.daily_mean_m.quantile(0.999))
    # Quantiles of a contaminated series are themselves robust at these levels
    # because only a handful of days are corrupted.
    return lo - CFG["bound_margin_m"], hi + CFG["bound_margin_m"]


def drop_one_reading(row, stat):
    bad = row[stat]
    n = row["n_observations"]
    if n > 1:
        row["daily_mean_m"] = (row["daily_mean_m"] * n - bad) / (n - 1)
        row["n_observations"] = n - 1
    else:
        row["daily_mean_m"] = np.nan
    row[stat] = np.nan
    return row


def qc_station(g, logs):
    station = g.station.iloc[0]
    g = g.sort_values("date").reset_index(drop=True)

    # 1. isolated days
    gap_prev = g.date.diff().dt.days
    gap_next = (-g.date.diff(-1)).dt.days
    iso = (gap_prev.fillna(1e9) > CFG["isolated_day_gap_days"]) & \
          (gap_next.fillna(1e9) > CFG["isolated_day_gap_days"])
    for _, r in g[iso].iterrows():
        logs.append(dict(station=station, date=r.date.date(), action="drop_isolated_day",
                         detail=f"mean={r.daily_mean_m:.2f}, n={r.n_observations}"))
    g = g[~iso].reset_index(drop=True)

    lo, hi = station_bounds(g)
    exempt = station in CFG["spike_rule_exempt"]

    for i in g.index:
        r = g.loc[i].copy()
        for stat in ["daily_max_m", "daily_min_m"]:
            v = r[stat]
            if pd.isna(v):
                continue
            # A value outside the historical range is only treated as corrupt when it
            # is also inconsistent with the same day's mean - a genuine record flood
            # peak (e.g. Tikarpara 72.34 m, 15-08-2022, daily mean 71.86 m) is kept.
            far_from_mean = abs(v - r.daily_mean_m) > CFG["intraday_spike_m"]
            out_of_range = ((v < lo) or (v > hi)) and far_from_mean
            spike = (not exempt
                     and (r.daily_max_m - r.daily_min_m) > CFG["intraday_range_m"]
                     and abs(v - r.daily_mean_m) > CFG["intraday_spike_m"])
            if out_of_range or spike:
                old_mean = r.daily_mean_m
                r = drop_one_reading(r, stat)
                logs.append(dict(station=station, date=r.date.date(),
                                 action=f"remove_corrupt_{stat.split('_')[1]}",
                                 detail=f"value={v:.2f} ({'out_of_range' if out_of_range else 'intraday_spike'}; "
                                        f"bounds=[{lo:.2f},{hi:.2f}]); mean {old_mean:.2f}->{r.daily_mean_m:.2f}"))
        g.loc[i] = r

    # A corrected mean can still be implausible if >1 reading was bad.
    still_bad = (g.daily_mean_m < lo) | (g.daily_mean_m > hi)
    for _, r in g[still_bad].iterrows():
        logs.append(dict(station=station, date=r.date.date(), action="null_implausible_mean",
                         detail=f"mean={r.daily_mean_m:.2f}"))
    g.loc[still_bad, ["daily_mean_m", "daily_max_m", "daily_min_m"]] = np.nan

    # 3. gap-aware change
    consecutive = g.date.diff().dt.days.eq(1)
    g["daily_change_m"] = np.where(consecutive, g.daily_mean_m.diff(), np.nan)
    g["daily_max_change_m"] = np.where(consecutive, g.daily_max_m.diff(), np.nan)
    g["rise_rate_m_per_day"] = g["daily_change_m"]
    return g


def regional_dip(out, logs, stations=("Naraj", "Kishan Nagar", "Tarpur"), dip_m=0.8, min_stations=2):
    """A one-day V-shaped drop (> dip_m below BOTH neighbours) that occurs at >= min_stations
    corridor gauges on the same day, mid-flood, is not hydraulically plausible (the river
    cannot fall and recover 1.5 m in 24 h along 40 km while Tikarpara stays high). This
    pattern points to mis-dated / copied records at source -> set to NaN and log.
    Found once in 2014-2025: 2022-08-16."""
    w = out.pivot(index="date", columns="station", values="daily_max_m").asfreq("D")
    dip = pd.DataFrame({s: (w[s].shift(1) - w[s] > dip_m) & (w[s].shift(-1) - w[s] > dip_m)
                        for s in stations if s in w})
    bad_days = dip.index[dip.sum(axis=1) >= min_stations]
    for day in bad_days:
        for s in dip.columns[dip.loc[day]]:
            m = (out.station == s) & (out.date == day)
            logs.append(dict(station=s, date=day.date(), action="null_regional_coherent_dip",
                             detail=f"max={out.loc[m, 'daily_max_m'].iloc[0]:.2f} m; same-day V-dip at "
                                    f"{int(dip.loc[day].sum())} corridor gauges"))
            out.loc[m, ["daily_mean_m", "daily_max_m", "daily_min_m"]] = np.nan
    return out


def recompute_changes(out):
    parts = []
    for _, g in out.groupby("station"):
        g = g.sort_values("date").copy()
        consecutive = g.date.diff().dt.days.eq(1)
        g["daily_change_m"] = np.where(consecutive, g.daily_mean_m.diff(), np.nan)
        g["daily_max_change_m"] = np.where(consecutive, g.daily_max_m.diff(), np.nan)
        g["rise_rate_m_per_day"] = g["daily_change_m"]
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def main():
    d = pd.read_csv(IN, parse_dates=["date"])
    d["station"] = d["station"].str.strip()
    logs = []
    out = pd.concat([qc_station(g, logs) for _, g in d.groupby("station")], ignore_index=True)
    out = recompute_changes(regional_dip(out, logs))
    out = out.sort_values(["station", "date"])
    out.to_csv(OUT, index=False)
    log = pd.DataFrame(logs)
    log.to_csv(LOG, index=False)
    print(f"Wrote {OUT} ({len(out)} rows); {len(log)} QC actions")
    print(log.to_string(index=False))


if __name__ == "__main__":
    main()
