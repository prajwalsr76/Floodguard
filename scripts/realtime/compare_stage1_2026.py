#!/usr/bin/env python3
"""
2026 (unseen season) score of the live Stage 1 models, old versus new, on identical inputs:
  frozen      - research models trained 2014-2020 (the live models until October 2026)
  operational - the same model types retrained on 2014-2025 (train_operational.py)
Nothing here is tuned on 2026; the choice to retrain was made before this comparison.
Out: 08_results/realtime/stage1_2026_frozen_vs_operational.csv
"""
import importlib, os, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import p  # noqa: E402

rows = []
for which in ("frozen", "operational"):
    os.environ["FG_STAGE1"] = which
    import realtime.run_realtime as R
    R = importlib.reload(R)
    d, _, _ = R.load_levels()
    rain, _, _ = R.load_rain()
    _, series, verif = R.run_stage1(d, rain)
    for st, v in verif.items():
        lt = v["lead_time"]
        rows.append(dict(models=which, trained=R.load_stage1(st)["trained"], gauge=st, days=v["n_days"], RMSE=v["RMSE"],
                         RMSE_persistence=v["RMSE_persistence"], skill=v["skill_vs_persistence"], skill_monsoon=v["skill_monsoon"],
                         warning_days=v["days_above_warning_obs"], caught=v["warning"]["hits"], missed=v["warning"]["misses"],
                         false_alarms=v["warning"]["false_alarms"], CSI=v["warning"]["CSI"], inside_80pct=v["inside_80pct_band"],
                         onsets=len(lt), watch_24h_ahead=sum(e["watch_h"] >= 24 for e in lt), warning_24h_ahead=sum(e["lead_h"] >= 24 for e in lt)))
df = pd.DataFrame(rows)
df.to_csv(p("08_results", "realtime", "stage1_2026_frozen_vs_operational.csv"), index=False)
print(df.to_string(index=False))
