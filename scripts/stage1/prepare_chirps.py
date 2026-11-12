#!/usr/bin/env python3
"""
Convert the GEE 01 export (long: date, zone, rain_mm) to the wide daily table used by
build_features.py, and report agreement with the CWC rain gauges inside the AOI
(Cuttack, Naraj, Tarapur - 02_reference) as an independent sanity check.

In : 06_features/chirps_zone_daily_raw.csv   (download from Drive/FloodGuard_GEE)
Out: 06_features/chirps_daily_subcatchments.csv   columns: date, z0_..., z1_..., z2_..., z3_...
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p, ROOT  # noqa: E402

CFG = load_config()


def main():
    raw = pd.read_csv(p("06_features", "chirps_zone_daily_raw.csv"), parse_dates=["date"])
    w = raw.pivot_table(index="date", columns="zone", values="rain_mm").sort_index()
    w = w.reindex(pd.date_range(w.index.min(), w.index.max(), freq="D"))
    missing = w.isna().sum()
    w.index.name = "date"
    w.round(3).to_csv(p(CFG["stage1"]["rainfall_csv"]))
    print(f"{len(w)} days, zones {list(w.columns)}; missing days per zone: {missing.to_dict()}")

    ref = list((ROOT / "02_reference" / "rainfall").glob("*Manual Daily*.csv"))
    if ref and "z3_floodplain" in w:
        g = pd.concat(pd.read_csv(f) for f in ref)
        g = g[g.Station.str.strip().isin(["Cuttack", "Naraj", "Tarapur"])]
        g["date"] = pd.to_datetime(g["Data Acquisition Time"], dayfirst=True, errors="coerce").dt.normalize()
        # CWC 08:30 reading = rain of the previous 24 h -> attribute to previous day
        g["date"] = g["date"] - pd.Timedelta(days=1)
        gm = g.groupby("date")["Manual Daily Rainfall (mm)"].mean()
        j = pd.concat([gm.rename("gauge"), w["z3_floodplain"].rename("chirps")], axis=1).dropna()
        if len(j) > 10:
            print(f"AOI gauge vs CHIRPS z3 (n={len(j)} days): r={j.corr().iloc[0, 1]:.2f}, "
                  f"gauge mean {j.gauge.mean():.1f} mm, CHIRPS mean {j.chirps.mean():.1f} mm")


if __name__ == "__main__":
    main()
