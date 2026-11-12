#!/usr/bin/env python3
"""
Convert the downloads of floodguard_all_exports.js into the files the pipeline reads.

  python scripts/gee/import_gee_exports.py <folder with fg_* files>

Produces
  01_raw/spatial/dem_srtm30.tif, worldcover2021.tif, jrc_water.tif
  01_raw/spatial/events/flood_<event>.tif   bands: flood, water, valid, chirps_sum1, chirps_sum3, chirps_sum7
                                            (flood/valid as fractions, rain resampled to the 100 m grid)
  01_raw/spatial/events/floodguard_s1_event_dates.csv
  06_features/chirps_zone_daily_raw.csv
Bands are matched by ORDER (GEE GeoTIFFs do not reliably carry band names): events with a
scene (n_candidates > 0) in the order of the EVENTS list, CHUNK events per fg_events_<x>.tif,
2 bands each; fg_events_rain.tif has 3 bands per event.
"""
import shutil, sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
CHUNK = 12


def one(folder, stem):
    hits = sorted(folder.glob(f"{stem}*"))
    if not hits:
        raise FileNotFoundError(f"{stem}* not found in {folder}")
    return hits[0]


def main(folder):
    folder = Path(folder)
    sp = p("01_raw", "spatial"); ev_dir = sp / "events"; ev_dir.mkdir(parents=True, exist_ok=True)
    for stem, dst in [("fg_dem_srtm30", "dem_srtm30.tif"), ("fg_worldcover2021", "worldcover2021.tif"),
                      ("fg_jrc_water", "jrc_water.tif")]:
        shutil.copy(one(folder, stem), sp / dst)
    shutil.copy(one(folder, "fg_chirps_zone_daily"), p("06_features", "chirps_zone_daily_raw.csv"))

    meta = pd.read_csv(one(folder, "fg_s1_event_dates"))
    order = pd.read_csv(p(CFG["stage2"]["events_csv"]))
    order = order[order.sentinel1_available].event_id.tolist()
    meta = meta.set_index("event_id").loc[order].reset_index()
    usable = meta[meta.n_candidates > 0].reset_index(drop=True)
    cols = [c for c in ["event_id", "peak_date", "s1_date", "days_from_peak", "relative_orbit", "pass",
                        "n_candidates"] if c in usable.columns]
    usable[cols].to_csv(ev_dir / "floodguard_s1_event_dates.csv", index=False)

    rain_src = rasterio.open(one(folder, "fg_events_rain"))
    assert rain_src.count == 3 * len(usable), f"rain bands {rain_src.count} != 3 x {len(usable)} events"
    chunks = sorted(folder.glob("fg_events_[a-z]*.tif"))
    chunks = [c for c in chunks if "rain" not in c.name]
    for ci, cf in enumerate(chunks):
        with rasterio.open(cf) as src:
            part = usable.iloc[ci * CHUNK:(ci + 1) * CHUNK]
            assert src.count == 2 * len(part), f"{cf.name}: {src.count} bands for {len(part)} events"
            prof = src.profile
            for k, r in enumerate(part.itertuples()):
                flood = src.read(2 * k + 1).astype("float32") / 100
                valid = src.read(2 * k + 2).astype("float32") / 100
                gi = usable.index[usable.event_id == r.event_id][0]
                rain = []
                for b in range(3):
                    out = np.zeros(flood.shape, "float32")
                    reproject(rasterio.band(rain_src, 3 * gi + b + 1), out, dst_transform=src.transform,
                              dst_crs=src.crs, resampling=Resampling.bilinear)
                    rain.append(out)
                prof.update(count=6, dtype="float32", compress="deflate", nodata=None)
                with rasterio.open(ev_dir / f"flood_{r.event_id}.tif", "w", **prof) as d:
                    for i, (a, n) in enumerate(zip([flood, flood, valid] + rain,
                                                   ["flood", "water", "valid", "chirps_sum1", "chirps_sum3",
                                                    "chirps_sum7"]), 1):
                        d.write(a, i); d.set_band_description(i, n)
    print(f"Imported static layers, CHIRPS zone CSV and {len(usable)} event rasters "
          f"({len(meta) - len(usable)} events had no Sentinel-1 scene within the window)")
    print(usable[["event_id", "s1_date", "days_from_peak"]].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1])
