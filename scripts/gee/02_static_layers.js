/**** FloodGuard GEE 02 - static spatial layers for the Stage-2 AOI
 *
 * Upload 00_project/aoi/gee_upload/aoi_floodplain.zip as a table asset, set
 * AOI_ASSET, run, then RUN every task in the Tasks tab. Put the GeoTIFFs in
 * 01_raw/spatial/ with the names given in `description`.
 *
 *  dem_srtm30.tif           SRTM GL1 v3 elevation (m, EGM96)            30 m
 *  worldcover2021.tif       ESA WorldCover v200 (2021) class codes      10 m
 *  jrc_water.tif            JRC GSW v1.4: band1 occurrence (%), band2 seasonality (months)  30 m
 *  chirps_cell_template.tif one CHIRPS day, to record the native 0.05 deg grid
 *
 * Slope, distance-to-river, height-above-river and the 100 m modelling grid are
 * derived locally by scripts/stage2/build_grid.py (reproducible, no GEE needed).
 * The river/channel network is taken from JRC occurrence >= 50 % (captures the
 * braided Mahanadi / Kathajodi / Birupa / Devi channels); the CWC river polygon
 * or HydroRIVERS can be used instead (see build_grid.py --river).
 ****/
var AOI_ASSET = 'projects/YOUR_PROJECT/assets/floodguard_aoi_floodplain';
var aoi = ee.FeatureCollection(AOI_ASSET).geometry();
var region = aoi.buffer(2000).bounds();     // small margin so edge cells are complete
var CRS = 'EPSG:32645';

var dem = ee.Image('USGS/SRTMGL1_003').select('elevation').clip(region);
var wc = ee.ImageCollection('ESA/WorldCover/v200').first().select('Map').clip(region);
var jrc = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select(['occurrence', 'seasonality'])
  .unmask(0).clip(region);
var chirps = ee.ImageCollection('UCSB-CHC/CHIRPS/V3/DAILY_SAT').filterDate('2022-08-15', '2022-08-16')
  .first().select('precipitation').clip(region);

Map.centerObject(aoi, 10);
Map.addLayer(dem, {min: 0, max: 60, palette: ['#0b4f6c', '#9ad1d4', '#f4e285', '#8c510a']}, 'DEM');
Map.addLayer(jrc.select('occurrence').selfMask(), {min: 0, max: 100, palette: ['#c6dbef', '#08306b']}, 'JRC occurrence');
Map.addLayer(ee.FeatureCollection(AOI_ASSET), {color: 'red'}, 'AOI');

function exp(img, name, scale, type) {
  Export.image.toDrive({image: type === 'int' ? img.toInt16() : img.toFloat(), description: name,
    folder: 'FloodGuard_GEE', region: region, scale: scale, crs: CRS, maxPixels: 1e10});
}
exp(dem, 'dem_srtm30', 30, 'float');
exp(wc, 'worldcover2021', 10, 'int');
exp(jrc, 'jrc_water', 30, 'int');
Export.image.toDrive({image: chirps.toFloat(), description: 'chirps_cell_template', folder: 'FloodGuard_GEE',
  region: region, crs: 'EPSG:4326', crsTransform: [0.05, 0, -180, 0, -0.05, 50], maxPixels: 1e9});
