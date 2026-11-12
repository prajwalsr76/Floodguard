#!/usr/bin/env python3
"""
One command to refresh the live dashboard:
    python scripts/realtime/update_dashboard.py            # fetch live data, run models, rebuild HTML
    python scripts/realtime/update_dashboard.py --offline  # reuse the last fetched inputs

This rebuilds the saved analysis page. For the live, self-updating system use the
live service instead: START_FLOODGUARD.bat (scripts/live/floodguard_server.py).
"""
import subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def run(script):
    print(f"\n=== {script} ===")
    subprocess.run([sys.executable, str(HERE / script)], cwd=ROOT, check=True)


if __name__ == "__main__":
    if "--offline" not in sys.argv:
        run("fetch_live_inputs.py")
    run("run_realtime.py")
    if not (ROOT / "08_results" / "realtime" / "case_layers.json").exists():
        run("export_case_layers.py")
    run("build_dashboard.py")
