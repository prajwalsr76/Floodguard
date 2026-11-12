# FloodGuard warning (hindcast) - valid 2023-09-16, issued 2023-09-15

| Station | Forecast max (m) | Warning level (m) | Status |
|---|---|---|---|
| Naraj | 26.28 | 25.41 | ABOVE WARNING |
| Kishan Nagar | 13.84 | 14.36 | below |
| Tarpur | 9.45 | 9.78 | below |

| Risk class | Area (km²) |
|---|---|
| Watch (0.5 x thr - thr) | 13.0 |
| Warning (thr - 0.9) | 0.0 |
| Severe (>= 0.9) | 23.8 |

Main factors raising risk in high-risk cells (mean SHAP, log-odds): dist_river_m +1.33, jrc_occurrence +1.27, lc_bare_frac +1.07, lc_water_frac +0.34, slope_deg +0.24

Verification against Sentinel-1 (2023-09-16): F1 0.798, IoU 0.664, ROC-AUC 0.997

_Research prototype - not an official CWC/OSDMA warning._