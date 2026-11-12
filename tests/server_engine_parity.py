#!/usr/bin/env python3
"""
Parity test for the live server's Stage 1 engine (scripts/live/fg_engine.py, standard library
only) against the Python training pipeline, on the same issue days as tests/engine_parity.mjs.
Run from the project root:  python tests/server_engine_parity.py
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "live"))
import fg_engine as FG  # noqa: E402

E = FG.load(json.loads((ROOT / "08_results" / "realtime" / "engine_assets.json").read_text()))
REF = json.loads((ROOT / "08_results" / "realtime" / "parity_reference.json").read_text())
TH = json.loads((ROOT / "08_results" / "realtime" / "dashboard_data.json").read_text())["thresholds"]
fails = 0
for C in REF:
    daily = {st: {d: [m, x, n] for d, m, x, n in rows} for st, rows in C["daily"].items()}
    rain = {z: {d: v for d, v in rows} for z, rows in C["rain"].items()}
    for st in ("Naraj", "Kishan Nagar", "Tarpur"):
        r, ref = FG.stage1(E, st, daily, rain, C["issue"], TH[st]), C["stage1"][st]
        checks = [("fc", abs(r["fc"] - ref["fc"]) < 1e-5), ("lo", abs(r["lo"] - ref["lo"]) < 1e-5), ("hi", abs(r["hi"] - ref["hi"]) < 1e-5),
                  ("p_warn", abs(r["p_warn"] - ref["p_warn"]) < 1e-9), ("status", r["status"] == ref["status"])]
        if "outlook" in ref:
            for k in ("+48h", "+72h"):
                checks.append((k, abs(r["outlook"][k]["fc"] - ref["outlook"][k]["fc"]) < 0.006))
        for name, ok in checks:
            if not ok:
                fails += 1
                print("FAIL", C["issue"], st, name, r.get(name), ref.get(name))
    print(C["issue"], {st: round(FG.stage1(E, st, daily, rain, C["issue"], TH[st])["fc"], 3) for st in ("Naraj", "Kishan Nagar", "Tarpur")})
print(f"{fails} FAILURES" if fails else "ALL SERVER ENGINE CHECKS PASSED")
sys.exit(1 if fails else 0)
