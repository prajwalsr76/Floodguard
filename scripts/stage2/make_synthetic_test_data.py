#!/usr/bin/env python3
"""
PIPELINE TEST ONLY - writes SYNTHETIC rasters in the exact formats produced by the GEE
scripts so that Stage 2 can be exercised end-to-end before real data arrive.
NEVER use outputs produced from these files as results.

Writes into 01_raw/spatial/ of the CURRENT project root:
  dem_srtm30.tif, worldcover2021.tif, jrc_water.tif,
  events/flood_<event_id>.tif (+ floodguard_s1_event_dates.csv)
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
CRS = CFG["project_crs"]
RES = 30
rng = np.random.default_rng(42)


def write(path, arrs, names, tr, dtype="float32"):
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = arrs[0].shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=len(arrs), dtype=dtype, crs=CRS,
                       transform=tr, compress="deflate") as d:
        for i, (a, n) in enumerate(zip(arrs, names), 1):
            d.write(a.astype(dtype), i); d.set_band_description(i, n)


def main():
    aoi = gpd.read_file(p(CFG["stage2"]["aoi_geojson"])).to_crs(CRS)
    minx, miny, maxx, maxy = aoi.buffer(2000).total_bounds
    w, h = int((maxx - minx) / RES), int((maxy - miny) / RES)
    tr = from_origin(minx, maxy, RES, RES)
    yy, xx = np.mgrid[0:h, 0:w]
    X, Y = minx + xx * RES, maxy - yy * RES
    st = gpd.read_file(p("00_project", "aoi", "stations.geojson")).to_crs(CRS).set_index("station")
    pts = [st.loc[s].geometry for s in ["Naraj", "Kishan Nagar", "Tarpur"]]
    # main channel: polyline through the gauges + a southern branch (Kathajodi-like)
    def chan_dist(points, wiggle):
        d = np.full((h, w), np.inf)
        for a, b in zip(points[:-1], points[1:]):
            ax_, ay_, bx, by = a.x, a.y, b.x, b.y
            t = np.clip(((X - ax_) * (bx - ax_) + (Y - ay_) * (by - ay_)) / ((bx - ax_) ** 2 + (by - ay_) ** 2), 0, 1)
            px, py = ax_ + t * (bx - ax_), ay_ + t * (by - ay_) + wiggle * np.sin(X / 4000)
            d = np.minimum(d, np.hypot(X - px, Y - py))
        return d
    d1 = chan_dist(pts, 600)
    south = [pts[0], type(pts[0])(pts[0].x + 20000, pts[0].y - 9000), pts[2]]
    d2 = chan_dist(south, 400)
    dch = np.minimum(d1, d2)
    # longitudinal profile: gauge levels fall from ~Naraj datum to ~Tarpur datum
    frac = np.clip((X - pts[0].x) / (pts[2].x - pts[0].x), 0, 1)
    bed = 20.5 - frac * 15.5
    dem = bed + 1.2 + 4.0 * (1 - np.exp(-dch / 6000)) + ndimage.gaussian_filter(rng.normal(0, 1.2, (h, w)), 8) * 3
    river = dch < 250
    dem[river] = bed[river] - 1
    write(p("01_raw", "spatial", "dem_srtm30.tif"), [dem], ["elevation"], tr)

    occ = np.where(river, 85, np.where(dch < 500, 30, 0)) + 0 * dem
    seas = np.where(river, 12, 0)
    write(p("01_raw", "spatial", "jrc_water.tif"), [occ, seas], ["occurrence", "seasonality"], tr, "int16")

    lc = np.full((h, w), 40)
    built = ndimage.gaussian_filter(rng.random((h, w)), 15) > 0.53
    lc[built] = 50
    lc[ndimage.gaussian_filter(rng.random((h, w)), 10) > 0.55] = 10
    lc[river] = 80
    write(p("01_raw", "spatial", "worldcover2021.tif"), [lc], ["Map"], tr, "int16")

    ev = pd.read_csv(p(CFG["stage2"]["events_csv"]))
    ev = ev[ev.sentinel1_available]
    daily = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    near = np.argmin(np.stack([np.hypot(X - q.x, Y - q.y) for q in pts]), 0)
    rows = []
    for r in ev.itertuples():
        dte = pd.Timestamp(r.peak_date)
        day = daily[daily.date == dte].set_index("station").daily_max_m
        lv = [day.get(s, np.nan) for s in ["Naraj", "Kishan Nagar", "Tarpur"]]
        lv = [v if not np.isnan(v) else lv[0] - 8 * i for i, v in enumerate(lv)]
        # synthetic "truth": water surface interpolated along the corridor; rain adds ponding
        ws = np.choose(near, lv) - 3.2
        rain7 = 80 + 120 * rng.random() + ndimage.gaussian_filter(rng.normal(0, 20, (h, w)), 30)
        depth = ws - dem + 0.004 * (rain7 - 100) + ndimage.gaussian_filter(rng.normal(0, .5, (h, w)), 4)
        flood = (depth > 0) & (dch < 20000) & ~river
        valid = np.ones((h, w))
        valid[:, : int(w * 0.03)] = 0          # swath edge
        rows.append(dict(event_id=r.event_id, peak_date=r.peak_date, s1_date=r.peak_date, days_from_peak=0))
        write(p("01_raw", "spatial", "events", f"flood_{r.event_id}.tif"),
              [flood, river | flood, valid, rain7 / 5, rain7 / 2, rain7], ["flood", "water", "valid", "chirps_sum1",
                                                                          "chirps_sum3", "chirps_sum7"], tr)
    pd.DataFrame(rows).to_csv(p("01_raw", "spatial", "events", "floodguard_s1_event_dates.csv"), index=False)
    print(f"SYNTHETIC test data: DEM {h}x{w} @30 m, {len(rows)} event rasters")


if __name__ == "__main__":
    main()
