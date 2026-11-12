/**** FloodGuard GEE 01 - CHIRPS v3 daily rainfall per sub-catchment (Stage 1 features)
 *
 * BEFORE RUNNING
 *   1. Upload 00_project/aoi/gee_upload/rain_zones.zip as a table asset
 *      (Assets -> NEW -> Shape files). Set ZONES_ASSET below to its path.
 *   2. Run. In the Tasks tab click RUN on the export.
 *   3. Download the CSV from Drive/FloodGuard_GEE and save it as
 *      06_features/chirps_zone_daily_raw.csv, then run
 *      python scripts/stage1/prepare_chirps.py
 *
 * Output columns: date, zone, rain_mm  (area-weighted mean over each zone)
 * Product: UCSB-CHC/CHIRPS/V3/DAILY_SAT (CHIRPS v3 pentads disaggregated to days
 * with IMERG Late V07). DAILY_RNL (ERA5-based) is the alternative; keep one
 * product for the whole study and state which in the report.
 ****/
var ZONES_ASSET = 'projects/YOUR_PROJECT/assets/floodguard_rain_zones';
var START = '2014-01-01', END = '2026-01-01';

var zones = ee.FeatureCollection(ZONES_ASSET);
var chirps = ee.ImageCollection('UCSB-CHC/CHIRPS/V3/DAILY_SAT')
  .filterDate(START, END).select('precipitation');

var perDay = chirps.map(function (img) {
  var date = img.date().format('YYYY-MM-dd');
  return img.reduceRegions({collection: zones, reducer: ee.Reducer.mean(), scale: 5566})
    .map(function (f) {
      return ee.Feature(null, {date: date, zone: f.get('zone'), rain_mm: f.get('mean')});
    });
}).flatten();

print('Rows (should be days x 4 zones):', perDay.size());
Map.centerObject(zones, 6);
Map.addLayer(chirps.filterDate('2022-08-10', '2022-08-17').sum().clip(zones.geometry().bounds()),
             {min: 0, max: 400, palette: ['white', 'blue', 'purple']}, 'CHIRPS 10-17 Aug 2022');
Map.addLayer(zones, {color: 'red'}, 'zones');

Export.table.toDrive({
  collection: perDay, description: 'floodguard_chirps_zone_daily',
  folder: 'FloodGuard_GEE', fileFormat: 'CSV', selectors: ['date', 'zone', 'rain_mm']
});
