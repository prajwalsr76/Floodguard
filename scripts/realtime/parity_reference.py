#!/usr/bin/env python3
"""
Reference outputs for the browser engine parity test (tests/engine_parity.mjs).
Runs the Python real-time chain and stores, for several issue days, the inputs the browser
would see (daily levels, rescaled zone rain) and the Python results (Stage 1 forecast,
range, P(exceed), outlook, status; Stage 2 class grid, areas, settlement classes, SHAP).

Out: 08_results/realtime/parity_reference.json
"""
import base64, gzip, json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import p  # noqa: E402
import realtime.run_realtime as R  # noqa: E402


def main():
    R.P_LIKELY = float(pd.read_csv(p("08_results", "stage2_connected", "cv_tuned_summary.csv"), index_col=0)
                       .loc["mean_threshold", "value"])
    d, _, _ = R.load_levels()
    rain, _, _ = R.load_rain()
    s1, series, _ = R.run_stage1(d, rain)
    t0 = pd.Timestamp(s1["Naraj"]["issue"])
    issues = [t0, pd.Timestamp("2026-07-29"), pd.Timestamp("2026-09-27"), pd.Timestamp("2026-08-27")]
    cases = []
    s2 = R.Stage2()
    hist0 = d[d.date < R.CUTOVER].pivot_table(index="date", columns="station", values="daily_max_m")
    evs = pd.read_csv(p("06_features", "stage2_events_summary.csv"), parse_dates=["s1_date"])
    ref_days = evs[evs.kind == "low_stage_reference"].s1_date
    ref_lv = {st: float(hist0[st].reindex(ref_days).median()) for st in R.TARGETS}
    ref_rain = {n: float(np.median([R.rain_sums(rain, t - pd.Timedelta(days=1))[n] for t in ref_days])) for n in (1, 3, 7)}
    s2.set_reference(ref_lv, ref_rain)
    places = json.loads(p("08_results", "realtime", "dashboard_data.json").read_text())["grid"]["places"]
    for t in issues:
        win = d[(d.date > t - pd.Timedelta(days=20)) & (d.date <= t)]
        daily = {st: [[str(r.date.date()), r.daily_mean_m, r.daily_max_m, r.daily_min_m] for r in g.itertuples()]
                 for st, g in win.groupby("station")}
        rw = rain.loc[t - pd.Timedelta(days=19):t]
        rz = {z: [[str(i.date()), float(v)] for i, v in rw[z].items()] for z in R.ZONES}
        # Stage 1 at issue t, recomputed the same way run_stage1 does (series rows + outlook)
        st1 = {}
        for st in R.TARGETS:
            S = series[st].set_index("issue")
            row = S.loc[t]
            st1[st] = dict(fc=float(row.fc), lo=float(row.lo), hi=float(row.hi), p_warn=float(row.p_warn),
                           status=R.status(row.fc, row.hi, st), base=float(row.base))
        if t == t0:
            for st in R.TARGETS:
                st1[st]["outlook"] = s1[st]["outlook"]
                st1[st]["members"] = s1[st]["members"]
        lv = {st: st1[st]["fc"] for st in R.TARGETS}
        rs = R.rain_sums(rain, t)
        prob, df = s2.predict(lv, rs)
        c = s2.classes(prob).astype(np.uint8)
        sm = s2.summary(prob, df)
        pl = s2.place_risk(prob, [dict(name=x["name"], row=x["row"], col=x["col"]) for x in places])
        cases.append(dict(issue=str(t.date()), daily=daily, rain=rz, stage1=st1, levels=lv, rain_sums={str(k): v for k, v in rs.items()},
                          classes=base64.b64encode(gzip.compress(c.tobytes(), 9, mtime=0)).decode(),
                          summary=sm, places=pl))
        print(t.date(), {k: round(v["fc"], 3) for k, v in st1.items()}, sm["km2_likely"], sm["km2_elevated"], sm["km2_watch"])
    p("08_results", "realtime", "parity_reference.json").write_text(json.dumps(cases, default=float))


if __name__ == "__main__":
    main()
