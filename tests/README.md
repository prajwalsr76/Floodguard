# tests (run from the project root)

* `node tests/engine_parity.mjs`: the dashboard's in-browser engine gives the same answers as the Python models
  on four issue days (forecasts, ranges, every one of 219,017 map cells, areas, village classes, SHAP).
* `python tests/server_engine_parity.py`: the live service's Stage 1 engine gives the same answers as Python.
* `python tests/test_live_server.py`: starts the live service offline on recorded CWC and Open-Meteo answers
  (`fixtures/`, 21 Oct 2026 15:00 IST), checks the automatic update (Naraj 22.003 m), that the page is served,
  and that a demo replay raises Watch, Warning and Danger alerts by itself (about 3 minutes).
* `fixture_reference.py`: Python reference for the recorded live answers.
