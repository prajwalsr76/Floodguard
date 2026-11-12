#!/usr/bin/env python3
"""
FloodGuard Stage 2 - build the common 100 m modelling grid and static features.

Inputs (01_raw/spatial/, from GEE script 02):
  dem_srtm30.tif, worldcover2021.tif, jrc_water.tif (band1 occurrence %, band2 seasonality)
  00_project/aoi/aoi_floodplain.geojson, stations.geojson

Static features per 100 m cell (EPSG:32645):
  elev_m           mean SRTM elevation
  slope_deg        mean slope (computed on the 30 m DEM, then averaged)
  river            1 if JRC occurrence >= 50 % covers >= 30 % of the cell (channel network)
  dist_river_m     Euclidean distance to the nearest river cell
  har_m            height above river: elev - elev of the nearest river cell (HAND-like)
  lc_<class>_frac  WorldCover class fractions (tree, shrub, grass, crop, built, bare, water, wetland)
  lc_major         majority WorldCover class
  near_station     index of the nearest corridor gauge (0 Naraj, 1 Kishan Nagar, 2 Tarpur)
  in_aoi           1 inside the AOI polygon
Output: 06_features/stage2_grid.tif (multi-band) + stage2_grid_meta.json + stage2_cells.parquet
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from rasterio.transform import from_origin
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
S2 = CFG["stage2"]
RES = S2["grid_resolution_m"]
CRS = CFG["project_crs"]
WC = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare", 80: "water", 90: "wetland",
      95: "mangrove", 100: "moss"}
CORRIDOR = ["Naraj", "Kishan Nagar", "Tarpur"]


def grid_from_aoi():
    aoi = gpd.read_file(p(S2["aoi_geojson"])).to_crs(CRS)
    minx, miny, maxx, maxy = aoi.total_bounds
    minx, miny = np.floor(minx / RES) * RES, np.floor(miny / RES) * RES
    maxx, maxy = np.ceil(maxx / RES) * RES, np.ceil(maxy / RES) * RES
    w, h = int((maxx - minx) / RES), int((maxy - miny) / RES)
    tr = from_origin(minx, maxy, RES, RES)
    in_aoi = rasterize([(g, 1) for g in aoi.geometry], out_shape=(h, w), transform=tr, fill=0, dtype="uint8")
    return tr, (h, w), in_aoi


def to_grid(src_path, tr, shape, resampling, band=1, src_array=None, src_meta=None):
    out = np.full(shape, np.nan, dtype="float32")
    if src_array is None:
        with rasterio.open(src_path) as s:
            arr = s.read(band).astype("float32")
            nod = s.nodata
            src_tr, src_crs = s.transform, s.crs
        if nod is not None:
            arr[arr == nod] = np.nan
    else:
        arr, (src_tr, src_crs) = src_array, src_meta
    reproject(arr, out, src_transform=src_tr, src_crs=src_crs, dst_transform=tr, dst_crs=CRS,
              resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return out


def main():
    sp = p(S2["rasters_dir"])
    tr, shape, in_aoi = grid_from_aoi()
    h, w = shape
    bands = {"in_aoi": in_aoi.astype("float32")}

    bands["elev_m"] = to_grid(sp / "dem_srtm30.tif", tr, shape, Resampling.average)
    with rasterio.open(sp / "dem_srtm30.tif") as s:
        dem = s.read(1).astype("float32"); dtr, dcrs = s.transform, s.crs
        px = abs(dtr.a) if dcrs.is_projected else 30.0
    gy, gx = np.gradient(dem, px)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
    bands["slope_deg"] = to_grid(None, tr, shape, Resampling.average, src_array=slope, src_meta=(dtr, dcrs))

    occ = to_grid(sp / "jrc_water.tif", tr, shape, Resampling.nearest, band=1)
    with rasterio.open(sp / "jrc_water.tif") as s:
        o = s.read(1).astype("float32"); otr, ocrs = s.transform, s.crs
    river_frac = to_grid(None, tr, shape, Resampling.average, src_array=(o >= 50).astype("float32"),
                         src_meta=(otr, ocrs))
    river_frac30 = to_grid(None, tr, shape, Resampling.average, src_array=(o >= 30).astype("float32"),
                           src_meta=(otr, ocrs))
    river = (river_frac >= 0.3) | (river_frac30 >= 0.5)
    # keep only connected channel systems (drops ponds / isolated tanks)
    lab, n = ndimage.label(river, structure=np.ones((3, 3)))
    if n:
        sizes = ndimage.sum(river, lab, range(1, n + 1))
        river = np.isin(lab, 1 + np.where(sizes >= 30)[0])
    bands["river"] = river.astype("float32")
    dist, (iy, ix) = ndimage.distance_transform_edt(~river, return_indices=True)
    bands["dist_river_m"] = (dist * RES).astype("float32")
    elev = bands["elev_m"]
    elev_f = np.where(np.isnan(elev), np.nanmedian(elev), elev)
    # River water-surface elevation: SRTM over water is noisy (radar returns, -10..+30 m
    # spikes). Use a robust low value per 5x5 window, then a normalized Gaussian smoothing
    # over river cells only (sigma = 2 km) so the surface varies smoothly along the channel.
    rv = np.where(river, elev_f, np.nan)
    with np.errstate(all="ignore"):
        rv = ndimage.generic_filter(rv, lambda a: np.nanpercentile(a, 25) if np.isfinite(a).any() else np.nan,
                                    size=5, mode="nearest")
    wgt = np.where(river & np.isfinite(rv), 1.0, 0.0)
    num = ndimage.gaussian_filter(np.where(wgt > 0, rv, 0.0), sigma=20)
    den = ndimage.gaussian_filter(wgt, sigma=20)
    riv_elev = np.where(river, num / np.maximum(den, 1e-6), np.nan)
    bands["har_m"] = (elev_f - riv_elev[iy, ix]).astype("float32")
    bands["jrc_occurrence"] = occ

    with rasterio.open(sp / "worldcover2021.tif") as s:
        lc = s.read(1); ltr, lcrs = s.transform, s.crs
    fr = {}
    for code, name in WC.items():
        if name in ("mangrove", "moss"):
            continue
        fr[name] = to_grid(None, tr, shape, Resampling.average, src_array=(lc == code).astype("float32"),
                           src_meta=(ltr, lcrs))
        bands[f"lc_{name}_frac"] = fr[name]
    names = list(fr)
    stack = np.stack([np.nan_to_num(fr[n], nan=-1) for n in names])
    inv = {v: k for k, v in WC.items()}
    bands["lc_major"] = np.vectorize(lambda i: inv[names[i]])(stack.argmax(0)).astype("float32")

    st = gpd.read_file(p("00_project", "aoi", "stations.geojson")).to_crs(CRS).set_index("station")
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = tr.c + (xx + 0.5) * RES, tr.f - (yy + 0.5) * RES
    d = np.stack([np.hypot(cx - st.loc[s].geometry.x, cy - st.loc[s].geometry.y) for s in CORRIDOR])
    bands["near_station"] = d.argmin(0).astype("float32")
    bands["dist_station_m"] = d.min(0).astype("float32")

    out = p("06_features"); out.mkdir(exist_ok=True)
    order = list(bands)
    with rasterio.open(out / "stage2_grid.tif", "w", driver="GTiff", height=h, width=w, count=len(order),
                       dtype="float32", crs=CRS, transform=tr, compress="deflate", nodata=np.nan) as dst:
        for i, k in enumerate(order, 1):
            dst.write(bands[k].astype("float32"), i); dst.set_band_description(i, k)
    meta = {"bands": order, "transform": list(tr)[:6], "shape": [h, w], "crs": CRS, "res": RES,
            "corridor_stations": CORRIDOR}
    (out / "stage2_grid_meta.json").write_text(json.dumps(meta, indent=1))
    cells = pd.DataFrame({k: v.ravel() for k, v in bands.items()})
    cells["row"], cells["col"] = yy.ravel(), xx.ravel()
    cells["cell_id"] = np.arange(len(cells))
    cells = cells[cells.in_aoi == 1]
    cells.to_parquet(out / "stage2_cells.parquet", index=False)
    print(f"Grid {h}x{w} @ {RES} m; {len(cells)} AOI cells; river cells {int(cells.river.sum())}")
    print(cells[["elev_m", "slope_deg", "dist_river_m", "har_m"]].describe().round(2).to_string())


if __name__ == "__main__":
    main()
