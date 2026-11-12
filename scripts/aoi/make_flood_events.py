#!/usr/bin/env python3
"""
Derive the list of historical flood events (and low-stage reference dates) for which
Stage-2 inundation labels must be produced from Sentinel-1 (or NRSC/NDEM maps).

Event   = consecutive days with Naraj daily max >= CWC warning level (25.41 m),
          merged if separated by <= 3 days. Peak date/level recorded.
Moderate= peaks between 24.0 m and the warning level (adds label diversity:
          the Stage-2 model must learn that lower stages flood less area).
Reference (negative) dates = monsoon days with Naraj below 22.5 m.
Only dates from 2014-10-10 onwards (Sentinel-1 IW GRD archive) are kept.

Output: 00_project/aoi/flood_events.csv
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import load_config, p  # noqa: E402

CFG = load_config()
WARN = CFG["thresholds"]["Naraj"]["warning"]
S1_START = pd.Timestamp("2014-10-10")


def episodes(s, thr, merge_gap=3):
    above = s[s >= thr]
    if above.empty:
        return []
    groups, cur = [], [above.index[0]]
    for d in above.index[1:]:
        if (d - cur[-1]).days <= merge_gap + 1:
            cur.append(d)
        else:
            groups.append(cur); cur = [d]
    groups.append(cur)
    return [(g[0], g[-1], s.loc[g].idxmax(), s.loc[g].max()) for g in groups]


def main():
    d = pd.read_csv(p("05_processed", "water_level", "manual", "daily_water_level_qc2.csv"), parse_dates=["date"])
    s = d[d.station == "Naraj"].set_index("date").daily_max_m.dropna().sort_index()
    rows = []
    for a, b, pk, lv in episodes(s, WARN):
        rows.append(dict(event_id=f"E{pk:%Y%m%d}", kind="flood_above_warning", start=a.date(), end=b.date(),
                         peak_date=pk.date(), naraj_peak_m=round(lv, 2)))
    for a, b, pk, lv in episodes(s, 24.0):
        if lv < WARN:
            rows.append(dict(event_id=f"M{pk:%Y%m%d}", kind="moderate_rise", start=a.date(), end=b.date(),
                             peak_date=pk.date(), naraj_peak_m=round(lv, 2)))
    low = s[(s < 22.5) & s.index.month.isin([7, 8, 9])]
    for yr, g in low.groupby(low.index.year):
        dd = g.index[len(g) // 2]
        rows.append(dict(event_id=f"R{dd:%Y%m%d}", kind="low_stage_reference", start=dd.date(), end=dd.date(),
                         peak_date=dd.date(), naraj_peak_m=round(g.loc[dd], 2)))
    ev = pd.DataFrame(rows)
    ev["sentinel1_available"] = pd.to_datetime(ev.peak_date) >= S1_START
    ev = ev.sort_values("peak_date")
    out = p(CFG["stage2"]["events_csv"]); out.parent.mkdir(parents=True, exist_ok=True)
    ev.to_csv(out, index=False)
    print(ev.to_string(index=False))


if __name__ == "__main__":
    main()
