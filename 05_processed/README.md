# 05_processed: cleaned water levels

* `water_level/manual/daily_water_level_cleaned.csv`: daily table from the team Drive (v1 processing).
* `water_level/manual/daily_water_level_qc2.csv`: after QC v2 (`scripts/processing/fix_daily_water_level.py`). **All models use this file.**
* `water_level/manual/qc2_log.csv`: every value QC v2 removed or changed, and why.
* `water_level/telemetry/`: the telemetry series, rejected because many readings sit near 1,000 m. Kept for reference only.
