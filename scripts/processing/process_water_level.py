#!/usr/bin/env python3
"""
FloodGuard - water-level cleaning + daily processing  (v2)

Changes from v1
---------------
* Adds an observation-level plausibility pass AFTER the reviewed decisions:
  a reading is removed when it is outside the station's plausible range AND
  far from the median of its own day (catches decimal-point typos such as
  Tarpur 356.32 m / 71.1 m, Naraj 2.28 m, Tikarpara 5.10 m while keeping
  genuine flood peaks). Removed readings are logged.
* daily_change / rise_rate are computed only between consecutive calendar days
  (v1 differenced across gaps of up to 862 days).
* Adds wl_0800_m: the reading nearest 08:00 (CWC's standard gauge time) so the
  daily feature does not depend on how many readings were taken that day
  (3/day in the dry season vs 24/day in the monsoon).
* Isolated days (no other observation within +/-7 days) are dropped.
* Station name spelled consistently as "Tikarpara" (as in the CWC files).

Inputs
------
 01_raw/cwc_water_level/{manual,telemetry}/*.csv
 00_project/audits/water_level/review_decisions_core_final_corrected.csv

Outputs (05_processed/water_level/)
-------
 {manual,telemetry}/observations_cleaned.csv
 {manual,telemetry}/daily_water_level_cleaned.csv
 processed_station_summary.csv
 cleaning_log.csv
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, ROOT  # noqa: E402

CFG = load_config()
QC = CFG["qc"]
AUD = ROOT / "00_project" / "audits" / "water_level"
OUT = ROOT / "05_processed" / "water_level"
CORE = set(CFG["stations"].keys())          # Tikarpara, Naraj, Kishan Nagar, Tarpur
TIME = "Data Acquisition Time"
REVIEW = AUD / "review_decisions_core_final_corrected.csv"


def find_raw_root():
    for q in [ROOT / "01_raw" / "cwc_water_level", ROOT / "01_raw" / "waterlevel"]:
        if (q / "manual").exists() or (q / "telemetry").exists():
            return q
    raise FileNotFoundError("Could not find 01_raw/cwc_water_level.")


def parse_dt(s):
    s = s.astype("string").str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"]:
        m = out.isna()
        out.loc[m] = pd.to_datetime(s[m], format=fmt, errors="coerce")
    return out


def load_review():
    d = pd.read_csv(REVIEW, low_memory=False)
    d.columns = [c.strip() for c in d.columns]
    for c in ["file", "station"]:
        d[c] = d[c].astype("string").str.strip()
    d["station"] = d["station"].replace({"Tikarapara": "Tikarpara"})
    d["timestamp"] = pd.to_datetime(d["timestamp"], errors="coerce")
    d["decision"] = d["decision"].astype("string").str.upper().str.strip()
    for c in ["water_level", "decision_water_level_to_remove", "decision_keep_water_level"]:
        d[c] = pd.to_numeric(d.get(c, np.nan), errors="coerce")
    bad = d[~d.decision.isin(["REMOVE", "KEEP", "REMOVE_DUPLICATE"])]
    if len(bad):
        raise RuntimeError(f"{len(bad)} review decisions are unresolved/invalid.")
    return d


def load_raw(raw_root):
    parts = []
    files = sorted(list((raw_root / "manual").glob("*.csv")) + list((raw_root / "telemetry").glob("*.csv")))
    for f in files:
        d = pd.read_csv(f, low_memory=False)
        wl = [c for c in d.columns if "River Water Level" in c and "(meter)" in c]
        if "Station" not in d.columns or TIME not in d.columns or not wl:
            continue
        x = pd.DataFrame({
            "station": d["Station"].astype("string").str.strip(),
            "timestamp": parse_dt(d[TIME]),
            "water_level_m": pd.to_numeric(d[wl[0]], errors="coerce"),
        })
        x["file"] = f.name
        x["mode"] = "manual" if "manual" in [z.lower() for z in f.parts] else "telemetry"
        x = x.dropna(subset=["station", "timestamp", "water_level_m"])
        parts.append(x[x.station.isin(CORE)])
    if not parts:
        raise RuntimeError("No usable core-station observations found.")
    return pd.concat(parts, ignore_index=True)


def apply_review(g, source_file, lookup, logs):
    """Unchanged v1 logic: dedup + reviewed anomaly/conflict decisions."""
    g = g.copy()
    before = len(g)
    g = g.drop_duplicates()
    if before - len(g):
        logs.append({"file": source_file, "operation": "remove_exact_duplicate_rows", "count": before - len(g)})
    before = len(g)
    g = g.drop_duplicates(["station", "timestamp", "water_level_m"], keep="first")
    if before - len(g):
        logs.append({"file": source_file, "operation": "collapse_same_value_timestamp_duplicates",
                     "count": before - len(g)})

    for key, rows in lookup.items():
        if key[0] != source_file:
            continue
        station, timestamp = key[1], key[2]
        mask = g.station.eq(station) & g.timestamp.eq(timestamp)
        if not mask.any():
            continue
        for r in rows:
            issue = str(r["issue_type"])
            decision = str(r["decision"]).upper().strip()
            if issue == "anomaly_candidate" and decision == "REMOVE" and pd.notna(r["water_level"]):
                rm = mask & np.isclose(g.water_level_m.astype(float), float(r["water_level"]), rtol=0, atol=1e-9)
                if rm.any():
                    g = g.loc[~rm].copy()
                    logs.append({"file": source_file, "operation": "remove_reviewed_anomaly", "station": station,
                                 "timestamp": timestamp, "water_level_m": float(r["water_level"]),
                                 "count": int(rm.sum())})
            elif issue == "conflicting_duplicate_timestamp":
                keep, remove = r.get("decision_keep_water_level"), r.get("decision_water_level_to_remove")
                if pd.notna(keep):
                    rm = mask & ~np.isclose(g.water_level_m.astype(float), float(keep), rtol=0, atol=1e-9)
                elif pd.notna(remove):
                    rm = mask & np.isclose(g.water_level_m.astype(float), float(remove), rtol=0, atol=1e-9)
                else:
                    continue
                if rm.any():
                    g = g.loc[~rm].copy()
                    logs.append({"file": source_file, "operation": "resolve_conflict", "station": station,
                                 "timestamp": timestamp, "count": int(rm.sum())})

    dup = g[g.duplicated(["station", "timestamp"], keep=False)]
    if len(dup):
        raise RuntimeError(f"Unresolved conflicting timestamps remain in {source_file}: {len(dup)} rows")
    return g


def plausibility_pass(d, logs):
    """v2: remove readings that are implausible for the station AND inconsistent with their own day."""
    keep = np.ones(len(d), dtype=bool)
    d = d.reset_index(drop=True)
    for (mode, station), g in d.groupby(["mode", "station"]):
        lo = g.water_level_m.quantile(0.001) - QC["bound_margin_m"]
        hi = g.water_level_m.quantile(0.999) + QC["bound_margin_m"]
        day_median = g.groupby(g.timestamp.dt.normalize()).water_level_m.transform("median")
        far = (g.water_level_m - day_median).abs() > QC["intraday_spike_m"]
        bad = ((g.water_level_m < lo) | (g.water_level_m > hi)) & far
        if station not in QC["spike_rule_exempt"]:
            # within-day spike even if inside the historical range
            bad |= far & ((g.water_level_m - day_median).abs() > 2 * QC["intraday_spike_m"])
        for i in g.index[bad]:
            logs.append({"file": d.at[i, "file"], "operation": "remove_implausible_reading_v2",
                         "station": station, "timestamp": d.at[i, "timestamp"],
                         "water_level_m": d.at[i, "water_level_m"], "count": 1})
        keep[g.index[bad]] = False
    return d[keep].copy()


def drop_isolated_days(d, logs):
    out = []
    for (mode, station), g in d.groupby(["mode", "station"]):
        days = pd.Series(sorted(g.timestamp.dt.normalize().unique()))
        gp, gn = days.diff().dt.days.fillna(1e9), (-days.diff(-1)).dt.days.fillna(1e9)
        iso = set(days[(gp > QC["isolated_day_gap_days"]) & (gn > QC["isolated_day_gap_days"])])
        m = g.timestamp.dt.normalize().isin(iso)
        for day in iso:
            logs.append({"operation": "drop_isolated_day", "station": station, "timestamp": day,
                         "count": int((g.timestamp.dt.normalize() == day).sum())})
        out.append(g[~m])
    return pd.concat(out, ignore_index=True)


def daily_table(d):
    d = d.sort_values(["station", "timestamp"]).copy()
    d["date"] = d.timestamp.dt.normalize()
    d["dist_0800_h"] = (d.timestamp - (d.date + pd.Timedelta(hours=8))).abs().dt.total_seconds() / 3600
    daily = d.groupby(["date", "station"], as_index=False).agg(
        daily_mean_m=("water_level_m", "mean"),
        daily_max_m=("water_level_m", "max"),
        daily_min_m=("water_level_m", "min"),
        n_observations=("water_level_m", "size"))
    w8 = (d.loc[d.groupby(["date", "station"]).dist_0800_h.idxmin()]
          .loc[lambda x: x.dist_0800_h <= 2, ["date", "station", "water_level_m"]]
          .rename(columns={"water_level_m": "wl_0800_m"}))
    daily = daily.merge(w8, on=["date", "station"], how="left").sort_values(["station", "date"])
    parts = []
    for _, g in daily.groupby("station"):
        consecutive = g.date.diff().dt.days.eq(1)
        g = g.copy()
        g["daily_change_m"] = np.where(consecutive, g.daily_mean_m.diff(), np.nan)
        g["daily_max_change_m"] = np.where(consecutive, g.daily_max_m.diff(), np.nan)
        g["rise_rate_m_per_day"] = g["daily_change_m"]
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def main():
    raw_root = find_raw_root()
    review = load_review()
    raw = load_raw(raw_root)
    lookup = {}
    for _, r in review.iterrows():
        lookup.setdefault((r.file, r.station, r.timestamp), []).append(r)

    logs = []
    cleaned = pd.concat([apply_review(g, f, lookup, logs) for f, g in raw.groupby("file", sort=True)],
                        ignore_index=True)
    cleaned = plausibility_pass(cleaned, logs)
    cleaned = drop_isolated_days(cleaned, logs)

    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    for mode in ["manual", "telemetry"]:
        d = cleaned[cleaned["mode"].eq(mode)].sort_values(["station", "timestamp"])
        md = OUT / mode
        md.mkdir(parents=True, exist_ok=True)
        d.to_csv(md / "observations_cleaned.csv", index=False)
        if d.empty:
            continue
        daily = daily_table(d)
        daily.to_csv(md / "daily_water_level_cleaned.csv", index=False)
        for station, g in daily.groupby("station"):
            summary.append({"mode": mode, "station": station, "first_date": g.date.min().date(),
                            "last_date": g.date.max().date(), "observed_days": len(g),
                            "mean_observations_per_day": g.n_observations.mean(),
                            "days_with_0800_reading": int(g.wl_0800_m.notna().sum())})
    pd.DataFrame(summary).to_csv(OUT / "processed_station_summary.csv", index=False)
    pd.DataFrame(logs).to_csv(OUT / "cleaning_log.csv", index=False)
    print(pd.DataFrame(summary).to_string(index=False))
    v2 = [l for l in logs if l.get("operation") in ("remove_implausible_reading_v2", "drop_isolated_day")]
    print(f"\nv2 plausibility removals / isolated days: {len(v2)}")


if __name__ == "__main__":
    main()
