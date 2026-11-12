# 09_dashboard: the FloodGuard dashboard

* **Live:** double-click `START_FLOODGUARD.bat` in the project folder. The page opens at
  http://localhost:8765 and updates by itself every 30 minutes (river levels, forecast, flood map, areas at
  risk, alerts).
* **Saved copy:** open `FloodGuard_Live.html` in Chrome or Edge. It shows the run it was built from (river
  readings up to 21 Oct 2026) and all validation results, and says that it is not live.

Files: `FloodGuard_Live.html` (the page), `engine_assets.json` (all models, read by the page and by the live
service), `live_static.json` (thresholds, rain points, what-if area table and demo replays for the service).
Rebuild with `python scripts/realtime/build_dashboard.py`.
