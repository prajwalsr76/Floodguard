#!/usr/bin/env python3
"""
FloodGuard end-to-end forecast: issue day t  ->  flood-risk map and warning for t+1.

  1. Stage 1  : next-day daily-max level at Naraj, Kishan Nagar, Tarpur
                (XGBoost models from train_models.py, features from build_features.py)
  2. Stage 2  : per-cell flood probability from static grid + predicted levels
                + CHIRPS antecedent rainfall up to day t
  3. Explain  : SHAP drivers for the high-risk cells
  4. Warn     : GeoTIFF + PNG map + markdown/JSON warning bulletin

Usage:  python scripts/stage2/forecast_and_warn.py --date 2022-08-16
        [--rain 06_features/rain_grid_<date>.tif]   (3 bands: sum1, sum3, sum7 ending day t;
         if omitted the CHIRPS floodplain-zone means from Stage 1 are used uniformly)
Risk classes are PROVISIONAL (0.2/0.5/0.8) until calibrated on held-out events.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
import rasterio
from rasterio.transform import Affine
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
import shap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402
from stage2.build_training_table import add_dynamic  # noqa: E402

CFG = load_config()
CLASSES = [(0.0, "Low"), (0.2, "Moderate"), (0.5, "High"), (0.8, "Very high")]
COLORS = ["#f7f7f7", "#fdd49e", "#fc8d59", "#b30000"]


def slug(s):
    return s.lower().replace(" ", "_")


def stage1_forecast(date):
    out = {}
    for st in ["Naraj", "Kishan Nagar", "Tarpur"]:
        X = pd.read_csv(p("06_features", f"stage1_{slug(st)}_forecast_inputs.csv"), parse_dates=["date"]).set_index("date")
        m = joblib.load(p("07_models", "stage1", f"{slug(st)}_tabular.joblib"))
        if pd.Timestamp(date) not in X.index:
            out[st] = dict(pred=np.nan, today=np.nan, threshold=m["threshold"], note="no input row for date")
            continue
        row = X.loc[[pd.Timestamp(date)], m["features"]]
        today = float(X.loc[pd.Timestamp(date), "base_max"])
        pred = today + float(m["xgb"].predict(row)[0])
        obs = X.loc[pd.Timestamp(date), "y_next_max"]
        out[st] = dict(pred=round(pred, 2), today=round(today, 2), observed_next=None if pd.isna(obs) else round(float(obs), 2),
                       threshold=m["threshold"], threshold_source=m["threshold_source"],
                       exceeds=bool(pred >= m["threshold"]))
    out["Tikarpara"] = dict(pred=np.nan)
    return out


def zone_rain(date):
    f = p(CFG["stage1"]["rainfall_csv"])
    if not f.exists():
        return None
    r = pd.read_csv(f, parse_dates=["date"]).set_index("date")["z3_floodplain"]
    t = pd.Timestamp(date)
    return {f"rain_chirps_sum{n}": float(r.loc[t - pd.Timedelta(days=n - 1):t].sum()) for n in (1, 3, 7)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="issue day t (forecast valid for t+1)")
    ap.add_argument("--rain", default=None)
    a = ap.parse_args()
    t = pd.Timestamp(a.date)
    out = p("08_results", "forecasts", f"{(t + pd.Timedelta(days=1)):%Y-%m-%d}"); out.mkdir(parents=True, exist_ok=True)

    s1 = stage1_forecast(t)
    lv = {k: v["pred"] for k, v in s1.items()}
    if np.isnan(lv["Naraj"]):
        raise SystemExit(f"Stage 1 has no Naraj forecast for {t.date()} (missing inputs)")

    meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
    tr, (h, w) = Affine(*meta["transform"]), meta["shape"]
    cells = pd.read_parquet(p("06_features", "stage2_cells.parquet"))
    rain = None
    if a.rain:
        with rasterio.open(a.rain) as r:
            rain = {f"rain_chirps_sum{n}": r.read(i)[cells.row, cells.col] for i, n in enumerate((1, 3, 7), 1)}
    else:
        rain = zone_rain(t)
    df = add_dynamic(cells.copy(), lv, rain)
    M = joblib.load(p("07_models", "stage2", "xgb_final.joblib"))
    for f in M["features"]:
        if f not in df:
            df[f] = np.nan
    prob = M["model"].predict_proba(df[M["features"]])[:, 1]
    prob[df.river.to_numpy() == 1] = np.nan          # permanent channel shown separately
    grid = np.full((h, w), np.nan, "float32"); grid[df.row, df.col] = prob

    with rasterio.open(out / "flood_probability.tif", "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=meta["crs"], transform=tr, nodata=np.nan, compress="deflate") as d:
        d.write(grid, 1)

    cls = np.digitize(prob, [c for c, _ in CLASSES[1:]])
    area = {CLASSES[i][1]: round(float(np.sum(cls[~np.isnan(prob)] == i)) * (meta["res"] / 1000) ** 2, 1)
            for i in range(len(CLASSES))}

    hi = df[(prob >= 0.5)]
    drivers = []
    if len(hi):
        sub = hi.sample(min(3000, len(hi)), random_state=0)
        sv = shap.TreeExplainer(M["model"]).shap_values(sub[M["features"]])
        drivers = (pd.Series(sv.mean(0), index=M["features"]).sort_values(ascending=False).head(5).round(3)
                   .reset_index().rename(columns={"index": "feature", 0: "mean_shap_logodds"}).to_dict("records"))

    fig, ax = plt.subplots(figsize=(10, 6))
    cgrid = np.full((h, w), np.nan); cgrid[df.row, df.col] = np.where(np.isnan(prob), np.nan, cls)
    ext = [tr.c, tr.c + w * tr.a, tr.f + h * tr.e, tr.f]
    ax.imshow(cgrid, cmap=ListedColormap(COLORS), norm=BoundaryNorm(np.arange(-0.5, 4.5), 4), extent=ext,
              interpolation="nearest")
    riv = np.full((h, w), np.nan); riv[df.row, df.col] = np.where(df.river == 1, 1, np.nan)
    ax.imshow(riv, cmap=ListedColormap(["#2171b5"]), extent=ext, interpolation="nearest")
    for i, (_, n) in enumerate(CLASSES):
        ax.plot([], [], "s", color=COLORS[i], label=f"{n} ({area[n]} km²)", markersize=10)
    ax.plot([], [], "s", color="#2171b5", label="River channel")
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title(f"FloodGuard flood-risk forecast valid {(t + pd.Timedelta(days=1)).date()} "
                 f"(issued {t.date()}) | Naraj forecast {lv['Naraj']:.2f} m", fontsize=10)
    ax.set_xlabel("Easting (m, UTM 45N)"); ax.set_ylabel("Northing (m)")
    fig.tight_layout(); fig.savefig(out / "flood_risk_map.png", dpi=140); plt.close(fig)

    level = "RED" if area["Very high"] + area["High"] > 50 or s1["Naraj"].get("exceeds") else \
            ("ORANGE" if area["High"] + area["Moderate"] > 50 else "GREEN")
    bulletin = {"issued": str(t.date()), "valid_for": str((t + pd.Timedelta(days=1)).date()), "alert_level": level,
                "stage1": s1, "risk_area_km2": area, "top_drivers_high_risk_cells": drivers,
                "disclaimer": "Research prototype - not an official CWC/OSDMA warning."}
    (out / "warning.json").write_text(json.dumps(bulletin, indent=1, default=str))
    md = [f"# FloodGuard warning - valid {bulletin['valid_for']} (issued {bulletin['issued']})",
          f"**Alert level: {level}**", "", "| Station | Today max (m) | Forecast next-day max (m) | Threshold (m) | Exceeds |",
          "|---|---|---|---|---|"]
    for st in ["Naraj", "Kishan Nagar", "Tarpur"]:
        v = s1[st]
        md.append(f"| {st} | {v.get('today')} | {v.get('pred')} | {v.get('threshold'):.2f} ({v.get('threshold_source','')}) | {v.get('exceeds')} |")
    md += ["", "| Risk class | Area (km²) |", "|---|---|"] + [f"| {k} | {v} |" for k, v in area.items()]
    if drivers:
        md += ["", "Main factors raising risk in high-risk cells (mean SHAP, log-odds):"] + \
              [f"- {d['feature']}: {d['mean_shap_logodds']:+.2f}" for d in drivers]
    md += ["", f"_{bulletin['disclaimer']}_"]
    (out / "warning.md").write_text("\n".join(md))
    print("\n".join(md)); print(f"\nOutputs in {out}")


if __name__ == "__main__":
    main()
