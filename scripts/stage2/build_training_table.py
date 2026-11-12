#!/usr/bin/env python3
"""
FloodGuard Stage 2 - assemble the cell x event table.

For every accepted event raster 01_raw/spatial/events/flood_<event_id>.tif (GEE 03):
  * 30 m flood / valid bands -> 100 m fractions on the common grid
  * label = 1 if flood_frac >= 0.5, 0 otherwise; cells with valid_frac < 0.5 dropped;
    permanent-river cells (static 'river' = 1) dropped (they are water in every event)
  * CHIRPS 1/3/7-day totals ending the day before the S1 date, resampled to the grid
  * river levels (daily MAX, same quantity Stage 1 forecasts) OBSERVED on the S1 acquisition date (from floodguard_s1_event_dates.csv);
    at forecast time these are replaced by Stage-1 predictions.
Event QC: 00_project/aoi/flood_events_qc.csv (event_id, accept 0/1, note) if present.

Output: 06_features/stage2_events/<event_id>.parquet (all valid cells, one file per event) and a summary.
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
S2 = CFG["stage2"]


def load_grid():
    meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
    tr = Affine(*meta["transform"])
    cells = pd.read_parquet(p("06_features", "stage2_cells.parquet"))
    return meta, tr, cells


def resample_band(src, band, tr, shape, crs, how):
    out = np.full(shape, np.nan, "float32")
    reproject(rasterio.band(src, band), out, dst_transform=tr, dst_crs=crs, resampling=how, dst_nodata=np.nan)
    return out


def levels_on(date, daily, max_gap=2):
    """Daily max level per station on the label date. If that day is missing (e.g. the
    2022-08-16 record nulled by QC), interpolate linearly between the nearest observed
    days, but only when the gap is <= max_gap days on both sides; otherwise NaN."""
    t = pd.Timestamp(date)
    out = {}
    for s in ["Tikarpara", "Naraj", "Kishan Nagar", "Tarpur"]:
        ser = daily[daily.station == s].set_index("date").daily_max_m.dropna()
        if t in ser.index:
            out[s] = float(ser.loc[t]); continue
        before, after = ser[ser.index < t], ser[ser.index > t]
        if len(before) and len(after) and (t - before.index[-1]).days <= max_gap \
                and (after.index[0] - t).days <= max_gap:
            t0, t1 = before.index[-1], after.index[0]
            w = (t - t0).days / (t1 - t0).days
            out[s] = float(before.iloc[-1] * (1 - w) + after.iloc[0] * w)
            print(f"  {s} {t.date()}: interpolated {out[s]:.2f} m from {t0.date()} / {t1.date()}")
        else:
            out[s] = np.nan
    return out


def add_dynamic(df, lv, rain=None):
    """Event-level and cell-level dynamic features shared by training and prediction."""
    corridor = ["Naraj", "Kishan Nagar", "Tarpur"]
    df["lvl_naraj"] = lv["Naraj"]
    df["lvl_kishan_nagar"] = lv["Kishan Nagar"]
    df["lvl_tarpur"] = lv["Tarpur"]
    near = np.array([lv[s] for s in corridor])[df.near_station.astype(int).to_numpy()]
    # fall back to Naraj-based estimate when the nearest gauge is missing that day
    df["lvl_near"] = np.where(np.isnan(near), lv["Naraj"], near)
    df["depth_proxy_m"] = df["lvl_near"] - df["elev_m"]   # gauge level above ground (datum caveat in report)
    if rain is not None:
        for k, v in rain.items():
            df[k] = v
    return df


def main():
    meta, tr, cells = load_grid()
    shape, crs = tuple(meta["shape"]), meta["crs"]
    ev_dir = p(S2["rasters_dir"], "events")
    dates = pd.read_csv(p(S2["rasters_dir"], "events", "floodguard_s1_event_dates.csv"))
    dates = dates[dates.s1_date.notna() & (dates.s1_date.astype(str) != "")]
    qc_path = p("00_project", "aoi", "flood_events_qc.csv")
    if qc_path.exists():
        qc = pd.read_csv(qc_path)
        dates = dates[dates.event_id.isin(qc[qc.accept == 1].event_id)]
    events = pd.read_csv(p(S2["events_csv"])).set_index("event_id")
    daily = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])

    idx = (cells.row.to_numpy(), cells.col.to_numpy())
    river_g = np.zeros(shape, bool); river_g[idx] = cells.river.to_numpy() == 1
    parts, summary = [], []
    for r in dates.itertuples():
        f = ev_dir / f"flood_{r.event_id}.tif"
        if not f.exists():
            continue
        with rasterio.open(f) as src:
            names = list(src.descriptions)
            b = {n: i + 1 for i, n in enumerate(names)}
            flood = resample_band(src, b.get("flood", 1), tr, shape, crs, Resampling.average)
            valid = resample_band(src, b.get("valid", 3), tr, shape, crs, Resampling.average)
            rain = {f"rain_{k}": resample_band(src, b[k], tr, shape, crs, Resampling.bilinear)[idx]
                    for k in ("chirps_sum1", "chirps_sum3", "chirps_sum7") if k in b}
        df = cells.copy()
        df["flood_frac"], df["valid_frac"] = flood[idx], valid[idx]
        # river-connected inundation: flooded patches (8-connected, 100 m) that touch the
        # channel network (dilated by 300 m). Separates fluvial flooding from isolated
        # rain ponding / flooded paddy that does not depend on river stage.
        fz = (flood >= 0.5) & (valid >= 0.5) & ~river_g
        lab, n = ndimage.label(fz, structure=np.ones((3, 3)))
        touch = np.unique(lab[ndimage.binary_dilation(river_g, iterations=3) & (lab > 0)])
        df["label_conn"] = np.isin(lab, touch[touch > 0])[idx].astype(int)
        lv = levels_on(r.s1_date, daily)
        df = add_dynamic(df, lv, rain)
        df["event_id"], df["s1_date"] = r.event_id, r.s1_date
        df["kind"] = events.loc[r.event_id, "kind"] if r.event_id in events.index else ""
        df["year"] = pd.Timestamp(r.s1_date).year
        df = df[(df.valid_frac >= 0.5) & (df.river == 0)]
        df["label"] = (df.flood_frac >= 0.5).astype(int)
        parts.append(df)
        summary.append(dict(event_id=r.event_id, s1_date=r.s1_date, kind=df.kind.iloc[0] if len(df) else "",
                            naraj_m=lv["Naraj"], cells=len(df), flooded_cells=int(df.label.sum()),
                            connected_cells=int(df.label_conn.sum()),
                            flooded_km2=df.label.sum() * (meta["res"] / 1000) ** 2))
    if not parts:
        raise RuntimeError(f"No event rasters found in {ev_dir}")
    out = pd.concat(parts, ignore_index=True)
    # one file per event keeps every file small enough to share (read back by train_spatial.load_events)
    edir = p("06_features", "stage2_events"); edir.mkdir(parents=True, exist_ok=True)
    for ev, g in out.groupby("event_id", sort=False):
        g.reset_index(drop=True).to_parquet(edir / f"{ev}.parquet", index=False, compression="zstd")
    sm = pd.DataFrame(summary)
    sm.to_csv(p("06_features", "stage2_events_summary.csv"), index=False)
    print(sm.round(2).to_string(index=False))
    print(f"\n{len(out)} cell-event rows, positive rate {out.label.mean():.3f}")


if __name__ == "__main__":
    main()
