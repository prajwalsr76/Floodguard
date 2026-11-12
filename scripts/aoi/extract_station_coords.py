#!/usr/bin/env python3
"""
Read the Latitude / Longitude / datum columns of the core stations from the raw CWC
water-level CSVs and write them into config/floodguard.yaml (coords_verified: true).

Also writes 00_project/aoi/station_metadata.csv with every metadata column that is
constant per station (River, District, RL_of_zeroGauge, MeanSeaLevel, ...).
Run from the project root:  python scripts/aoi/extract_station_coords.py
"""
import sys
from pathlib import Path
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p, ROOT  # noqa: E402

CFG = load_config()
CORE = list(CFG["stations"].keys())


def main():
    files = sorted((ROOT / "01_raw" / "cwc_water_level").rglob("*.csv"))
    meta = []
    for f in files:
        # metadata is constant per station, so a sample of rows is enough per chunk
        for chunk in pd.read_csv(f, chunksize=200_000, low_memory=False):
            chunk["Station"] = chunk["Station"].astype(str).str.strip()
            c = chunk[chunk.Station.isin(CORE)]
            if len(c):
                meta.append(c.drop_duplicates("Station").assign(file=f.name))
    if not meta:
        raise RuntimeError("No core stations found in raw CSVs")
    m = pd.concat(meta).drop_duplicates("Station")
    out = p("00_project", "aoi"); out.mkdir(parents=True, exist_ok=True)
    keep = [c for c in m.columns if not c.startswith("River Water Level") and c not in ("_id", "SlNo", "Data Acquisition Time")]
    m[keep].to_csv(out / "station_metadata.csv", index=False)
    print(m[keep].to_string(index=False))

    lat = next(c for c in m.columns if c.lower().startswith("lat"))
    lon = next(c for c in m.columns if c.lower().startswith("lon"))
    cfg_path = ROOT / "config" / "floodguard.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    for _, r in m.iterrows():
        s = cfg["stations"][r.Station]
        s.update(lat=round(float(r[lat]), 6), lon=round(float(r[lon]), 6), coords_verified=True)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("Updated config/floodguard.yaml - now re-run scripts/aoi/make_aoi.py")


if __name__ == "__main__":
    main()
