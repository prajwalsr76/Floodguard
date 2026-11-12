#!/usr/bin/env python3
"""
Render ONE self-contained Earth Engine script (scripts/gee/floodguard_all_exports.js)
that needs no asset uploads: the AOI, rainfall zones, event list and the exact 100 m
modelling-grid transform are embedded. Every export is sized to stay < 10 MB.

Exports (Drive folder FloodGuard_GEE):
  fg_chirps_zone_daily.csv      date, zone, rain_mm   (CHIRPS v3 DAILY_SAT, 2014-2025)
  fg_dem_srtm30.tif             SRTM GL1 elevation, Int16, 30 m, EPSG:32645
  fg_worldcover2021.tif         ESA WorldCover v200 class, UInt8, 30 m (mode of 10 m)
  fg_jrc_water.tif              JRC GSW v1.4 occurrence (%), seasonality (months), UInt8, 30 m
  fg_events_a.tif / _b.tif ...  Sentinel-1 labels on the exact 100 m grid, UInt8, per event
                                bands <event>_flood, <event>_valid = % of 10 m pixels flooded / observed.
                                Chunks of 12 events (~8 MB each).
  fg_events_rain.tif            <event>_r1/_r3/_r7: CHIRPS totals ending the day before the S1
                                date, native 0.05 deg grid.
  fg_s1_event_dates.csv         event_id, s1_date, days_from_peak, orbit, pass, n_candidates
"""
import json, math, sys
from pathlib import Path
import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
RES = CFG["stage2"]["grid_resolution_m"]
CHUNK = 12


def geom_coords(g, tol):
    g = g.simplify(tol)
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    return [[[round(x, 5), round(y, 5)] for x, y in poly.exterior.coords] for poly in polys]


def main():
    aoi = gpd.read_file(p(CFG["stage2"]["aoi_geojson"]))
    zones = gpd.read_file(p("00_project", "aoi", "rain_zones.geojson"))
    aoi_m = aoi.to_crs(CFG["project_crs"])
    minx, miny, maxx, maxy = aoi_m.total_bounds
    minx, maxy = math.floor(minx / RES) * RES, math.ceil(maxy / RES) * RES
    maxx, miny = math.ceil(maxx / RES) * RES, math.floor(miny / RES) * RES
    grid = {"crsTransform": [RES, 0, minx, 0, -RES, maxy], "w": int((maxx - minx) / RES), "h": int((maxy - miny) / RES)}

    ev = pd.read_csv(p(CFG["stage2"]["events_csv"]))
    ev = ev[ev.sentinel1_available]
    events = [{"id": r.event_id, "peak": str(r.peak_date)} for r in ev.itertuples()]

    z = [{"zone": r.zone, "polys": geom_coords(r.geometry, 0.01 if r.zone != "z3_floodplain" else 0.001)}
         for r in zones.itertuples()]
    a = geom_coords(aoi.geometry.iloc[0], 0.001)
    tpl = (Path(__file__).parent / "floodguard_all_exports.template.js").read_text(encoding="utf-8")
    js = (tpl.replace("/*AOI*/[]", json.dumps(a)).replace("/*ZONES*/[]", json.dumps(z))
             .replace("/*EVENTS*/[]", json.dumps(events)).replace("/*GRID*/{}", json.dumps(grid))
             .replace("/*CHUNK*/12", str(CHUNK)))
    out = Path(__file__).parent / "floodguard_all_exports.js"
    out.write_text(js, encoding="utf-8")
    print(f"{out.name}: {len(js)/1024:.0f} KB, {len(events)} events in {math.ceil(len(events)/CHUNK)} chunks, "
          f"grid {grid['w']}x{grid['h']} @ {RES} m")


if __name__ == "__main__":
    main()
