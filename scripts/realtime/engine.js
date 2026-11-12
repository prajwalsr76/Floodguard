/* FloodGuard browser engine.
 * Evaluates the frozen Stage 1 and Stage 2 models exactly as exported by
 * export_js_models.py (see the arithmetic contract there). Pure functions, no DOM:
 * the same file runs in the dashboard and in the Node parity test.
 */
(function (root) {
  "use strict";
  const f32 = Math.fround;

  /* ---------- decoding ---------- */
  function b64bytes(s) {
    if (typeof atob === "function") { const bin = atob(s); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }
    return new Uint8Array(Buffer.from(s, "base64"));
  }
  async function gunzip(u8) {
    const ds = new DecompressionStream("gzip");
    const stream = new Blob([u8]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  async function arr(s, Type, gz) {
    let u = b64bytes(s);
    if (gz) u = await gunzip(u);
    const buf = u.buffer.slice(u.byteOffset, u.byteOffset + u.byteLength);
    return new Type(buf);
  }
  async function xgbModel(m, gz) {
    return { margin: m.margin, n: m.n_trees, left: await arr(m.left, Int32Array, gz), right: await arr(m.right, Int32Array, gz),
      feat: await arr(m.feat, Int16Array, gz), cond: await arr(m.cond, Float32Array, gz), dleft: await arr(m.dleft, Uint8Array, gz),
      start: await arr(m.start, Int32Array, gz) };
  }

  async function load(A) {
    const E = { stage1: {}, stage2: null };
    for (const st of Object.keys(A.stage1)) {
      const s = A.stage1[st];
      const H = {};
      for (const h of Object.keys(s.h)) {
        H[h] = { xgb: await xgbModel(s.h[h].xgb, true),
          res: { normal: Array.from(await arr(s.h[h].res_normal, Int32Array, false), v => v / 1e4),
                 high: Array.from(await arr(s.h[h].res_high, Int32Array, false), v => v / 1e4) } };
      }
      E.stage1[st] = { features: s.features, fill: s.fill, ridge: s.ridge, xgb: await xgbModel(s.xgb, true),
        rf: { n: s.rf.n_trees, feat: await arr(s.rf.feat, Uint8Array, true), val: await arr(s.rf.val, Float32Array, true),
              roff: await arr(s.rf.roff, Uint16Array, true), start: await arr(s.rf.start, Int32Array, false) },
        res: { normal: Array.from(await arr(s.res_normal, Int32Array, false), v => v / 1e4),
               high: Array.from(await arr(s.res_high, Int32Array, false), v => v / 1e4) },
        h: H };
    }
    const s2 = A.stage2;
    const bins = {};
    for (const f of s2.static) bins[f] = await arr(s2.bins[f].data, s2.bins[f].dtype === "u16" ? Uint16Array : Uint8Array, true);
    const conds = {}; for (const f of s2.features) conds[f] = Float32Array.from(s2.conds[f]);
    const mask = await arr(s2.mask, Uint8Array, true);
    const rows = [], cols = [];
    for (let i = 0; i < mask.length; i++) if (mask[i]) { rows.push(Math.floor(i / s2.w)); cols.push(i % s2.w); }
    E.stage2 = { features: s2.features, static: s2.static, margin: s2.margin, n: s2.n_trees, conds, bins,
      left: await arr(s2.left, Int32Array, true), right: await arr(s2.right, Int32Array, true), feat: await arr(s2.feat, Int16Array, true),
      kidx: await arr(s2.kidx, Int32Array, true), leaf: await arr(s2.leaf, Float32Array, true), dleft: await arr(s2.dleft, Uint8Array, true),
      hess: await arr(s2.hess, Float32Array, true), start: await arr(s2.start, Int32Array, true),
      elev: await arr(s2.elev, Float32Array, true), near: await arr(s2.near, Uint8Array, true), river: await arr(s2.river, Uint8Array, true),
      refcls: await arr(s2.refcls, Uint8Array, true), rows: Int32Array.from(rows), cols: Int32Array.from(cols),
      w: s2.w, h: s2.h, pLikely: s2.p_likely, nCells: rows.length,
      cut: s2.cut || { c0: s2.p_likely, slope: 0, floor: s2.p_likely, pivot: 0 },
      conn: !!s2.connectivity, touch: s2.touch ? await arr(s2.touch, Uint8Array, true) : null };
    const idx = new Int32Array(s2.w * s2.h).fill(-1);
    for (let i = 0; i < rows.length; i++) idx[rows[i] * s2.w + cols[i]] = i;
    E.stage2.cellAt = idx;
    return E;
  }

  /* ---------- dates ---------- */
  const DAY = 86400000;
  const toT = s => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
  const toS = t => new Date(t).toISOString().slice(0, 10);
  const addD = (s, k) => toS(toT(s) + k * DAY);
  function dayOfYear(s) { const t = toT(s); return Math.round((t - Date.UTC(+s.slice(0, 4), 0, 1)) / DAY) + 1; }

  /* ---------- live data helpers ---------- */
  const round2 = x => Math.floor(x * 100 + 0.5) / 100;           // same rule as fetch_live_inputs.py
  /** hourly [[isoTime, level]] -> {date: [mean, max, min, n]} */
  function dailyFromHourly(H) {
    const g = {};
    for (const [t, v] of H) { if (v === null || !isFinite(v)) continue; const d = t.slice(0, 10); (g[d] = g[d] || []).push(v); }
    const out = {};
    for (const d of Object.keys(g).sort()) { const a = g[d]; let s = 0, mx = -Infinity, mn = Infinity; for (const v of a) { s += v; if (v > mx) mx = v; if (v < mn) mn = v; } out[d] = [round2(s / a.length), mx, mn, a.length]; }
    return out;
  }

  /* ---------- Stage 1 features (mirror of build_features.station_frame / rainfall_features) ---------- */
  const slug = s => s.toLowerCase().replace(/ /g, "_");
  function stageFeatures(features, daily, rain, t) {
    // daily: {station: {date: [mean, max, min]}}; rain: {zone: {date: mm}}
    const byslug = {}; for (const st of Object.keys(daily)) byslug[slug(st)] = daily[st];
    const get = (s, d, k) => { const r = byslug[s] && byslug[s][d]; const v = r ? r[k] : undefined; return (v === undefined || v === null || !isFinite(v)) ? NaN : v; };
    const rget = (z, d) => { const v = rain[z] ? rain[z][d] : undefined; return (v === undefined || v === null || !isFinite(v)) ? NaN : v; };
    const x = new Float64Array(features.length);
    for (let j = 0; j < features.length; j++) {
      const f = features[j];
      const [grp, rest] = f.split("__");
      let v = NaN, m;
      if (grp === "own" || grp === "up") {
        m = rest.match(/^(.*)_(mean_lag\d+|max_lag\d+|min_lag0|chg1|chg2|maxchg1|rmax3|rmax7|intraday_range)$/);
        const s = m[1], q = m[2];
        if ((m = q.match(/^(mean|max)_lag(\d+)$/))) v = get(s, addD(t, -+m[2]), m[1] === "mean" ? 0 : 1);
        else if (q === "min_lag0") v = get(s, t, 2);
        else if (q === "chg1") v = get(s, t, 0) - get(s, addD(t, -1), 0);
        else if (q === "chg2") v = get(s, t, 0) - get(s, addD(t, -2), 0);
        else if (q === "maxchg1") v = get(s, t, 1) - get(s, addD(t, -1), 1);
        else if (q === "rmax3" || q === "rmax7") { const n = q === "rmax3" ? 3 : 7; let mx = -Infinity, ok = true; for (let k = 0; k < n; k++) { const a = get(s, addD(t, -k), 1); if (isNaN(a)) { ok = false; break; } if (a > mx) mx = a; } v = ok ? mx : NaN; }
        else if (q === "intraday_range") v = get(s, t, 1) - get(s, t, 2);
      } else if (grp === "rain") {
        m = rest.match(/^(z\d_[a-z_]+?)_(d0|lag\d|sum\d+)$/);
        const z = m[1], q = m[2];
        if (q === "d0") v = rget(z, t);
        else if (q.startsWith("lag")) v = rget(z, addD(t, -+q.slice(3)));
        else { const n = +q.slice(3); let s = 0, ok = true; for (let k = n - 1; k >= 0; k--) { const a = rget(z, addD(t, -k)); if (isNaN(a)) { ok = false; break; } s += a; } v = ok ? s : NaN; }
      } else if (grp === "season") {
        const doy = dayOfYear(t), mo = +t.slice(5, 7);
        if (rest === "doy_sin") v = Math.sin(2 * Math.PI * doy / 365.25);
        else if (rest === "doy_cos") v = Math.cos(2 * Math.PI * doy / 365.25);
        else if (rest === "monsoon") v = (mo >= 6 && mo <= 10) ? 1 : 0;
      }
      x[j] = v;
    }
    return x;
  }

  function xgbPredict(M, x) {
    let s = M.margin;
    for (let k = 0; k < M.n; k++) {
      let n = M.start[k];
      const base = n;
      while (M.left[n] !== -1) {
        const v = x[M.feat[n]];
        const goLeft = isNaN(v) ? M.dleft[n] === 1 : f32(v) < M.cond[n];
        n = base + (goLeft ? M.left[n] : M.right[n]);
      }
      s += M.cond[n];
    }
    return s;
  }
  function rfPredict(R, x) {
    let acc = 0;
    for (let k = 0; k < R.n; k++) {
      let n = R.start[k];
      while (R.feat[n] !== 255) n = f32(x[R.feat[n]]) <= R.val[n] ? n + 1 : n + R.roff[n];
      acc += R.val[n];
    }
    return acc / R.n;
  }
  function ridgePredict(G, x) { let s = 0; for (let j = 0; j < x.length; j++) s += G.w[j] * x[j]; return G.b + s; }

  function quantile(sorted, q) {
    const n = sorted.length, pos = (n - 1) * q, i = Math.floor(pos), g = pos - i;
    if (i + 1 >= n) return sorted[n - 1];
    const a = sorted[i], b = sorted[i + 1];
    return g >= 0.5 ? b - (b - a) * (1 - g) : a + (b - a) * g;     // numpy 'linear' lerp
  }
  function interval(pred, base, warn, bank) {
    const reg = base >= warn - 2.0 ? "high" : "normal";
    let r = bank[reg];
    if (r.length < 30) r = bank.normal.concat(bank.high).sort((a, b) => a - b);
    let ex = 0; for (const e of r) if (pred + e >= warn) ex++;
    return { lo: pred + quantile(r, 0.1), hi: pred + quantile(r, 0.9), p: ex / r.length, regime: reg };
  }
  function statusOf(fc, hi, th) {
    if (fc >= th.danger) return "RED";
    if (fc >= th.warning) return "ORANGE";
    if (hi >= th.warning || fc >= th.warning - 0.5) return "YELLOW";
    return "GREEN";
  }

  /** Stage 1 for one gauge at issue day t. daily/rain as in stageFeatures; th = {warning, danger}. */
  function stage1(E, st, daily, rain, t, th) {
    const S = E.stage1[st];
    const x = stageFeatures(S.features, daily, rain, t);
    const base = x[S.features.indexOf(`own__${slug(st)}_max_lag0`)];
    if (isNaN(base)) return null;
    const xf = Float64Array.from(x, (v, j) => isNaN(v) ? S.fill[j] : v);
    const mX = base + xgbPredict(S.xgb, x), mR = base + rfPredict(S.rf, xf), mG = base + ridgePredict(S.ridge, xf);
    const fc = (mX + mR + mG) / 3;
    const iv = interval(fc, base, th.warning, S.res);
    const outlook = {};
    for (const h of ["2", "3"]) {
      const pr = base + xgbPredict(S.h[h].xgb, x);
      const o = interval(pr, base, th.warning, S.h[h].res);
      outlook["+" + (24 * h) + "h"] = { valid: addD(t, +h), fc: pr, lo: o.lo, hi: o.hi, p_warn: o.p };
    }
    return { issue: t, valid: addD(t, 1), today_max: base, fc, lo: iv.lo, hi: iv.hi, p_warn: iv.p, regime: iv.regime,
      members: { XGBoost: mX, RandomForest: mR, Ridge: mG }, outlook, status: statusOf(fc, iv.hi, th), nMissing: x.filter(isNaN).length };
  }

  /* ---------- Stage 2 ---------- */
  function upperBound(a, x) { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; } return lo; }
  const yieldNow = () => new Promise(r => setTimeout(r, 0));

  /** Probability for every AOI cell. lv = {Naraj, "Kishan Nagar", Tarpur}; rs = {1,3,7}. */
  async function stage2(E, lv, rs, onProgress) {
    const S = E.stage2, F = S.features, nf = F.length, N = S.nCells;
    const stIdx = F.map(f => S.static.indexOf(f));
    const iN = F.indexOf("lvl_naraj"), iNear = F.indexOf("lvl_near"), iDep = F.indexOf("depth_proxy_m");
    const iR = { 1: F.indexOf("rain_chirps_sum1"), 3: F.indexOf("rain_chirps_sum3"), 7: F.indexOf("rain_chirps_sum7") };
    const NA = { u8: 255, u16: 65535 };
    const binOf = (f, v) => isNaN(v) ? -1 : upperBound(S.conds[f], f32(v));
    const corridor = [lv.Naraj, lv["Kishan Nagar"], lv.Tarpur];
    const nearLv = corridor.map(v => (v === null || isNaN(v)) ? lv.Naraj : v);
    const bNaraj = binOf("lvl_naraj", lv.Naraj);
    const bNear = nearLv.map(v => binOf("lvl_near", v));
    const bRain = { 1: binOf("rain_chirps_sum1", rs[1]), 3: binOf("rain_chirps_sum3", rs[3]), 7: binOf("rain_chirps_sum7", rs[7]) };
    const statBins = S.static.map(f => S.bins[f]);
    const statNA = S.static.map(f => S.bins[f] instanceof Uint16Array ? NA.u16 : NA.u8);
    const prob = new Float64Array(N);
    const xb = new Int32Array(nf);
    const CH = 12000;
    for (let i0 = 0; i0 < N; i0 += CH) {
      const i1 = Math.min(N, i0 + CH);
      for (let i = i0; i < i1; i++) {
        for (let j = 0; j < nf; j++) {
          const si = stIdx[j];
          if (si >= 0) { const b = statBins[si][i]; xb[j] = b === statNA[si] ? -1 : b; }
        }
        const nr = S.near[i];
        xb[iN] = bNaraj; xb[iNear] = bNear[nr];
        xb[iDep] = binOf("depth_proxy_m", nearLv[nr] - S.elev[i]);
        xb[iR[1]] = bRain[1]; xb[iR[3]] = bRain[3]; xb[iR[7]] = bRain[7];
        let s = S.margin;
        for (let k = 0; k < S.n; k++) {
          let n = S.start[k]; const base = n;
          while (S.left[n] !== -1) {
            const b = xb[S.feat[n]];
            const goLeft = b < 0 ? S.dleft[n] === 1 : b <= S.kidx[n];
            n = base + (goLeft ? S.left[n] : S.right[n]);
          }
          s += S.leaf[n];
        }
        prob[i] = S.river[i] ? 0 : 1 / (1 + Math.exp(-s));
      }
      if (onProgress) onProgress(i1 / N);
      await yieldNow();
    }
    return { prob, rs, lv, nearLv };
  }

  /** flood-likely cut-off at a given Naraj level (level-aware rule, see improve_stage2.py) */
  function likelyCut(E, naraj) {
    const C = E.stage2.cut;
    const L = (naraj === null || naraj === undefined || isNaN(naraj)) ? C.pivot : naraj;
    return Math.max(C.floor, C.c0 - C.slope * Math.max(0, L - C.pivot));
  }
  /** keep[i] = 1 if cell i belongs to an 8-connected patch of mask cells that touches the river */
  function riverConnected(E, mask) {
    const S = E.stage2, W = S.w, H = S.h, keep = new Uint8Array(S.nCells), seen = new Uint8Array(S.nCells);
    const stack = new Int32Array(S.nCells), comp = new Int32Array(S.nCells);
    for (let s0 = 0; s0 < S.nCells; s0++) {
      if (!mask[s0] || seen[s0]) continue;
      let top = 0, n = 0, touches = false;
      stack[top++] = s0; seen[s0] = 1;
      while (top > 0) {
        const i = stack[--top]; comp[n++] = i;
        if (S.touch[i]) touches = true;
        const r = S.rows[i], q = S.cols[i];
        for (let dr = -1; dr <= 1; dr++) for (let dq = -1; dq <= 1; dq++) {
          if (!dr && !dq) continue;
          const rr = r + dr, qq = q + dq;
          if (rr < 0 || qq < 0 || rr >= H || qq >= W) continue;
          const j = S.cellAt[rr * W + qq];
          if (j >= 0 && mask[j] && !seen[j]) { seen[j] = 1; stack[top++] = j; }
        }
      }
      if (touches) for (let k = 0; k < n; k++) keep[comp[k]] = 1;
    }
    return keep;
  }
  /** raw class per cell (before removing normal monsoon water): 0 none, 1 watch, 2 elevated, 3 likely.
   *  Flood-likely patches that do not touch the river are shown as elevated (the model was trained
   *  on river-connected floods only). */
  function rawClasses(E, prob, naraj) {
    const S = E.stage2, pl = likelyCut(E, naraj), c2 = Math.min(0.60, pl), c = new Uint8Array(S.nCells), m = new Uint8Array(S.nCells);
    for (let i = 0; i < S.nCells; i++) {
      if (S.river[i]) continue;
      const p = prob[i];
      c[i] = p >= pl ? 3 : p >= c2 ? 2 : p >= 0.30 ? 1 : 0;
      if (c[i] === 3) m[i] = 1;
    }
    if (S.conn && S.touch) { const k = riverConnected(E, m); for (let i = 0; i < S.nCells; i++) if (c[i] === 3 && !k[i]) c[i] = 2; }
    return c;
  }
  function classes(E, prob, naraj) {
    const S = E.stage2, c = rawClasses(E, prob, naraj);
    for (let i = 0; i < S.nCells; i++) if (!(c[i] > S.refcls[i])) c[i] = 0;
    return c;
  }
  const km2 = n => Math.floor(n / 10 + 0.5) / 10;
  function areas(c) { const k = [0, 0, 0, 0]; for (const v of c) k[v]++; return { km2_watch: km2(k[1]), km2_elevated: km2(k[2]), km2_likely: km2(k[3]) }; }
  function grid(E, c) { const S = E.stage2, g = new Uint8Array(S.w * S.h); for (let i = 0; i < S.nCells; i++) g[S.rows[i] * S.w + S.cols[i]] = c[i]; return g; }
  function placeClasses(E, c, places) {
    const S = E.stage2, g = grid(E, c), out = {};
    for (const p of places) { let mx = 0; for (let r = Math.max(0, p.row - 5); r <= Math.min(S.h - 1, p.row + 5); r++) for (let q = Math.max(0, p.col - 5); q <= Math.min(S.w - 1, p.col + 5); q++) { const v = g[r * S.w + q]; if (v > mx) mx = v; } out[p.name] = mx; }
    return out;
  }

  /* ---------- Tree SHAP (path-dependent, Lundberg et al. 2018, Algorithm 2) ---------- */
  function treeShapOne(S, k, xb, phi) {
    const base = S.start[k];
    function extend(m, pz, po, pi) {
      const l = m.length; const nm = m.map(e => ({ d: e.d, z: e.z, o: e.o, w: e.w }));
      nm.push({ d: pi, z: pz, o: po, w: l === 0 ? 1 : 0 });
      for (let i = l - 1; i >= 0; i--) { nm[i + 1].w += po * nm[i].w * (i + 1) / (l + 1); nm[i].w = pz * nm[i].w * (l - i) / (l + 1); }
      return nm;
    }
    function unwind(m, i) {
      const l = m.length - 1; let n = m[l].w; const nm = m.map(e => ({ d: e.d, z: e.z, o: e.o, w: e.w }));
      for (let j = l - 1; j >= 0; j--) {
        if (m[i].o !== 0) { const t = nm[j].w; nm[j].w = n * (l + 1) / ((j + 1) * m[i].o); n = t - nm[j].w * m[i].z * (l - j) / (l + 1); }
        else nm[j].w = nm[j].w * (l + 1) / (m[i].z * (l - j));
      }
      for (let j = i; j < l; j++) { nm[j].d = nm[j + 1].d; nm[j].z = nm[j + 1].z; nm[j].o = nm[j + 1].o; }
      nm.pop(); return nm;
    }
    function rec(j, m, pz, po, pi) {
      m = extend(m, pz, po, pi);
      const n = base + j;
      if (S.left[n] === -1) {
        for (let i = 1; i < m.length; i++) { const u = unwind(m, i); let w = 0; for (const e of u) w += e.w; phi[m[i].d] += w * (m[i].o - m[i].z) * S.leaf[n]; }
        return;
      }
      const d = S.feat[n], b = xb[d];
      const goLeft = b < 0 ? S.dleft[n] === 1 : b <= S.kidx[n];
      const hot = goLeft ? S.left[n] : S.right[n], cold = goLeft ? S.right[n] : S.left[n];
      let iz = 1, io = 1;
      let kk = -1; for (let q = 1; q < m.length; q++) if (m[q].d === d) { kk = q; break; }
      if (kk >= 0) { iz = m[kk].z; io = m[kk].o; m = unwind(m, kk); }
      const rj = S.hess[n];
      rec(hot, m, iz * S.hess[base + hot] / rj, io, d);
      rec(cold, m, iz * S.hess[base + cold] / rj, 0, d);
    }
    rec(0, [], 1, 1, -1);
  }
  /** mean SHAP over a deterministic sample (every step-th) of flood-likely cells, as in run_realtime.py */
  async function shapMean(E, res, c) {
    const S = E.stage2, F = S.features, nf = F.length;
    const idx = []; for (let i = 0; i < c.length; i++) if (c[i] === 3) idx.push(i);
    if (idx.length < 20) return [];
    const step = Math.ceil(idx.length / 300), sample = idx.filter((_, k) => k % step === 0);
    const stIdx = F.map(f => S.static.indexOf(f));
    const binOf = (f, v) => isNaN(v) ? -1 : upperBound(S.conds[f], f32(v));
    const sum = new Float64Array(nf), xb = new Int32Array(nf);
    for (let q = 0; q < sample.length; q++) {
      const i = sample[q];
      for (let j = 0; j < nf; j++) { const si = stIdx[j]; if (si >= 0) { const b = S.bins[S.static[si]][i]; xb[j] = b === (S.bins[S.static[si]] instanceof Uint16Array ? 65535 : 255) ? -1 : b; } }
      const nr = S.near[i], lvN = res.nearLv[nr];
      xb[F.indexOf("lvl_naraj")] = binOf("lvl_naraj", res.lv.Naraj);
      xb[F.indexOf("lvl_near")] = binOf("lvl_near", lvN);
      xb[F.indexOf("depth_proxy_m")] = binOf("depth_proxy_m", lvN - S.elev[i]);
      for (const n of [1, 3, 7]) xb[F.indexOf("rain_chirps_sum" + n)] = binOf("rain_chirps_sum" + n, res.rs[n]);
      const phi = new Float64Array(nf);
      for (let k = 0; k < S.n; k++) treeShapOne(S, k, xb, phi);
      for (let j = 0; j < nf; j++) sum[j] += phi[j];
      if (q % 20 === 19) await yieldNow();
    }
    const out = F.map((f, j) => ({ feature: f, value: Math.round(sum[j] / sample.length * 1000) / 1000 }));
    return out.sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, 7);
  }

  root.FGEngine = { load, dailyFromHourly, stageFeatures, stage1, stage2, classes, rawClasses, likelyCut, areas, grid, placeClasses, shapMean, addD, round2, xgbPredict, rfPredict };
})(typeof globalThis !== "undefined" ? globalThis : this);
