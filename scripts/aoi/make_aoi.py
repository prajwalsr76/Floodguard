#!/usr/bin/env python3
"""
FloodGuard - build the study-area geometries used by every spatial step.

Outputs (00_project/aoi/, all EPSG:4326 GeoJSON so they upload straight to GEE):
  aoi_floodplain.geojson  Stage-2 AOI: Lower Mahanadi floodplain corridor
                          Naraj -> Kishan Nagar -> Tarpur, buffered, clipped to the
                          CWC Mahanadi basin polygon.
  mahanadi_basin.geojson  Simplified CWC Mahanadi basin (for CHIRPS aggregation).
  rain_zones.geojson      Rainfall sub-catchments for Stage-1 features:
                            z0_above_hirakud  (west of Hirakud dam, 83.87 E)
                            z1_hirakud_tikarpara
                            z2_tikarpara_naraj
                            z3_floodplain     (= AOI)
                          Zones are longitude bands of the CWC basin - an explicit
                          approximation. Replace with HydroSHEDS/DEM-delineated
                          sub-basins when time permits (see GEE script 01).
  stations.geojson        The four core stations.

Station coordinates come from config/floodguard.yaml. Run
scripts/aoi/extract_station_coords.py first to replace the approximate values
with the Latitude/Longitude recorded in the raw CWC CSVs.
"""
import sys
from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
BUFFER_M = 15000          # floodplain half-width around the gauge corridor
HIRAKUD_LON = 83.87


def find_basin():
    for c in [p("01_raw", "river", "basin", "basin_cwc.GeoJSON"), p("01_raw", "river", "basin_cwc.GeoJSON")]:
        if c.exists():
            return c
    raise FileNotFoundError("Unzip 01_raw/river/basin_cwc_geojson.zip into 01_raw/river/basin/")


def main():
    out = p("00_project", "aoi"); out.mkdir(parents=True, exist_ok=True)
    st = CFG["stations"]
    pts = gpd.GeoDataFrame(
        [{"station": k, "role": v["role"], "coords_verified": v["coords_verified"]} for k, v in st.items()],
        geometry=[Point(v["lon"], v["lat"]) for v in st.values()], crs="EPSG:4326")
    pts.to_file(out / "stations.geojson", driver="GeoJSON")

    basin = gpd.read_file(find_basin())
    name_col = "ba_name" if "ba_name" in basin.columns else basin.columns[0]
    mah = basin[basin[name_col].astype(str).str.strip().str.lower().eq("mahanadi")].to_crs("EPSG:4326")
    mah = gpd.GeoDataFrame({"name": ["Mahanadi"]}, geometry=[mah.union_all().simplify(0.005)], crs="EPSG:4326")
    mah.to_file(out / "mahanadi_basin.geojson", driver="GeoJSON")

    corridor = [st[s] for s in ["Naraj", "Kishan Nagar", "Tarpur"]]
    line = gpd.GeoSeries([LineString([(c["lon"], c["lat"]) for c in corridor])], crs="EPSG:4326")
    aoi = line.to_crs(CFG["project_crs"]).buffer(BUFFER_M, cap_style=1).to_crs("EPSG:4326")
    aoi = gpd.GeoDataFrame({"name": ["Lower Mahanadi floodplain corridor"]},
                           geometry=[aoi.iloc[0].intersection(mah.geometry.iloc[0])], crs="EPSG:4326")
    aoi["area_km2"] = aoi.to_crs(CFG["project_crs"]).area / 1e6
    aoi.to_file(out / "aoi_floodplain.geojson", driver="GeoJSON")

    b = mah.geometry.iloc[0]
    minx, miny, maxx, maxy = b.bounds
    tik, nar = st["Tikarpara"]["lon"], st["Naraj"]["lon"]
    zones = [("z0_above_hirakud", box(minx, miny, HIRAKUD_LON, maxy).intersection(b)),
             ("z1_hirakud_tikarpara", box(HIRAKUD_LON, miny, tik, maxy).intersection(b)),
             ("z2_tikarpara_naraj", box(tik, miny, nar, maxy).intersection(b)),
             ("z3_floodplain", aoi.geometry.iloc[0])]
    z = gpd.GeoDataFrame({"zone": [n for n, _ in zones]}, geometry=[g for _, g in zones], crs="EPSG:4326")
    z["area_km2"] = z.to_crs(CFG["project_crs"]).area / 1e6
    z.to_file(out / "rain_zones.geojson", driver="GeoJSON")
    print(z.drop(columns="geometry").round(0).to_string(index=False))
    print(f"AOI area: {aoi.area_km2.iloc[0]:.0f} km2, bounds {tuple(round(v, 3) for v in aoi.total_bounds)}")
    if not all(v["coords_verified"] for v in st.values()):
        print("WARNING: station coordinates are approximate - run extract_station_coords.py and re-run.")


if __name__ == "__main__":
    main()
