#!/usr/bin/env python3
"""Render 03_sentinel1_flood_masks.js from the template + flood_events.csv,
and package the AOI / rain-zone GeoJSONs as shapefile zips for GEE upload."""
import json, sys, zipfile
from pathlib import Path
import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
HERE = Path(__file__).resolve().parent


def main():
    ev = pd.read_csv(p(CFG["stage2"]["events_csv"]))
    ev = ev[ev.sentinel1_available]
    events = [{"event_id": r.event_id, "peak_date": str(r.peak_date), "kind": r.kind,
               "naraj_peak_m": float(r.naraj_peak_m)} for r in ev.itertuples()]
    tpl = (HERE / "03_sentinel1_flood_masks.template.js").read_text(encoding="utf-8")
    js = tpl.replace("/*EVENTS_JSON*/[]", json.dumps(events, indent=1))
    (HERE / "03_sentinel1_flood_masks.js").write_text(js, encoding="utf-8")
    print(f"03_sentinel1_flood_masks.js written with {len(events)} events "
          f"(GEE allows ~3000 queued tasks; {len(events)} exports is fine)")

    up = p("00_project", "aoi", "gee_upload"); up.mkdir(parents=True, exist_ok=True)
    for name in ["aoi_floodplain", "rain_zones", "stations"]:
        g = gpd.read_file(p("00_project", "aoi", f"{name}.geojson"))
        shp_dir = up / name; shp_dir.mkdir(exist_ok=True)
        g.to_file(shp_dir / f"{name}.shp")
        with zipfile.ZipFile(up / f"{name}.zip", "w") as z:
            for f in shp_dir.iterdir():
                z.write(f, f.name)
    print(f"GEE upload zips in {up}")


if __name__ == "__main__":
    main()
