#!/usr/bin/env python3
"""
FloodGuard - quality check of the Sentinel-1 flood masks (step 4).

For every event raster: valid coverage of the AOI, flooded area (non-channel cells with
>= 50 % flooded 10 m pixels), the Naraj level on the S1 date, and the share of flooded
cells lying within 2 km of a channel (river-driven) vs farther away (paddy / rain ponding).

Automatic decisions (written to 00_project/aoi/flood_events_qc.csv, edit by hand if needed):
  reject  valid coverage < 25 % of the AOI (too little of the floodplain observed)
  reject  |days_from_peak| > 4 for flood/moderate events (image too far from the peak)
  accept  otherwise; 'note' flags events whose flooded area is dominated by far-field
          ponding (> 60 % of flooded cells farther than 2 km from a channel)
Panels: 08_results/stage2/flood_mask_qc/qc_page_<n>.png, area_vs_stage.png
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()


def main():
    meta = json.loads(p("06_features", "stage2_grid_meta.json").read_text())
    with rasterio.open(p("06_features", "stage2_grid.tif")) as g:
        B = {n: g.read(i + 1) for i, n in enumerate(meta["bands"])}
    aoi, river, dist = B["in_aoi"] == 1, B["river"] == 1, B["dist_river_m"]
    n_aoi = aoi.sum()
    ev = pd.read_csv(p("01_raw", "spatial", "events", "floodguard_s1_event_dates.csv"))
    kinds = pd.read_csv(p(CFG["stage2"]["events_csv"])).set_index("event_id").kind
    d = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    nar = d[d.station == "Naraj"].set_index("date").daily_max_m

    rows, masks = [], []
    for r in ev.itertuples():
        with rasterio.open(p("01_raw", "spatial", "events", f"flood_{r.event_id}.tif")) as s:
            fl, va = s.read(1) >= 0.5, s.read(3) >= 0.5
        fz = fl & va & aoi & ~river
        cov = (va & aoi).sum() / n_aoi
        near = (fz & (dist <= 2000)).sum() / max(fz.sum(), 1)
        lv = nar.get(pd.Timestamp(r.s1_date), np.nan)
        kind = kinds.get(r.event_id, "")
        reasons = []
        if cov < 0.25:
            reasons.append(f"coverage {cov:.0%} < 25%")
        if kind != "low_stage_reference" and abs(r.days_from_peak) > 4:
            reasons.append(f"{r.days_from_peak:.0f} d from peak")
        note = "far-field ponding dominates" if (fz.sum() > 2000 and near < 0.4) else ""
        rows.append(dict(event_id=r.event_id, kind=kind, s1_date=r.s1_date, days_from_peak=round(r.days_from_peak, 1),
                         naraj_m=lv, coverage=round(cov, 3), flooded_km2=fz.sum() / 100,
                         flooded_density_pct=round(100 * fz.sum() / max((va & aoi & ~river).sum(), 1), 2),
                         share_within_2km=round(near, 2), accept=int(not reasons),
                         note="; ".join(reasons + ([note] if note else []))))
        masks.append((r.event_id, r.s1_date, lv, fz, va & aoi))
    q = pd.DataFrame(rows)
    out = p("08_results", "stage2", "flood_mask_qc"); out.mkdir(parents=True, exist_ok=True)
    q.to_csv(p("00_project", "aoi", "flood_events_qc.csv"), index=False)
    q.to_csv(out / "flood_events_qc.csv", index=False)

    per = 20
    for pg in range(0, len(masks), per):
        fig, ax = plt.subplots(4, 5, figsize=(18, 9))
        for a in ax.ravel():
            a.axis("off")
        for a, (eid, dt, lv, fz, va) in zip(ax.ravel(), masks[pg:pg + per]):
            img = np.ones(fz.shape + (3,)); img[~aoi] = .9; img[aoi & ~va] = [.75, .75, .75]
            img[river] = [.13, .44, .71]; img[fz] = [.84, .19, .12]
            acc = q.set_index("event_id").loc[eid]
            a.imshow(img); a.set_title(f"{eid} {dt}\nNaraj {lv:.2f} m, {fz.sum()/100:.0f} km²"
                                       f"{'' if acc.accept else '  REJECT'}", fontsize=7,
                                       color="k" if acc.accept else "#b30000")
        fig.suptitle("Sentinel-1 flood masks (red = flood, blue = channel, grey = not observed)")
        fig.tight_layout(); fig.savefig(out / f"qc_page_{pg // per + 1}.png", dpi=100); plt.close(fig)

    a = q[q.accept == 1]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for k, c in [("flood_above_warning", "#b30000"), ("moderate_rise", "#e69f00"), ("low_stage_reference", "#2171b5")]:
        s = a[a.kind == k]
        ax.scatter(s.naraj_m, s.flooded_density_pct, label=k.replace("_", " "), color=c)
    ax.axvline(CFG["thresholds"]["Naraj"]["warning"], color="grey", ls="--", lw=.8)
    ax.set(xlabel="Naraj daily max on Sentinel-1 date (m)", ylabel="Flooded share of observed floodplain (%)",
           title="Flood extent rises with river stage")
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(out / "area_vs_stage.png", dpi=130)
    r = a[["naraj_m", "flooded_density_pct"]].corr(method="spearman").iloc[0, 1]
    print(q.to_string(index=False))
    print(f"\naccepted {len(a)} of {len(q)} | Spearman(stage, flooded share) = {r:.2f}")


if __name__ == "__main__":
    main()
