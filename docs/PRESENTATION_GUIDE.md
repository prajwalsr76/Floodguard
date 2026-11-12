# Presenting FloodGuard (about 10 minutes)

## Before the presentation (the day before)

1. Double-click `START_FLOODGUARD.bat`. The dashboard opens at http://localhost:8765 and the connection
   pill at the top says **Live** with the time of the last CWC reading.
2. Connect Telegram from the dashboard (*Alerts and data* → *Get alerts on your phone*, 2 minutes,
   `docs/ALERTS_SETUP.md`) and press **Send a test alert**. Ask examiners to press Start in the bot
   before the demo if you want their phones to buzz too.
3. Run the checks once (2 minutes), so you can say they pass:
   ```
   python tests/server_engine_parity.py
   python tests/test_live_server.py
   ```
4. Keep the laptop on and online. If the venue has no internet, the page still opens with the last
   readings and says how old they are; the demo replay works offline.

## Slides and report

* The presentation deck (14 slides with speaker notes) follows the order below; it can be downloaded as
  PowerPoint or PDF from its page.
* The full report is `00_project/FloodGuard_Project_Context_Master_v5.docx` (PDF copy beside it).

## The demo

**1. It is live (1 min).** Point at the pill: *Live, CWC reading … IST, next update …*. Say: "Every
30 minutes FloodGuard reads the four CWC gauges and the rainfall over the catchment, runs both models
and updates this page. Nobody presses anything." Open *Alerts and data*: data health shows each gauge's
last reading and age; the run log is in `08_results/live/run_log.csv`.

**2. Today (1 min).** The status band reads *Normal* in October. "If a gauge stops reporting for six
hours the band turns grey, *Data delayed*: we never show Normal from old data."

**3. A real flood, replayed through the live system (4 min).** Press **Demo flood → July–August 2026**.
One day passes every 12 seconds, with the real CWC readings and rain of those days and the same models
and alert rules as live:
* 22 Jul: **Watch** (yellow), first alert sent.
* 24 Jul: **Warning** (orange). Areas at risk fill in by name: Nidhipur, Nemalo, Cuttack, Barang…
* 29 Jul evening: **Danger** (red) for 30 Jul. The real peak was 26.61 m on 30 Jul, above the danger level.
* In the river chart, the green dot "what happened" shows the gauge reading the next day.
* If Telegram is set up, the phone buzzes with each alert (marked DEMO).
Click an area in *Areas at risk* to outline it on the map. Press **Stop demo** to return to live.

**4. Why trust it (3 min).** Tab *2026 season test*: the models never saw 2026; 20 of 22 warning days
caught at Naraj, a Watch at least 24 h ahead at all 17 flood onsets. Radar check: F1 0.77 / 0.73 / 0.78.
Tab *Accuracy 2015–2025*: the 2022 record flood is now mapped at 82% of its area (was 57%).

**5. Limits (1 min).** Tab *How it works*: derived levels at Kishan Nagar and Tarpur, no Hirakud release
input, record floods still a little small, research prototype.

## Questions you are likely to get

* **How is this different from CWC or Google Flood Hub?** Do not claim "first". CWC already forecasts
  Naraj, and Google's flood forecasting is live across India with inundation maps. FloodGuard's own
  contribution: 100 m maps for this delta that explain why each area is at risk (SHAP), areas named by
  village with the Naraj level at which water reaches them, 48 h and 72 h outlooks with a stated
  uncertainty, every result checked against local Sentinel-1 radar including an unseen year, and a
  fully open, reproducible system that anyone can run on a laptop.
* **Did you tune on 2026?** No. Models stop at 2025. The decision to retrain was made before scoring 2026,
  and the old-versus-new comparison is reported in full, including where it got worse (Kishan Nagar
  all-year error).
* **Why the ensemble?** As accurate as the best single model at Naraj (0.107 m), steadier across gauges.
* **Why is built-up land rarely flooded on the map?** Radar cannot see water between buildings, so the
  training maps rarely mark it; the page therefore reports homes and buildings within 500 m of flood-likely land.
* **What if the internet or CWC is down?** The page shows *Data delayed*, the last readings stay
  visible with their age, no "All clear" is sent, and it recovers by itself.
