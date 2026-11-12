#!/usr/bin/env python3
"""
End-to-end test of the live service, offline: starts scripts/live/floodguard_server.py on port 8799
with the CWC and Open-Meteo answers recorded on 21 Oct 2026 (tests/fixtures), then checks
  1. the automatic first update: Naraj forecast 22.003 m (the Python pipeline gives the same),
     level Normal, no gauge late;
  2. the dashboard page and model file are served;
  3. a demo replay of the July 2026 flood raises Watch, Warning and Danger alerts by itself
     (no message is sent to real phones during the test).
Run from the project root:  python tests/test_live_server.py   (takes about a minute)
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
tmp = tempfile.mkdtemp()
env = dict(os.environ, FG_FIXTURES=str(ROOT / "tests" / "fixtures"), FG_NOW="2026-10-21T10:40:00Z",
           FG_PORT=str(PORT), FG_LIVE_DIR=tmp, FG_NO_SEND="1", PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "live" / "floodguard_server.py"), "--no-browser"], env=env)
base = f"http://127.0.0.1:{PORT}"
get = lambda path: json.loads(urlopen(base + path, timeout=10).read().decode())
fails = 0


def check(name, ok, info=""):
    global fails
    print(("ok   " if ok else "FAIL ") + name, info)
    fails += 0 if ok else 1


try:
    for _ in range(60):
        try:
            L = get("/api/latest")
            if L.get("generated_utc"):
                break
        except Exception:
            pass
        time.sleep(1)
    check("first automatic update", L.get("mode") == "live")
    n = L["stage1"]["Naraj"]
    check("Naraj forecast 22.003 m", abs(n["fc"] - 22.003) < 0.0006, n["fc"])
    check("level Normal", L["level"] == "GREEN", L["level_name"])
    check("no gauge late", L["data_state"] == "ok" and not L["delayed"], L["delayed"])
    html = urlopen(base + "/", timeout=10).read()
    check("dashboard served", b"FloodGuard" in html and len(html) > 500_000, len(html))
    check("model file served", len(urlopen(base + "/engine_assets.json", timeout=30).read()) > 1_000_000)
    urlopen(Request(base + "/api/demo/start?scenario=jul2026", method="POST"), timeout=10)
    seen = set()
    for _ in range(400):
        time.sleep(1)
        seen |= {a["kind"] for a in get("/api/alerts") if a.get("demo")}
        if "raised to Danger" in seen:
            break
    check("demo raises Watch, Warning, Danger", {"raised to Watch", "raised to Warning", "raised to Danger"} <= seen, sorted(seen))
finally:
    proc.terminate()
print(f"{fails} FAILURES" if fails else "LIVE SERVER TEST PASSED")
sys.exit(1 if fails else 0)
