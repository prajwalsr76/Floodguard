#!/usr/bin/env python3
"""
Fetch the live inputs for FloodGuard (run on a computer with normal internet access).

  * CWC Flood Forecasting System (ffs.india-water.gov.in), hourly stage 'HHS' for
    Tikarpara 034-MDBURLA, Naraj 036-MDBURLA, Kishan Nagar 046-MDBURLA, Tarpur 047-MDBURLA
    -> 01_raw/live/cwc_ffs_daily.csv (daily mean/max/min since 2025) and cwc_ffs_hourly.csv (last 7 days)
  * Open-Meteo daily precipitation averaged over sample points in the 4 rain zones
    -> openmeteo_zone_daily_hist.csv (2022-01-01..today) and openmeteo_zone_daily_fc.csv (past 14 d + 7 d)

The FFS API decodes the 'specification' parameter twice, so it is URL-encoded twice.
Requires: requests, pandas, geopandas, shapely.
"""
import json, sys, time
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import p  # noqa: E402

LIVE = p("01_raw", "live"); LIVE.mkdir(parents=True, exist_ok=True)
FFS = "https://ffs.india-water.gov.in/iam/api/new-entry-data/specification/sorted-page"
CODES = {"Tikarpara": "034-MDBURLA", "Naraj": "036-MDBURLA", "Kishan Nagar": "046-MDBURLA", "Tarpur": "047-MDBURLA"}
HDR = {"User-Agent": "FloodGuard research prototype (IIIT Pune)", "Accept": "application/json"}


def r2(x):
    """Round to 0.01 half-up (the browser engine uses the identical rule)."""
    return np.floor(x * 100 + 0.5) / 100


def enc(obj):
    s = json.dumps(obj, separators=(",", ":"))
    return quote(quote(s, safe="-_.!~*'()"), safe="-_.!~*'()")


def ffs_series(code, since="2025-01-01", page_size=1000, max_pages=15):
    spec = {"where": {"where": {"expression": {"valueIsRelationField": False, "fieldName": "id.stationCode", "operator": "eq", "value": code}},
                      "and": {"expression": {"valueIsRelationField": False, "fieldName": "id.datatypeCode", "operator": "eq", "value": "HHS"}}},
            "and": {"expression": {"valueIsRelationField": False, "fieldName": "dataValue", "operator": "null", "value": "false"}}}
    sort = {"sortOrderDtos": [{"sortDirection": "DESC", "field": "id.dataTime"}]}
    rows = []
    for pg in range(max_pages):
        url = f"{FFS}?sort-criteria={enc(sort)}&page-number={pg}&page-size={page_size}&specification={enc(spec)}"
        r = requests.get(url, headers=HDR, timeout=60); r.raise_for_status()
        j = r.json()
        if not j:
            break
        rows += [(d["id"]["dataTime"][:16], float(d["dataValue"])) for d in j]
        if rows[-1][0] < since:
            break
    s = pd.DataFrame(rows, columns=["time", "level_m"])
    s["time"] = pd.to_datetime(s.time)
    return s.drop_duplicates("time").sort_values("time")


def zone_points():
    import geopandas as gpd
    from shapely.geometry import Point
    z = gpd.read_file(p("00_project", "aoi", "rain_zones.geojson")).to_crs(4326)
    out = {}
    for _, r in z.iterrows():
        g = r.geometry; x0, y0, x1, y1 = g.bounds; step = max(0.1, np.sqrt(g.area / 12))
        out[r["zone"]] = [(round(y, 3), round(x, 3)) for x in np.arange(x0 + step / 2, x1, step)
                          for y in np.arange(y0 + step / 2, y1, step) if g.contains(Point(x, y))]
    return out


def om(url_base, pts, **params):
    q = {"latitude": ",".join(str(a) for a, _ in pts), "longitude": ",".join(str(b) for _, b in pts),
         "daily": "precipitation_sum", "timezone": "Asia/Kolkata", **params}
    r = requests.get(url_base, params=q, headers=HDR, timeout=120); r.raise_for_status()
    j = r.json(); j = j if isinstance(j, list) else [j]
    t = pd.to_datetime(j[0]["daily"]["time"])
    v = np.nanmean(np.array([[np.nan if x is None else x for x in a["daily"]["precipitation_sum"]] for a in j], float), axis=0)
    return pd.Series(v, index=t)


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    daily, hourly = [], []
    for st, code in CODES.items():
        s = ffs_series(code)
        print(f"{st}: {len(s)} readings {s.time.min()} .. {s.time.max()}")
        g = s.groupby(s.time.dt.normalize()).level_m
        daily.append(pd.DataFrame({"station": st, "date": g.mean().index, "daily_mean_m": r2(g.mean()).values,
                                   "daily_max_m": g.max().values, "daily_min_m": g.min().values, "n_obs": g.size().values}))
        h = s[s.time >= s.time.max() - pd.Timedelta(days=7)].assign(station=st)
        hourly.append(h[["station", "time", "level_m"]])
    pd.concat(daily).to_csv(LIVE / "cwc_ffs_daily.csv", index=False)
    pd.concat(hourly).to_csv(LIVE / "cwc_ffs_hourly.csv", index=False)
    (LIVE / "cwc_meta.json").write_text(json.dumps({"fetched_utc": now, "source": "CWC Flood Forecasting System ffs.india-water.gov.in, datatype HHS", "codes": CODES}, indent=1))

    Z = zone_points()
    hist, fc = {}, {}
    for z, pts in Z.items():
        hist[z] = om("https://historical-forecast-api.open-meteo.com/v1/forecast", pts, start_date="2022-01-01", end_date=str(date.today()))
        time.sleep(1.5)
        fc[z] = om("https://api.open-meteo.com/v1/forecast", pts, past_days=14, forecast_days=7)
        time.sleep(1.5)
        print(f"rain {z}: {len(pts)} points, last 3 days {hist[z].iloc[-3:].round(1).tolist()}")
    r2(pd.DataFrame(hist)).rename_axis("date").to_csv(LIVE / "openmeteo_zone_daily_hist.csv")
    r2(pd.DataFrame(fc)).rename_axis("date").to_csv(LIVE / "openmeteo_zone_daily_fc.csv")
    (LIVE / "openmeteo_meta.json").write_text(json.dumps({"fetched_utc": now}))
    print("live inputs written to", LIVE)


if __name__ == "__main__":
    main()
