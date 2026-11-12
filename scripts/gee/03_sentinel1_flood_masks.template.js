/**** FloodGuard GEE 03 - Sentinel-1 inundation labels + event rainfall (Stage-2 labels)
 *
 * GENERATED from 00_project/aoi/flood_events.csv by scripts/gee/render_event_script.py.
 * Do not edit the EVENTS list by hand - regenerate it.
 *
 * Method (conventional change detection, UN-SPIDER recommended practice; no CNN):
 *   post  = S1 IW GRD VV mosaic from the acquisition date closest to the Naraj peak
 *           (within +/- MAX_DAYS), one relative orbit.
 *   pre   = median VV of the SAME relative orbit over the dry season (Jan-Apr) of that year.
 *   both  speckle-filtered with a 50 m focal median.
 *   flood = (post - pre < -DIFF_DB) AND (post < WATER_DB)
 *           AND NOT permanent water (JRC seasonality >= 10 months)
 *           AND slope < 5 deg.
 *   water = post < WATER_DB (all open water incl. channel - kept as a separate band).
 *   valid = pixels covered by both pre and post.
 * Bands exported per event (30 m, EPSG:32645):
 *   flood, water, valid, chirps_sum1, chirps_sum3, chirps_sum7  (CHIRPS totals ending the day BEFORE the S1 date)
 * A CSV with the actual S1 acquisition date per event is exported too: the Stage-2
 * label must be paired with the river level ON THAT DATE, not on the peak date.
 *
 * ALWAYS inspect each map visually against the NRSC/Bhuvan flood map where one
 * exists for the same event, and record accepted/rejected events in
 * 00_project/aoi/flood_events_qc.csv.
 ****/
var AOI_ASSET = 'projects/YOUR_PROJECT/assets/floodguard_aoi_floodplain';
var MAX_DAYS = 6, DIFF_DB = 3, WATER_DB = -16;
var EVENTS = /*EVENTS_JSON*/[];

var aoi = ee.FeatureCollection(AOI_ASSET).geometry();
var region = aoi.buffer(2000).bounds();
var CRS = 'EPSG:32645';
var slope = ee.Terrain.slope(ee.Image('USGS/SRTMGL1_003'));
var permanent = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('seasonality').unmask(0).gte(10);
var chirps = ee.ImageCollection('UCSB-CHC/CHIRPS/V3/DAILY_SAT').select('precipitation');

var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(aoi)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .select('VV');

function smooth(img) { return img.focal_median(50, 'circle', 'meters'); }

function eventImage(ev) {
  var peak = ee.Date(ev.peak_date);
  var cand = s1.filterDate(peak.advance(-MAX_DAYS, 'day'), peak.advance(MAX_DAYS + 1, 'day'))
    .map(function (i) { return i.set('dt', ee.Number(i.date().difference(peak, 'day')).abs()); })
    .sort('dt');
  // Events with no S1 scene in the window: the image export task fails (expected,
  // drop that event); the metadata table below still exports with n_candidates = 0.
  var first = ee.Image(cand.first());
  var orbit = first.get('relativeOrbitNumber_start');
  var day = ee.Date(first.date().format('YYYY-MM-dd'));
  var post = cand.filter(ee.Filter.eq('relativeOrbitNumber_start', orbit))
    .filterDate(day, day.advance(1, 'day'))
    .mosaic();
  var yr = peak.get('year');
  var pre = s1.filter(ee.Filter.eq('relativeOrbitNumber_start', orbit))
    .filterDate(ee.Date.fromYMD(yr, 1, 1), ee.Date.fromYMD(yr, 5, 1)).median();
  post = smooth(post); pre = smooth(pre);
  var water = post.lt(WATER_DB);
  var flood = post.subtract(pre).lt(-DIFF_DB).and(water).and(permanent.not()).and(slope.lt(5));
  var valid = post.mask().and(pre.mask()).rename('valid');
  // Rainfall totals END THE DAY BEFORE the S1 image: at forecast time (issue day t,
  // target t+1) rainfall is only known up to day t. This keeps training consistent.
  var rain = function (n) {
    return chirps.filterDate(day.advance(-n, 'day'), day).sum()
      .rename('chirps_sum' + n);
  };
  var img = flood.unmask(0).rename('flood').addBands(water.unmask(0).rename('water'))
    .addBands(valid.unmask(0)).addBands(rain(1)).addBands(rain(3)).addBands(rain(7))
    .clip(region);
  return {img: img, s1_date: day.format('YYYY-MM-dd'), orbit: orbit, n: cand.size(),
          dt: first.get('dt'), pass: first.get('orbitProperties_pass')};
}

function eventMeta(ev) {
  var peak = ee.Date(ev.peak_date);
  var cand = s1.filterDate(peak.advance(-MAX_DAYS, 'day'), peak.advance(MAX_DAYS + 1, 'day'))
    .map(function (i) { return i.set('dt', ee.Number(i.date().difference(peak, 'day')).abs()); })
    .sort('dt');
  var f = ee.Feature(null, {event_id: ev.event_id, peak_date: ev.peak_date, n_candidates: cand.size()});
  return ee.Feature(ee.Algorithms.If(cand.size().gt(0),
    f.set({s1_date: ee.Image(cand.first()).date().format('YYYY-MM-dd'),
           days_from_peak: ee.Image(cand.first()).get('dt'),
           relative_orbit: ee.Image(cand.first()).get('relativeOrbitNumber_start'),
           pass: ee.Image(cand.first()).get('orbitProperties_pass')}),
    f.set({s1_date: '', days_from_peak: -1})));
}

var meta = [];
EVENTS.forEach(function (ev) {
  var r = eventImage(ev);
  meta.push(eventMeta(ev));
  Export.image.toDrive({image: r.img.toFloat(), description: 'flood_' + ev.event_id,
    folder: 'FloodGuard_GEE', region: region, scale: 30, crs: CRS, maxPixels: 1e10});
});
var metaFC = ee.FeatureCollection(meta);
print('Event / Sentinel-1 pairing (check days_from_peak):', metaFC);
Export.table.toDrive({collection: metaFC, description: 'floodguard_s1_event_dates',
  folder: 'FloodGuard_GEE', fileFormat: 'CSV'});

// visual check of the biggest event
var big = EVENTS.filter(function (e) { return e.event_id === 'E20220817'; })[0] || EVENTS[0];
var vis = eventImage(big);
Map.centerObject(aoi, 10);
Map.addLayer(vis.img.select('water').selfMask(), {palette: ['#6baed6']}, 'water ' + big.event_id);
Map.addLayer(vis.img.select('flood').selfMask(), {palette: ['#d7301f']}, 'flood ' + big.event_id);
Map.addLayer(ee.FeatureCollection(AOI_ASSET), {color: 'black'}, 'AOI', false);
