# 01_raw: inputs as downloaded (do not edit)

* `spatial/gee_downloads/`: the original Earth Engine exports (DEM, WorldCover, JRC water, CHIRPS zone rainfall, five event-raster bundles, Sentinel-1 dates).
* `spatial/dem_srtm30.tif`, `worldcover2021.tif`, `jrc_water.tif`: static layers unpacked from them.
* `spatial/events/`: one raster per Sentinel-1 scene 2015–2025 (flood, water, valid, CHIRPS 1/3/7-day rain) and `floodguard_s1_event_dates.csv`.
* `spatial/events_2026/`: Sentinel-1 flood and coverage percentages on the 100 m grid for 2 Aug, 28 Aug and 1 Oct 2026 (array 435 x 767 x 2).
* `live/`: the last live fetch: CWC daily levels (Dec 2024–Oct 2026) and the last 7 days hourly, Open-Meteo rainfall per zone, OpenStreetMap settlements.
* `river/basin/`: CWC Mahanadi basin boundary.
