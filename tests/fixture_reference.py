"""Python reference for the browser live-run test: feed the recorded CWC/Open-Meteo responses
(tests/fixtures) through the Python chain and print the Stage 1 forecasts and live-map areas."""
import json, sys, shutil, tempfile
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
import realtime.run_realtime as R
from common.config import p

CODES = {"034-MDBURLA": "Tikarpara", "036-MDBURLA": "Naraj", "046-MDBURLA": "Kishan Nagar", "047-MDBURLA": "Tarpur"}
tmp = Path(tempfile.mkdtemp())
for f in R.LIVE.iterdir():
    shutil.copy(f, tmp / f.name)
ffs = json.load(open("tests/fixtures/ffsgz.json"))
old = pd.read_csv(tmp / "cwc_ffs_daily.csv", parse_dates=["date"])
rows, hourly = [], []
for code, txt in ffs.items():
    st = CODES[code]
    j = json.loads(txt)
    s = pd.DataFrame([(r["id"]["dataTime"][:16], float(r["dataValue"])) for r in j], columns=["time", "level_m"])
    s["time"] = pd.to_datetime(s.time)
    g = s.groupby(s.time.dt.normalize()).level_m
    rows.append(pd.DataFrame({"station": st, "date": g.mean().index, "daily_mean_m": (np.floor(g.mean() * 100 + 0.5) / 100).values,
                              "daily_max_m": g.max().values, "daily_min_m": g.min().values, "n_obs": g.size().values}))
    hourly.append(s.assign(station=st)[["station", "time", "level_m"]])
new = pd.concat(rows)
# keep archive days before the fixture window, replace the window with the fixture (as the browser sees it)
keep = old[~old.set_index(["station", "date"]).index.isin(new.set_index(["station", "date"]).index)]
keep = keep[keep.date < new.date.min()]
pd.concat([keep, new]).sort_values(["station", "date"]).to_csv(tmp / "cwc_ffs_daily.csv", index=False)
pd.concat(hourly).to_csv(tmp / "cwc_ffs_hourly.csv", index=False)
om = json.load(open("tests/fixtures/omgz.json"))
fc = {}
for z, txt in om.items():
    j = json.loads(txt); j = j if isinstance(j, list) else [j]
    t = pd.to_datetime(j[0]["daily"]["time"])
    v = np.array([[np.nan if x is None else x for x in a["daily"]["precipitation_sum"]] for a in j], float)
    fc[z] = pd.Series(np.floor(np.nanmean(v, axis=0) * 100 + 0.5) / 100, index=t)
pd.DataFrame(fc).rename_axis("date").to_csv(tmp / "openmeteo_zone_daily_fc.csv")
R.LIVE = tmp
R.P_LIKELY = 0.9
d, _, _ = R.load_levels(); rain, _, _ = R.load_rain()
s1, series, _ = R.run_stage1(d, rain)
out = {st: dict(issue=s1[st]["issue"], fc=s1[st]["fc"], lo=s1[st]["lo"], hi=s1[st]["hi"], p=s1[st]["p_warn"], o48=s1[st]["outlook"]["+48h"]["fc"], o72=s1[st]["outlook"]["+72h"]["fc"]) for st in R.TARGETS}
print(json.dumps(out))
t0 = pd.Timestamp(s1["Naraj"]["issue"])
lv = {st: float(series[st].set_index("issue").fc[t0]) for st in R.TARGETS}
rs = R.rain_sums(rain, t0)
s2 = R.Stage2()
hist0 = d[d.date < R.CUTOVER].pivot_table(index="date", columns="station", values="daily_max_m")
evs = pd.read_csv(p("06_features", "stage2_events_summary.csv"), parse_dates=["s1_date"])
ref_days = evs[evs.kind == "low_stage_reference"].s1_date
s2.set_reference({st: float(hist0[st].reindex(ref_days).median()) for st in R.TARGETS},
                 {n: float(np.median([R.rain_sums(rain, t - pd.Timedelta(days=1))[n] for t in ref_days])) for n in (1, 3, 7)})
prob, df = s2.predict(lv, rs)
sm = s2.summary(prob, df)
print("stage2", {k: sm[k] for k in ("km2_likely", "km2_elevated", "km2_watch")}, "rain", {k: round(v, 2) for k, v in rs.items()}, "lv", {k: round(v, 3) for k, v in lv.items()})
