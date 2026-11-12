// Parity test: browser engine (engine.js) vs the Python real-time chain.
// Run from the project root:  node tests/engine_parity.mjs
import fs from "fs"; import zlib from "zlib"; import path from "path"; import { fileURLToPath } from "url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const R = path.join(ROOT, "08_results", "realtime") + "/";
eval(fs.readFileSync(path.join(ROOT, "scripts", "realtime", "engine.js"), "utf8"));
const FG = globalThis.FGEngine;
const A = JSON.parse(fs.readFileSync(R + "engine_assets.json", "utf8"));
const REF = JSON.parse(fs.readFileSync(R + "parity_reference.json", "utf8"));
const DD = JSON.parse(fs.readFileSync(R + "dashboard_data.json", "utf8"));
const t0 = Date.now(); const E = await FG.load(A); console.log("load ms", Date.now() - t0);
let fails = 0; const chk = (name, ok, info) => { if (!ok) { fails++; console.log("FAIL", name, info ?? ""); } };
for (const C of REF) {
  const daily = {}; for (const st of Object.keys(C.daily)) { daily[st] = {}; for (const [d, m, x, n] of C.daily[st]) daily[st][d] = [m, x, n]; }
  const rain = {}; for (const z of Object.keys(C.rain)) { rain[z] = {}; for (const [d, v] of C.rain[z]) rain[z][d] = v; }
  const out = {};
  for (const st of ["Naraj", "Kishan Nagar", "Tarpur"]) {
    const r = FG.stage1(E, st, daily, rain, C.issue, DD.thresholds[st]); const p = C.stage1[st]; out[st] = r;
    chk(`${C.issue} ${st} fc`, Math.abs(r.fc - p.fc) < 1e-5, [r.fc, p.fc]);
    chk(`${C.issue} ${st} lo/hi`, Math.abs(r.lo - p.lo) < 1e-5 && Math.abs(r.hi - p.hi) < 1e-5, [r.lo, p.lo, r.hi, p.hi]);
    chk(`${C.issue} ${st} p`, Math.abs(r.p_warn - p.p_warn) < 1e-9, [r.p_warn, p.p_warn]);
    chk(`${C.issue} ${st} status`, r.status === p.status, [r.status, p.status]);
    if (p.outlook) for (const k of ["+48h", "+72h"]) { chk(`${C.issue} ${st} ${k}`, Math.abs(r.outlook[k].fc - p.outlook[k].fc) < 0.006 && Math.abs(r.outlook[k].p_warn - p.outlook[k].p_warn) < 1e-3, [r.outlook[k], p.outlook[k]]); }
    if (p.members) for (const k of Object.keys(p.members)) chk(`${C.issue} ${st} member ${k}`, Math.abs(r.members[k] - p.members[k]) < 0.006, [r.members[k], p.members[k]]);
  }
  const lv = { Naraj: C.levels.Naraj, "Kishan Nagar": C.levels["Kishan Nagar"], Tarpur: C.levels.Tarpur };
  const rs = { 1: C.rain_sums["1"], 3: C.rain_sums["3"], 7: C.rain_sums["7"] };
  const t1 = Date.now(); const res = await FG.stage2(E, lv, rs); const ms = Date.now() - t1;
  const c = FG.classes(E, res.prob, lv.Naraj); const cref = new Uint8Array(zlib.gunzipSync(Buffer.from(C.classes, "base64")));
  let diff = 0; for (let i = 0; i < c.length; i++) if (c[i] !== cref[i]) diff++;
  const ar = FG.areas(c);
  chk(`${C.issue} classes`, diff === 0, diff + " cells differ");
  for (const k of ["km2_likely", "km2_elevated", "km2_watch"]) chk(`${C.issue} ${k}`, ar[k] === C.summary[k], [ar[k], C.summary[k]]);
  const pl = FG.placeClasses(E, c, DD.grid.places); let pd = 0; for (const k of Object.keys(C.places)) if (pl[k] !== C.places[k]) pd++;
  chk(`${C.issue} places`, pd === 0, pd);
  const t2 = Date.now(); const sh = await FG.shapMean(E, res, c); const ms2 = Date.now() - t2;
  for (const s of C.summary.shap) { const m = sh.find(x => x.feature === s.feature); chk(`${C.issue} shap ${s.feature}`, m && Math.abs(m.value - s.value) <= 0.0015, [m && m.value, s.value]); }
  console.log(C.issue, "stage2 ms", ms, "shap ms", ms2, ar, "fc", ["Naraj","Kishan Nagar","Tarpur"].map(s => out[s].fc.toFixed(3)).join(" "));
}
console.log(fails ? `${fails} FAILURES` : "ALL PARITY CHECKS PASSED");
