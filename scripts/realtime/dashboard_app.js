(function(){
"use strict";
/* FloodGuard dashboard.
 * Opened from the FloodGuard service (http://localhost:8765) it is LIVE: it polls the service
 * every 30 s, shows each new automatic update, computes the flood map in the page with the
 * same model file, and pops up alerts. Opened as a file it shows the saved run. */
const D = JSON.parse(document.getElementById("fgdata").textContent);
const FG = window.FGEngine;
const $ = id => document.getElementById(id);
const TG = ["Naraj","Kishan Nagar","Tarpur"], ALL = ["Tikarpara","Naraj","Kishan Nagar","Tarpur"];
const LEVEL = {GREEN:"Normal",YELLOW:"Watch",ORANGE:"Warning",RED:"Danger",DELAYED:"Data delayed"};
const RANK = {GREEN:0,YELLOW:1,ORANGE:2,RED:3};
const NS = "http://www.w3.org/2000/svg";
const fin = x => x!==null && x!==undefined && isFinite(x);
const f2 = (x,d=2) => fin(x) ? Number(x).toFixed(d) : "–";
const sgn = (x,d=2) => fin(x) ? (x>0?"+":x<0?"−":"±")+Math.abs(x).toFixed(d) : "–";
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const dParse = s => { const [y,m,d] = s.slice(0,10).split("-").map(Number); const h = s.length>11 ? Number(s.slice(11,13)) : 0, mi = s.length>14 ? Number(s.slice(14,16)) : 0; return new Date(Date.UTC(y,m-1,d,h,mi)); };
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const dLab = t => { const d = new Date(t); return d.getUTCDate()+" "+MON[d.getUTCMonth()]; };
const dLong = s => { const d = dParse(s); return d.getUTCDate()+" "+MON[d.getUTCMonth()]+" "+d.getUTCFullYear(); };
const dDay = s => { const d = dParse(s); return DOW[d.getUTCDay()]+" "+d.getUTCDate()+" "+MON[d.getUTCMonth()]; };
const hm = s => s ? s.slice(11,16) : "–";
const istFmt = ms => new Date(ms).toLocaleString("en-IN",{timeZone:"Asia/Kolkata",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit",hour12:false})+" IST";
const ZN = {z0_above_hirakud:"Above Hirakud",z1_hirakud_tikarpara:"Hirakud to Tikarpara",z2_tikarpara_naraj:"Tikarpara to Naraj",z3_floodplain:"Delta floodplain"};
const FEAT = {dist_river_m:"Distance to the river channel",jrc_occurrence:"How often the land was under water before",lc_bare_frac:"Bare sand and sandbars",
  lc_tree_frac:"Tree cover",lvl_naraj:"Forecast level at Naraj",lc_water_frac:"Open water land cover",lc_crop_frac:"Farmland",slope_deg:"Slope of the land",
  lvl_near:"Forecast level at the nearest gauge",har_m:"Height above the river",rain_chirps_sum7:"Rain in the last 7 days",rain_chirps_sum3:"Rain in the last 3 days",rain_chirps_sum1:"Rain yesterday",
  depth_proxy_m:"River level above the ground",lc_built_frac:"Built-up land",elev_m:"Ground height",lc_grass_frac:"Grassland",lc_shrub_frac:"Shrubland",lc_wetland_frac:"Wetland"};
const svgEl = (tag, attrs={}, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const b64u8 = s => { const bin = atob(s); const u = new Uint8Array(bin.length); for (let i=0;i<bin.length;i++) u[i]=bin.charCodeAt(i); return u; };
function niceTicks(lo, hi, n=5){ const span = hi-lo || 1; const step0 = span/n; const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1,2,2.5,5,10].map(m=>m*mag).find(s=>span/s<=n) || 10*mag; const t=[]; for (let v=Math.ceil(lo/step)*step; v<=hi+1e-9; v+=step) t.push(+v.toFixed(6)); return t; }
function toast(t, ms=4200){ const e=$("toast"); e.textContent=t; e.hidden=false; clearTimeout(toast.h); toast.h=setTimeout(()=>e.hidden=true, ms); }
const store = { get(k,d){ try{ const v=localStorage.getItem("fg."+k); return v===null?d:v; }catch(e){ return d; } }, set(k,v){ try{ localStorage.setItem("fg."+k,v); }catch(e){} } };

/* ---------- grid helpers: which cell belongs to which named area ---------- */
const G = D.grid, AREA = b64u8(G.area_of_cell), BUILT = b64u8(G.built_u8), CROP = b64u8(G.crop_u8);
const MASK = b64u8(G.mask_bits), CELL_R = new Int32Array(AREA.length), CELL_C = new Int32Array(AREA.length);
{ let k=0; for (let i=0;i<G.w*G.h && k<AREA.length;i++) if (MASK[i>>3] & (128>>(i&7))) { CELL_R[k]=Math.floor(i/G.w); CELL_C[k]=i%G.w; k++; } }
const PLACE_BY_NAME = Object.fromEntries(G.places.map((p,i)=>[p.name,Object.assign({i},p)]));

/* ---------- view model ---------- */
function snapshotView(){
  const gauges={};
  ALL.forEach(st=>{ const o=D.latest_obs[st]; gauges[st]={last_time:o.time.slice(0,16), level:o.level, change_24h:o.change_24h, ok:true, age_h:null, readings_today:null, notes:[]}; });
  const lv = TG.map(t=>D.stage1[t].status).reduce((a,b)=>RANK[b]>RANK[a]?b:a,"GREEN");
  return { mode:"snapshot", genMs:Date.parse(D.generated_utc), gauges, stage1:D.stage1, level:lv, data_state:"ok", delayed:[],
    hourly:D.hourly, rain:D.rain, rain_sums:D.maps.live.rain_mm, map:D.maps.live };
}
const SNAP = snapshotView();
let V = SNAP;
const SRV = { on:false, fails:0, status:null, lastGen:null, alertsSeen:null, E:null, mapKey:null, busy:false };

/* ---------- generic time chart (unchanged from the validated version) ---------- */
function timeChart(el, o){
  const W = o.w||720, H = o.h||280, m = {l:46,r:o.right?46:14,t:14,b:26};
  el.innerHTML = "";
  const s = svgEl("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":o.aria||"chart"}, el);
  let ys=[], xs=[];
  (o.lines||[]).forEach(L=>L.pts.forEach(p=>{ if(fin(p[1])){ys.push(p[1]);xs.push(p[0]);} }));
  (o.boxes||[]).forEach(b=>{ys.push(b.lo,b.hi);xs.push(b.x0,b.x1);});
  (o.bands||[]).forEach(b=>b.pts.forEach(p=>{ if(fin(p[1])&&fin(p[2])){ys.push(p[1],p[2]);xs.push(p[0]);} }));
  (o.bars||[]).forEach(b=>{ xs.push(b[0]); ys.push(b[1]||0, 0); });
  (o.dots||[]).forEach(d=>{ if(fin(d.y)){ ys.push(d.y); xs.push(d.x); } });
  (o.hl||[]).forEach(h=>{ if(h.force) ys.push(h.y); });
  if (!xs.length){ el.innerHTML='<p class="empty">No readings to show.</p>'; return; }
  const x0=Math.min(...xs), x1=Math.max(...xs);
  let y0=Math.min(...ys), y1=Math.max(...ys); const pad=Math.max((y1-y0)*0.12,o.minPad||0.1); y1+=pad; y0 = o.bars ? 0 : y0-pad;
  const X=v=>m.l+(v-x0)/(x1-x0||1)*(W-m.l-m.r), Y=v=>H-m.b-(v-y0)/(y1-y0)*(H-m.t-m.b);
  const g = svgEl("g",{class:"grid"},s);
  niceTicks(y0,y1,5).forEach(t=>{ svgEl("line",{x1:m.l,x2:W-m.r,y1:Y(t),y2:Y(t)},g); const tx=svgEl("text",{x:m.l-6,y:Y(t)+4,"text-anchor":"end"},s); tx.textContent=o.bars?f2(t,0):f2(t,(y1-y0)<3?2:1); });
  const day=86400000, spanD=(x1-x0)/day, xt=[];
  if (spanD>60){ let d=new Date(x0); let c=Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,1); while(c<=x1){xt.push(c); d=new Date(c); c=Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,1);} }
  else { const stepD = spanD>20?7:1; let t=new Date(x0); t=Date.UTC(t.getUTCFullYear(),t.getUTCMonth(),t.getUTCDate()); if(t<x0) t+=day; for(;t<=x1;t+=stepD*day) xt.push(t); }
  xt.forEach(v=>{ svgEl("line",{x1:X(v),x2:X(v),y1:m.t,y2:H-m.b},g); const tx=svgEl("text",{x:X(v),y:H-8,"text-anchor":"middle"},s); tx.textContent = spanD>60 ? MON[new Date(v).getUTCMonth()] : dLab(v); });
  (o.hl||[]).forEach(h=>{ if(h.y<y0||h.y>y1) return; svgEl("line",{x1:m.l,x2:W-m.r,y1:Y(h.y),y2:Y(h.y),style:`stroke:${h.col}`,"stroke-width":1.4,"stroke-dasharray":"5 4"},s);
    const tx=svgEl("text",{x:W-m.r-4,y:Y(h.y)-5,"text-anchor":"end",style:`fill:${h.col};font-weight:700`},s); tx.textContent=h.label; });
  (o.bands||[]).forEach(b=>{ const P=b.pts.filter(p=>fin(p[1])&&fin(p[2])); if(P.length<2) return;
    const d="M"+P.map(p=>X(p[0])+","+Y(p[2])).join("L")+"L"+P.slice().reverse().map(p=>X(p[0])+","+Y(p[1])).join("L")+"Z"; svgEl("path",{d,style:`fill:${b.col}`,stroke:"none"},s); });
  if (o.bars){ const bw=Math.max(1,(W-m.l-m.r)/o.bars.length*0.8); o.bars.forEach(b=>{ if(!b[1]) return; svgEl("rect",{x:X(b[0])-bw/2,y:Y(b[1]),width:bw,height:Math.max(0,Y(0)-Y(b[1])),style:`fill:${o.barCol}`},s); }); }
  (o.boxes||[]).forEach(b=>{ svgEl("rect",{x:X(b.x0),y:Y(b.hi),width:Math.max(2,X(b.x1)-X(b.x0)),height:Math.max(2,Y(b.lo)-Y(b.hi)),rx:3,style:`fill:${b.fill};stroke:${b.stroke}`,"stroke-width":1},s);
    svgEl("line",{x1:X(b.x0),x2:X(b.x1),y1:Y(b.mid),y2:Y(b.mid),style:`stroke:${b.stroke}`,"stroke-width":2.4},s);
    if(b.label){ const tx=svgEl("text",{x:(X(b.x0)+X(b.x1))/2,y:Y(b.hi)-6,"text-anchor":"middle",style:"fill:var(--ink);font-weight:700"},s); tx.textContent=b.label; } });
  (o.lines||[]).forEach(L=>{ let d="", pen=false; L.pts.forEach(p=>{ if(!fin(p[1])){pen=false;return;} d+=(pen?"L":"M")+X(p[0]).toFixed(1)+","+Y(p[1]).toFixed(1); pen=true; });
    svgEl("path",{d,fill:"none",style:`stroke:${L.col}`,"stroke-width":L.w||1.8,"stroke-dasharray":L.dash||"","stroke-linejoin":"round","stroke-linecap":"round"},s);
    if (L.end){ const P=L.pts.filter(p=>fin(p[1])); const q=P[P.length-1]; if(q) svgEl("circle",{cx:X(q[0]),cy:Y(q[1]),r:4,style:`fill:${L.col};stroke:var(--board)`,"stroke-width":2},s); } });
  (o.dots||[]).forEach(d=>{ if(!fin(d.y)) return; svgEl("circle",{cx:X(d.x),cy:Y(d.y),r:5,style:`fill:${d.col};stroke:var(--board)`,"stroke-width":2},s);
    if (d.label){ const tx=svgEl("text",{x:X(d.x),y:Y(d.y)-9,"text-anchor":"middle",style:`fill:${d.col};font-weight:700`},s); tx.textContent=d.label; } });
  if (o.right){ const R=o.right; const ry=R.pts.filter(p=>fin(p[1])).map(p=>p[1]); let r0=Math.min(...ry),r1=Math.max(...ry); const rp=(r1-r0)*0.1; r0-=rp; r1+=rp;
    const RY=v=>H-m.b-(v-r0)/(r1-r0)*(H-m.t-m.b); niceTicks(r0,r1,5).forEach(t=>{const tx=svgEl("text",{x:W-m.r+6,y:RY(t)+4},s); tx.textContent=f2(t,0);});
    let d="",pen=false; R.pts.forEach(p=>{ if(!fin(p[1])){pen=false;return;} d+=(pen?"L":"M")+X(p[0]).toFixed(1)+","+RY(p[1]).toFixed(1); pen=true; });
    svgEl("path",{d,fill:"none",style:`stroke:${R.col}`,"stroke-width":1.5},s);
    (R.hl||[]).forEach(h=>{ if(h.y<r0||h.y>r1) return; svgEl("line",{x1:m.l,x2:W-m.r,y1:RY(h.y),y2:RY(h.y),style:`stroke:${h.col}`,"stroke-width":1.2,"stroke-dasharray":"5 4"},s);
      const tx=svgEl("text",{x:m.l+6,y:RY(h.y)-5,style:`fill:${h.col};font-weight:700`},s); tx.textContent=h.label; }); }
}
function seg(el, items, cur, onPick){
  el.innerHTML=""; items.forEach(([k,lab])=>{ const b=document.createElement("button"); b.type="button"; b.textContent=lab; b.setAttribute("aria-pressed",String(k===cur));
    b.addEventListener("click",()=>{ [...el.children].forEach(c=>c.setAttribute("aria-pressed","false")); b.setAttribute("aria-pressed","true"); onPick(k); }); el.appendChild(b); });
}

/* ---------- connection pill ---------- */
function renderConn(){
  const el=$("conn"), t=$("connText");
  if (V.mode==="demo"){ el.className="conn demo"; t.innerHTML=`<b>Demo replay</b> ${esc(dLong(V.demo.day))}, day ${V.demo.step} of ${V.demo.n_steps}`; return; }
  if (V.mode==="live"){
    if (SRV.fails>=3){ el.className="conn err"; t.innerHTML=`<b>FloodGuard service not answering</b> showing the update of ${istFmt(V.genMs)}`; return; }
    const n=V.gauges.Naraj, nx=V.next_update_utc?istFmt(Date.parse(V.next_update_utc)).replace(/^.*, /,""):"";
    el.className = "conn "+(V.data_state==="delayed"?"stale":"live");
    t.innerHTML = `<b>${V.data_state==="delayed"?"Live, data delayed":"Live"}</b> CWC reading ${n.last_time?esc(dLab(dParse(n.last_time))+", "+hm(n.last_time)):"–"} IST. Next update ${esc(nx)}`; return; }
  const n=V.gauges.Naraj; el.className="conn"; t.innerHTML=`<b>Saved run</b> river readings up to ${n&&n.last_time?esc(dLab(dParse(n.last_time))+", "+hm(n.last_time))+" IST":"–"}. Start FloodGuard for live updates`;
}

/* ---------- status band ---------- */
const HEAD = {GREEN:"No flooding expected in the next 24 hours", YELLOW:"The river is rising. Stay alert for the next 24 hours",
  ORANGE:"Flooding expected along the river within 24 hours", RED:"Severe flooding expected within 24 hours", DELAYED:"Live river readings are late, so the warning cannot be updated"};
const TODO = {GREEN:"No action needed. FloodGuard checks the river again automatically.",
  YELLOW:"Keep an eye on official updates. Make sure phones are charged and you know your nearest safe shelter.",
  ORANGE:"People on low-lying land near the river should get ready to move: keep documents, medicines and drinking water at hand, move valuables and livestock to higher ground.",
  RED:"Move people and livestock from low-lying land near the river to safe shelters now. Follow instructions from the district administration and OSDMA.",
  DELAYED:"Check the CWC and OSDMA bulletins directly. FloodGuard will update by itself as soon as readings arrive."};
const SCALE = {Naraj:[20,28],"Kishan Nagar":[10,16.5],Tarpur:[5,12]};
function bandLevel(){ return V.data_state==="delayed" ? "DELAYED" : (V.level || "GREEN"); }
function renderBand(){
  const L=bandLevel(), n=V.stage1.Naraj, el=$("band");
  el.className="band l-"+L;
  const valid = n ? n.valid : null;
  const genTxt = V.mode==="live" ? `Updated automatically ${istFmt(V.genMs)}` : V.mode==="demo" ? "Demo replay of a real 2026 flood with the live models" : "Saved run, not live";
  const ng=V.gauges.Naraj;
  const tiles = TG.map(st=>{
    const s=V.stage1[st], g=V.gauges[st], th=D.thresholds[st], [a,b]=SCALE[st], P=v=>Math.max(0,Math.min(100,(v-a)/(b-a)*100));
    if (!s) return `<div class="gtile"><div class="nm"><span>${st}</span><span class="st s-NONE">No data</span></div><p class="small">No recent reading from this gauge${g&&g.last_time?`; last ${esc(g.last_time)} IST`:""}.</p></div>`;
    const now = fin(g.level) ? g.level : s.today_max;
    return `<div class="gtile"><div class="nm"><span>${st}</span><span class="st s-${s.status}">${LEVEL[s.status]}</span></div>
      <div class="row"><span>Now</span><span class="big">${f2(now)}<small> m</small></span></div>
      <div class="row"><span>${valid?dDay(s.valid):"Tomorrow"}</span><span><b>${f2(s.fc)} m</b> (${f2(s.lo)} to ${f2(s.hi)})</span></div>
      <div class="staff" title="Now (filled), tomorrow (arrow), warning and danger levels (marks)"><span class="now" style="width:${P(now)}%"></span>
        <i style="left:${P(th.warning)}%;background:#fff;opacity:.95"></i><i style="left:${P(th.danger)}%;background:#fff;opacity:.95;width:4px"></i><span class="fc" style="left:${P(s.fc)}%"></span></div>
      <div class="gfoot">Warning ${f2(th.warning)} m, danger ${f2(th.danger)} m${st==="Naraj"?"":" (derived)"}. Chance of crossing warning: ${(s.p_warn*100).toFixed(0)}%</div></div>`; }).join("");
  el.innerHTML = `<div><div class="word">${LEVEL[L]}</div><p class="head">${HEAD[L]}${L!=="DELAYED"&&valid?`, up to ${dDay(valid)}`:""}.</p><p class="todo">${TODO[L]}</p>
    <p class="meta">${genTxt}. River readings up to ${ng&&ng.last_time?esc(dLab(dParse(ng.last_time))+", "+hm(ng.last_time))+" IST":"–"}.</p></div><div class="gauges">${tiles}</div>`;
  document.title = (L==="GREEN"?"":LEVEL[L]+": ")+"FloodGuard Live";
}
function renderNotices(){
  const out=[];
  if (V.mode==="snapshot") out.push(`<div class="notice">This is a saved run (river readings up to ${esc(dLong(V.gauges.Naraj.last_time))}). To see live readings that update by themselves, double-click <b>START_FLOODGUARD.bat</b> in the project folder; this page then opens at <b>http://localhost:8765</b>.</div>`);
  if (V.mode==="demo") out.push(`<div class="notice demo"><b>Demo replay.</b> FloodGuard is replaying ${esc(V.demo.scenario)} one day every few seconds, using the CWC readings and rain of those days and the same models as live. Alerts are sent marked DEMO. <button class="btn" type="button" id="stopDemo2">Stop demo</button></div>`);
  if (V.data_state==="delayed") out.push(`<div class="notice"><b>Data delayed.</b> No recent reading from ${esc(V.delayed.join(", "))}. The last forecast is not shown as current, and no "all clear" will be sent until readings return.</div>`);
  else if (V.delayed && V.delayed.length) out.push(`<div class="notice warn"><b>${esc(V.delayed.join(", "))} is late.</b> Forecasts still run without it${V.upstream_missing?"; without the upstream gauge at Tikarpara the next-day forecast is less certain":""}.</div>`);
  $("notices").innerHTML=out.join("");
  const sd=$("stopDemo2"); if (sd) sd.onclick=stopDemo;
}

/* ---------- map ---------- */
let mapKey="now", ladderI=Math.min(5,D.ladder.length-1), playTimer=null, selArea=null;
const curMap = () => mapKey==="now" ? V.map : mapKey==="ladder" ? D.ladder[ladderI] : D.maps[mapKey];
function highlightPng(ai){
  if (highlightPng.c[ai]) return highlightPng.c[ai];
  const cv=document.createElement("canvas"); cv.width=G.w; cv.height=G.h; const ctx=cv.getContext("2d"), img=ctx.createImageData(G.w,G.h), px=img.data;
  const inA=new Uint8Array(G.w*G.h); for (let i=0;i<AREA.length;i++) if (AREA[i]===ai) inA[CELL_R[i]*G.w+CELL_C[i]]=1;
  for (let r=0;r<G.h;r++) for (let c=0;c<G.w;c++){ const k=r*G.w+c; if(!inA[k]) continue; const edge = c===0||c===G.w-1||r===0||r===G.h-1||!inA[k-1]||!inA[k+1]||!inA[k-G.w]||!inA[k+G.w];
    const o=k*4; if (edge){ px[o]=20;px[o+1]=90;px[o+2]=140;px[o+3]=255; } else { px[o]=27;px[o+1]=110;px[o+2]=158;px[o+3]=38; } }
  ctx.putImageData(img,0,0); return (highlightPng.c[ai]=cv.toDataURL("image/png"));
}
highlightPng.c={};
function buildMap(el, layers, opts={}){
  el.innerHTML="";
  const add=src=>{ const i=document.createElement("img"); i.src=src; i.alt=""; i.decoding="async"; el.appendChild(i); return i; };
  add(G.aoi_png); layers.forEach(add); add(G.river_png);
  if (opts.highlight!==undefined && opts.highlight!==null) add(highlightPng(opts.highlight));
  const s=svgEl("svg",{viewBox:`0 0 ${G.w} ${G.h}`,"aria-hidden":"true"},el);
  if (opts.places!==false) G.places.forEach((p,i)=>{
    const c=opts.cls?opts.cls[p.name]||0:0, hi=opts.highlight===i, big=p.kind!=="village"||c>0||hi;
    svgEl("circle",{cx:p.col,cy:p.row,r:hi?4.5:big?3.4:2.4,style:"fill:"+(hi?"var(--river)":c?["","var(--watch)","var(--warning)","var(--danger)"][c]:"var(--muted)")+";stroke:var(--board)","stroke-width":1.2},s);
    if (big){ const t=svgEl("text",{x:p.col+5,y:p.row+3.5,class:"lbl"+(hi?" hi":"")},s); t.textContent=p.name; } });
  Object.entries(G.stations).forEach(([n,p])=>{ svgEl("path",{d:`M${p.col} ${p.row-7} l6 10 h-12 z`,class:"gauge"},s);
    const t=svgEl("text",{x:p.col+8,y:p.row-6,class:"gauge-lbl"},s); t.textContent=n+" gauge"; });
  if (opts.busy){ const b=document.createElement("span"); b.className="busy"; b.textContent=opts.busy; el.appendChild(b); }
}
function renderMap(busyText){
  const M=curMap();
  buildMap($("map"), [M.png], {cls:M.places, busy:mapKey==="now"?busyText:null, highlight:selArea});
  $("ladderBox").hidden = mapKey!=="ladder";
  const lv=M.levels;
  let sub;
  if (mapKey==="now") sub = M.valid ? `Forecast for ${dDay(M.valid)}, from the forecast river levels (Naraj ${f2(lv.Naraj)} m, Kishan Nagar ${f2(lv["Kishan Nagar"])} m, Tarpur ${f2(lv.Tarpur)} m) and ${f2(M.rain_mm["7"],0)} mm of rain on the floodplain in the last 7 days.` : "";
  else if (mapKey==="ladder") sub=`What if Naraj reached ${f2(lv.Naraj)} m? Kishan Nagar and Tarpur at their usual levels for that day (${f2(lv["Kishan Nagar"])} m, ${f2(lv.Tarpur)} m), with typical flood-week rain.`;
  else { const o=M.levels_observed; sub=`Replay of a 2026 flood: map issued ${dLong(M.issue)} for ${dLong(M.valid)}. Forecast Naraj ${f2(lv.Naraj)} m; the river actually reached ${f2(o.Naraj)} m.`; }
  $("mapSub").textContent=sub;
  $("mapKey").innerHTML=`<span><i style="background:rgb(189,0,38)"></i>Flood likely</span><span><i style="background:rgb(253,141,60)"></i>Elevated risk</span><span><i style="background:rgb(254,217,118)"></i>Low risk, watch</span><span><i style="background:rgb(96,165,250)"></i>Normal monsoon river water</span><span><i style="background:var(--ink);border-radius:0;clip-path:polygon(50% 0,100% 100%,0 100%)"></i>CWC gauge</span>`;
  renderAreas(M); renderShap(M);
}
function areaRows(M){
  const A=M.areas||{area_km2:[],area_elev_km2:[]}, rows=[];
  G.area_names.forEach((name,i)=>{
    const lk=A.area_km2[i]||0, el=A.area_elev_km2[i]||0, centre=(M.places||{})[name]||0;
    let r = (lk>=1||centre===3) ? 3 : (lk>=0.2||el>=1||centre===2) ? 2 : (lk>=0.05||el>=0.2||centre===1) ? 1 : 0;
    rows.push({i,name,lk,el,centre,r});
  });
  return rows.sort((a,b)=>b.r-a.r||b.lk-a.lk||b.el-a.el||a.name.localeCompare(b.name));
}
function renderAreas(M){
  const A=M.areas||{};
  const nPlaces=Object.values(M.places||{}).filter(c=>c===3).length;
  $("areaSum").innerHTML=[[f2(M.km2_likely,1),"km² flood likely"],[f2(A.built_near_km2,1),"km² of homes and buildings within 500 m"],[String(nPlaces),"towns and villages within 1 km"]]
    .map(([v,l])=>`<div><b>${v}</b><span>${l}</span></div>`).join("");
  const q=$("areaSearch").value.trim().toLowerCase();
  const rows=areaRows(M).filter(r=>q ? r.name.toLowerCase().includes(q) : r.r>0);
  const GR={3:["High risk","var(--danger)"],2:["Medium risk","var(--warning)"],1:["Low risk","var(--watch)"],0:["No flood risk shown","var(--normal)"]};
  let h="", last=-1;
  rows.forEach(r=>{ if (r.r!==last){ h+=`<div class="agrp"><i style="background:${GR[r.r][1]}"></i>${GR[r.r][0]}</div>`; last=r.r; }
    const d=[]; if (r.lk>0) d.push(`${f2(r.lk,1)} km² flood likely`); if (r.el>0) d.push(`${f2(r.el,1)} km² elevated`);
    if (r.centre>=2) d.push(r.centre===3?"flooding likely within 1 km of the village centre":"elevated risk within 1 km of the village centre");
    const trig = D.village_trigger_naraj_m[r.name]; if (trig) d.push(`water reaches here from Naraj ${f2(trig,1)} m`);
    h+=`<button class="aitem r${r.r}" type="button" data-a="${r.i}" aria-pressed="${selArea===r.i}"><span class="n">${esc(r.name)} area</span><span class="k">${r.lk>0?f2(r.lk,1)+" km²":r.r?"":"none"}</span><span class="d">${esc(d.join(". ")||"No flooding beyond normal river water.")}</span></button>`; });
  $("areaList").innerHTML = h || `<p class="empty">${q?"No place with that name in the study area.":"No area is at risk beyond normal monsoon river water. Try the 2026 replays or the what-if level to see which areas flood first."}</p>`;
  $("areaList").querySelectorAll(".aitem").forEach(b=>b.addEventListener("click",()=>{ const a=+b.dataset.a; selArea = selArea===a ? null : a; renderMap(); }));
  $("areasSub").textContent = mapKey==="now" ? "Named after the nearest town or village. Select one to outline it on the map." : "For the map shown. Select an area to outline it.";
}
function renderShap(M){
  const sh=M.shap||[], el=$("shap");
  if (!sh.length){ el.innerHTML=`<p class="muted small">No land reaches the flood-likely class on this map, so there is nothing to explain.</p>`; return; }
  const mx=Math.max(...sh.map(x=>Math.abs(x.value)));
  el.innerHTML=sh.map(x=>{ const w=Math.abs(x.value)/mx*50, pos=x.value>=0;
    return `<div class="bar"><span class="name" title="${esc(x.feature)}">${esc(FEAT[x.feature]||x.feature)}</span><span class="track"><span class="fill" style="${pos?"left:50%":"right:50%"};width:${w}%;background:${pos?"var(--danger)":"var(--river)"}"></span></span><span class="v">${sgn(x.value)}</span></div>`; }).join("")+
    `<p class="muted tiny" style="margin-top:4px">Average contribution (SHAP) in flood-likely cells. Red pushes the risk up, blue down.</p>`;
}
function stopPlay(){ if(playTimer){ clearInterval(playTimer); playTimer=null; } $("playBtn").textContent="Play"; }
function initMapControls(){
  const items=[["now","Tomorrow"]]; Object.keys(D.maps).filter(k=>k.startsWith("replay")).forEach(k=>items.push([k,"2026 flood, "+dLab(dParse(D.maps[k].valid))])); items.push(["ladder","What if"]);
  seg($("mapSeg"), items, mapKey, k=>{ stopPlay(); mapKey=k; renderMap(); });
  const r=$("ladder"); r.max=D.ladder.length-1; r.value=ladderI;
  const upd=()=>{ ladderI=+r.value; const L=D.ladder[ladderI], th=D.thresholds.Naraj; $("ladderVal").textContent="Naraj "+f2(L.naraj,1)+" m";
    $("ladderNote").textContent=L.naraj>=th.danger?`above the danger level (${th.danger} m)`:L.naraj>=th.warning?`above the warning level (${th.warning} m)`:`below the warning level (${th.warning} m)`;
    if (mapKey==="ladder") renderMap(); };
  r.addEventListener("input",()=>{ stopPlay(); upd(); }); upd();
  $("playBtn").addEventListener("click",()=>{
    if (playTimer){ stopPlay(); return; }
    if (+r.value>=+r.max) r.value=0; upd(); $("playBtn").textContent="Pause";
    playTimer=setInterval(()=>{ if(+r.value>=+r.max){ stopPlay(); return; } r.value=+r.value+1; upd(); }, 1100);
  });
  $("areaSearch").addEventListener("input",()=>renderAreas(curMap()));
}

/* ---------- hydrograph, outlook table, rain ---------- */
let hydroSt="Naraj";
function renderHydro(){
  const st=hydroSt, s=V.stage1[st], th=D.thresholds[st];
  const P=(V.hourly[st]||[]).map(p=>[dParse(p[0]).getTime(),p[1]]), day=86400000, boxes=[], dots=[];
  if (s){ const add=(v,lab,o)=>{ const x0=dParse(v).getTime(); boxes.push({x0:x0+day*0.08,x1:x0+day*0.92,lo:o.lo,hi:o.hi,mid:o.fc,label:lab,fill:"var(--chart-band)",stroke:"var(--chart-fc)"}); };
    add(s.valid,"+24 h",s); add(s.outlook["+48h"].valid,"+48 h",s.outlook["+48h"]); add(s.outlook["+72h"].valid,"+72 h",s.outlook["+72h"]);
    const g=V.gauges[st]; if (V.mode==="demo" && g && fin(g.observed_next_day)) dots.push({x:dParse(s.valid).getTime()+day/2,y:g.observed_next_day,col:"var(--normal)",label:"what happened"}); }
  timeChart($("hydro"),{lines:[{pts:P,col:"var(--chart-obs)",w:2,end:true}],boxes,dots,
    hl:[{y:th.warning,col:"var(--warning)",label:"warning "+f2(th.warning)},{y:th.danger,col:"var(--danger)",label:"danger "+f2(th.danger)}],minPad:.15,aria:`${st} river level and forecast`});
  $("hydroLegend").innerHTML = `<span><i style="background:var(--chart-obs)"></i>${V.mode==="demo"?"observed daily highest level":"observed, hourly"}</span><span><i style="background:var(--chart-fc)"></i>forecast highest level and 80% range</span>`+(V.mode==="demo"?`<span><i style="background:var(--normal)"></i>what the gauge really read next day</span>`:"");
  if (!s){ $("outlook").innerHTML=`<tr><td>No forecast: no recent reading from ${st}.</td></tr>`; return; }
  const R=[["Tomorrow",s.valid,s.fc,s.lo,s.hi,s.p_warn],["Day after",s.outlook["+48h"].valid,s.outlook["+48h"].fc,s.outlook["+48h"].lo,s.outlook["+48h"].hi,s.outlook["+48h"].p_warn],
    ["In 3 days",s.outlook["+72h"].valid,s.outlook["+72h"].fc,s.outlook["+72h"].lo,s.outlook["+72h"].hi,s.outlook["+72h"].p_warn]];
  $("outlook").innerHTML=`<thead><tr><th>${esc(st)}</th><th>Date</th><th>Highest level</th><th>80% range</th><th>Chance above warning</th></tr></thead><tbody>`+
    R.map(r=>`<tr><td>${r[0]}</td><td>${dLab(dParse(r[1]))}</td><td><b>${f2(r[2])} m</b></td><td>${f2(r[3])} to ${f2(r[4])} m</td><td>${(r[5]*100).toFixed(0)}%</td></tr>`).join("")+`</tbody>`;
}
let rainZ="z2_tikarpara_naraj";
function renderRain(){
  const r=(V.rain.recent[rainZ]||[]).slice(-14), f=V.rain.forecast[rainZ]||[];
  const all=r.map(p=>[p[0],p[1],false]).concat(f.map(p=>[p[0],p[1],true]));
  const W=520,H=230,m={l:36,r:8,t:18,b:26}, el=$("rain"); el.innerHTML="";
  if (!all.length){ el.innerHTML='<p class="empty">No rainfall data in this update.</p>'; return; }
  const s=svgEl("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"daily rainfall"},el);
  const mx=Math.max(10,...all.map(p=>fin(p[1])?p[1]:0)), ticks=niceTicks(0,mx,4), top=Math.max(mx,ticks[ticks.length-1]);
  const Y=v=>H-m.b-v/top*(H-m.t-m.b), bw=(W-m.l-m.r)/all.length;
  const g=svgEl("g",{class:"grid"},s); ticks.forEach(t=>{ svgEl("line",{x1:m.l,x2:W-m.r,y1:Y(t),y2:Y(t)},g); const tx=svgEl("text",{x:m.l-6,y:Y(t)+4,"text-anchor":"end"},s); tx.textContent=t; });
  all.forEach((p,i)=>{ const x=m.l+i*bw, v=fin(p[1])?p[1]:0; svgEl("rect",{x:x+bw*.15,y:Y(v),width:bw*.7,height:Math.max(0,Y(0)-Y(v)),rx:2,style:"fill:"+(p[2]?"color-mix(in srgb,var(--river) 40%,transparent)":"var(--river)")},s);
    if (i%3===0){ const tx=svgEl("text",{x:x+bw/2,y:H-8,"text-anchor":"middle"},s); tx.textContent=dLab(dParse(p[0])); }
    if (v>=1){ const tx=svgEl("text",{x:x+bw/2,y:Y(v)-4,"text-anchor":"middle",style:"font-size:9px"},s); tx.textContent=v.toFixed(0); } });
  if (f.length){ const xs=m.l+r.length*bw; svgEl("line",{x1:xs,x2:xs,y1:m.t-6,y2:H-m.b,style:"stroke:var(--faint)","stroke-dasharray":"3 3"},s);
    const tx=svgEl("text",{x:xs+4,y:m.t-4},s); tx.textContent="forecast"; }
}

/* ---------- worked example and message ---------- */
function renderWorked(){
  const s=V.stage1.Naraj; if (!s){ $("worked").textContent="No Naraj forecast in this update."; return; }
  const b=s.today_max, M=s.members, T=V.gauges.Tikarpara, dl=k=>sgn(M[k]-b,3);
  const r3=(V.rain.recent.z2_tikarpara_naraj||[]).slice(-3).reduce((a,p)=>a+(fin(p[1])?p[1]:0),0);
  $("worked").textContent=
`Issue day ${s.issue}, Naraj
  today's highest level so far   ${f2(b)} m
  Tikarpara (upstream) now        ${f2(T&&T.level)} m  (${sgn(T&&T.change_24h)} m in 24 h)
  rain, Tikarpara to Naraj        ${f2(r3,1)} mm in 3 days

Each model predicts the change to tomorrow's highest level from 77 inputs
  Ridge regression                ${dl("Ridge")} m
  Random forest                   ${dl("RandomForest")} m
  XGBoost                         ${dl("XGBoost")} m

Forecast = today + average change = ${f2(s.fc)} m  for ${s.valid}
80% range                          ${f2(s.lo)} to ${f2(s.hi)} m
Chance of reaching ${f2(D.thresholds.Naraj.warning)} m (warning) = ${(s.p_warn*100).toFixed(1)} %
Level                              ${LEVEL[s.status]}`;
}
function bulletinText(){
  const L=bandLevel(), M=(mapKey==="now"?V.map:V.map), s=V.stage1.Naraj, n=V.gauges.Naraj;
  let t=`FloodGuard ${LEVEL[L].toUpperCase()}: Mahanadi delta, Cuttack district\n`;
  if (V.mode==="demo") t=`[DEMO replay of ${V.demo.scenario}, ${dLong(V.demo.day)}]\n`+t;
  t+=`${HEAD[L]}${L!=="DELAYED"&&s?", up to "+dDay(s.valid):""}.\nRiver readings up to ${n&&n.last_time?dLab(dParse(n.last_time))+", "+hm(n.last_time)+" IST":"–"}.\n\n`;
  TG.forEach(st=>{ const x=V.stage1[st], g=V.gauges[st]; t+= x ? `${st}: now ${f2(fin(g.level)?g.level:x.today_max)} m, highest tomorrow ${f2(x.fc)} m (warning ${f2(D.thresholds[st].warning)} m, danger ${f2(D.thresholds[st].danger)} m).\n` : `${st}: no recent reading.\n`; });
  const rows=areaRows(M).filter(r=>r.r>=2).slice(0,8);
  if (M.km2_likely>0) t+=`\nFlood likely over about ${f2(M.km2_likely,0)} km² beyond normal river water, with ${f2(M.areas.built_near_km2,1)} km² of homes and buildings within 500 m.`;
  else t+=`\nNo flooding expected beyond normal monsoon river water.`;
  if (rows.length) t+=`\nAreas at risk: ${rows.map(r=>`${r.name} (${f2(r.lk,1)} km²)`).join(", ")}.`;
  t+=`\n\nWhat to do: ${TODO[L]}\n\nResearch prototype, not an official warning. Follow CWC, OSDMA and the district administration.`;
  return t;
}
function renderBulletin(){ $("bulletin").textContent=bulletinText(); }
$("copyBul").addEventListener("click",()=>{ const t=bulletinText(); const done=()=>{ $("copyBul").textContent="Copied"; setTimeout(()=>$("copyBul").textContent="Copy message",1600); };
  try { if (navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done,()=>toast("Select the text and press Ctrl+C")); else toast("Select the text and press Ctrl+C"); } catch(e){ toast("Select the text and press Ctrl+C"); } });

function renderLive(busy){ renderConn(); renderBand(); renderNotices(); renderMap(busy); renderHydro(); renderRain(); renderWorked(); renderBulletin(); if (done.alerts) renderHealth(); }

/* ---------- alerts and data health ---------- */
async function renderAlertsTab(){
  const chans=$("chans");
  if (!SRV.on){ $("alertLog").innerHTML=`<p class="empty">Alerts are sent by the FloodGuard service. Start it with START_FLOODGUARD.bat to see them here.</p>`; $("tgConnect").disabled=true;
    chans.innerHTML=""; $("chanSub").textContent="Not connected to the FloodGuard service."; $("testBtn").disabled=true; renderHealth(); return; }
  $("testBtn").disabled=false; $("tgConnect").disabled=false;
  const st=SRV.status||{}, ch=st.channels||{};
  chans.innerHTML=[["desktop","Pop-up on this computer"],["telegram","Telegram"],["email","E-mail"],["sms","SMS"]].map(([k,l])=>`<span class="chip ${ch[k]?"on":""}">${l}: ${ch[k]?"on":"off"}</span>`).join("")+
    `<span class="chip ${notifOn()?"on":""}">Browser pop-up: ${notifOn()?"on":"off"}</span>`;
  $("chanSub").textContent = ch.telegram||ch.email||ch.sms ? "Alerts go to every channel that is on." : "Pop-ups only so far. Add Telegram below to get alerts on a phone.";
  try { const log=await (await fetch("/api/alerts",{cache:"no-store"})).json(); drawAlertLog(log); } catch(e){}
  const T=st.telegram||{};
  $("tgState").textContent = ch.telegram ? `Connected to @${T.bot}. Alerts go to: ${(T.chats||[]).join(", ")}.` : T.has_token ? `Token saved for @${T.bot||"your bot"}; press Start in the bot, then Connect.` : "Takes about two minutes, once.";
  $("tgConnect").textContent = ch.telegram ? "Add people who pressed Start" : "Connect";
  renderHealth();
}
$("tgConnect").addEventListener("click", async ()=>{
  if (!SRV.on){ toast("Start FloodGuard first (START_FLOODGUARD.bat)."); return; }
  const b=$("tgConnect"); b.disabled=true; $("tgMsg").textContent="Contacting Telegram…";
  try{
    const r=await (await fetch("/api/telegram/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token:$("tgToken").value.trim()})})).json();
    $("tgMsg").innerHTML = esc(r.message||"") + (r.need_start&&r.bot ? ` <a href="https://t.me/${encodeURIComponent(r.bot)}" target="_blank" rel="noopener">Open @${esc(r.bot)}</a>` : "");
    if (r.ok){ $("tgToken").value=""; toast("Telegram connected. A welcome message was sent."); }
    SRV.status=await (await fetch("/api/status",{cache:"no-store"})).json();
  }catch(e){ $("tgMsg").textContent="The FloodGuard service did not answer."; }
  b.disabled=false; renderAlertsTab();
});
function drawAlertLog(log){
  const L=(log||[]).slice().reverse();
  $("alertLog").innerHTML = L.length ? L.map(a=>`<div class="aent l-${a.level||""}"><div class="t">${esc((a.demo?"[Demo] ":"")+a.kind.charAt(0).toUpperCase()+a.kind.slice(1))}<span>${esc(a.time_ist)}</span></div>
    <pre>${esc(a.text)}</pre><div class="tiny muted" style="margin-top:4px">${Object.entries(a.channels||{}).map(([k,v])=>`${k}: ${v}`).join(", ")}</div></div>`).join("")
    : `<p class="empty">No alert yet. The first alert goes out when any gauge reaches Watch.</p>`;
}
function renderHealth(){
  if (SRV.status && SRV.status.settings) $("staleH").textContent=SRV.status.settings.stale_hours;
  const rows=ALL.map(st=>{ const g=V.gauges[st]||{};
    const state = V.mode==="snapshot" ? `<span class="muted">saved run</span>` : g.ok ? `<span class="ok">on time</span>` : `<span class="bad">late</span>`;
    return `<tr><td><b>${st}</b>${st==="Tikarpara"?'<div class="tiny muted">upstream input</div>':""}</td><td>${g.last_time?esc(dLab(dParse(g.last_time))+", "+hm(g.last_time)):"–"}</td><td class="n">${f2(g.level)} m</td><td class="n">${fin(g.age_h)?f2(g.age_h,1)+" h":"–"}</td><td class="n">${fin(g.readings_today)?g.readings_today:"–"}</td><td>${state}</td><td class="tiny">${esc([...(g.notes||[]), g.error||""].filter(Boolean).join("; ")||"all readings passed the checks")}</td></tr>`; }).join("");
  const s=SRV.status;
  $("health").innerHTML=`<table><thead><tr><th>Gauge</th><th>Last reading (IST)</th><th class="n">Level</th><th class="n">Age</th><th class="n">Readings today</th><th>Status</th><th>Checks</th></tr></thead><tbody>${rows}</tbody></table>`+
    (s?`<p class="tiny muted" style="margin-top:8px">Service started ${istFmt(Date.parse(s.started_utc))}. Updates done: ${s.runs}. Last update ${s.last_run_utc?istFmt(Date.parse(s.last_run_utc)):"–"}. Every update is logged in 08_results/live/run_log.csv.${s.last_error?` Last problem: ${esc(String(s.last_error).slice(0,200))}`:""}</p>`:"");
}
const notifOn = () => ("Notification" in window) && Notification.permission==="granted";
$("notifBtn").addEventListener("click", async ()=>{
  if (!("Notification" in window)){ toast("This browser cannot show pop-ups."); return; }
  const p=await Notification.requestPermission(); toast(p==="granted"?"Pop-ups are on for this browser.":"Pop-ups were not allowed."); renderAlertsTab(); });
$("testBtn").addEventListener("click", async ()=>{
  $("testBtn").disabled=true;
  try { const r=await (await fetch("/api/test-alert",{method:"POST"})).json(); toast("Test sent: "+Object.entries(r).map(([k,v])=>`${k} ${v}`).join(", "),7000); beep(); }
  catch(e){ toast("The FloodGuard service did not answer."); }
  $("testBtn").disabled=false; renderAlertsTab(); });
function beep(){ try{ const A=new (window.AudioContext||window.webkitAudioContext)(); const o=A.createOscillator(), g=A.createGain(); o.connect(g); g.connect(A.destination); o.frequency.value=880; g.gain.value=.08; o.start(); setTimeout(()=>{o.frequency.value=660;},180); setTimeout(()=>{o.stop(); A.close();},420); }catch(e){} }
function announce(a){
  const txt=(a.demo?"[Demo] ":"")+a.text.split("\n")[0];
  toast(txt, 9000); beep();
  if (notifOn()) try{ new Notification("FloodGuard: "+a.level_name, {body:a.text.slice(0,240), tag:"fg-"+a.time_utc}); }catch(e){}
}

/* ---------- live connection to the FloodGuard service ---------- */
async function loadEngine(){
  if (SRV.E) return SRV.E;
  const a = await (await fetch("/engine_assets.json")).json();
  SRV.E = await FG.load(a); return SRV.E;
}
function classPng(E,c){
  const S=E.stage2, cv=document.createElement("canvas"); cv.width=S.w; cv.height=S.h;
  const ctx=cv.getContext("2d"), img=ctx.createImageData(S.w,S.h), px=img.data;
  const COL=[null,[254,217,118,150],[253,141,60,205],[189,0,38,235]];
  for (let i=0;i<S.nCells;i++){ const o=(S.rows[i]*S.w+S.cols[i])*4; let col=COL[c[i]];
    if (!col && S.refcls[i]===3 && !S.river[i]) col=[96,165,250,170];
    if (col){ px[o]=col[0]; px[o+1]=col[1]; px[o+2]=col[2]; px[o+3]=col[3]; } }
  ctx.putImageData(img,0,0); return cv.toDataURL("image/png");
}
async function computeMap(){
  const s=V.stage1;
  if (!s.Naraj){ return; }
  const lv={Naraj:s.Naraj.fc,"Kishan Nagar":s["Kishan Nagar"]?s["Kishan Nagar"].fc:NaN,Tarpur:s.Tarpur?s.Tarpur.fc:NaN};
  const rs={1:+V.rain_sums["1"],3:+V.rain_sums["3"],7:+V.rain_sums["7"]};
  const key=JSON.stringify([lv,rs]);
  if (key===SRV.mapKey && V.map && V.map.valid===s.Naraj.valid) return;
  if (SRV.busy) return; SRV.busy=true;
  try{
    const E=await loadEngine();
    renderMap("Updating the flood map");
    const res=await FG.stage2(E, lv, rs, f=>{ const b=document.querySelector("#map .busy"); if(b) b.textContent=`Updating the flood map, ${(f*100).toFixed(0)}%`; });
    const c=FG.classes(E,res.prob,lv.Naraj);
    const n=G.area_names.length, ak=new Float64Array(n), ae=new Float64Array(n); let built=0, crop=0;
    for (let i=0;i<c.length;i++){ if (c[i]===3){ ak[AREA[i]]+=0.01; built+=BUILT[i]/255*0.01; crop+=CROP[i]/255*0.01; } else if (c[i]===2) ae[AREA[i]]+=0.01; }
    // built-up land within 500 m (5 cells) of flood-likely land: separable max filter on the grid
    const W=G.w, H=G.h, g=new Uint8Array(W*H), t=new Uint8Array(W*H);
    for (let i=0;i<c.length;i++) if (c[i]===3) g[CELL_R[i]*W+CELL_C[i]]=1;
    for (let r=0;r<H;r++) for (let q=0;q<W;q++){ let m=0; for (let k=Math.max(0,q-5);k<=Math.min(W-1,q+5)&&!m;k++) m=g[r*W+k]; t[r*W+q]=m; }
    let builtNear=0;
    for (let i=0;i<c.length;i++){ const r=CELL_R[i], q=CELL_C[i]; let m=0; for (let k=Math.max(0,r-5);k<=Math.min(H-1,r+5)&&!m;k++) m=t[k*W+q]; if (m) builtNear+=BUILT[i]/255*0.01; }
    const sh=await FG.shapMean(E,res,c);
    const ar=FG.areas(c);
    V.map=Object.assign({valid:s.Naraj.valid, issue:s.Naraj.issue, levels:lv, rain_mm:{"1":rs[1],"3":rs[3],"7":rs[7]}, png:classPng(E,c), places:FG.placeClasses(E,c,G.places), shap:sh,
      areas:{area_km2:Array.from(ak,x=>Math.round(x*100)/100), area_elev_km2:Array.from(ae,x=>Math.round(x*100)/100), built_km2:Math.round(built*100)/100, built_near_km2:Math.round(builtNear*100)/100, crop_km2:Math.round(crop*100)/100, km2_likely:ar.km2_likely}}, ar);
    SRV.mapKey=key;
  } catch(e){ console.warn("map update failed", e); toast("The flood map could not be updated in this browser; the river forecast is still current."); }
  finally { SRV.busy=false; renderMap(); renderBulletin(); renderBand(); }
  const s2=V.stage1;                                   // a newer update arrived while drawing: draw again
  if (s2.Naraj && JSON.stringify([{Naraj:s2.Naraj.fc,"Kishan Nagar":s2["Kishan Nagar"]?s2["Kishan Nagar"].fc:NaN,Tarpur:s2.Tarpur?s2.Tarpur.fc:NaN},
      {1:+V.rain_sums["1"],3:+V.rain_sums["3"],7:+V.rain_sums["7"]}])!==SRV.mapKey) computeMap();
}
function viewFromServer(L){
  return { mode:L.mode, genMs:Date.parse(L.generated_utc), gauges:L.gauges, stage1:L.stage1, level:L.level, data_state:L.data_state, delayed:L.delayed,
    upstream_missing:L.upstream_missing, hourly:L.hourly, rain:L.rain, rain_sums:L.rain_sums, demo:L.demo, next_update_utc:L.next_update_utc,
    map: V.map && V.mode!=="snapshot" ? V.map : Object.assign({}, D.maps.live, {valid:null}) };
}
async function poll(){
  try{
    const L=await (await fetch("/api/latest",{cache:"no-store"})).json();
    SRV.fails=0;
    if (L && L.generated_utc && L.generated_utc!==SRV.lastGen){
      SRV.lastGen=L.generated_utc; const first = V.mode==="snapshot";
      V=viewFromServer(L); renderLive(); computeMap();
      if (first) toast(L.mode==="demo"?"Demo replay running.":"Connected. This page now updates by itself.");
    } else renderConn();
    const al=await (await fetch("/api/alerts",{cache:"no-store"})).json();
    const lastT = al.length ? al[al.length-1].time_utc+al.length : "";
    if (SRV.alertsSeen!==null && lastT!==SRV.alertsSeen && al.length){ const a=al[al.length-1]; if (a.kind!=="test") announce(a); }
    SRV.alertsSeen=lastT; if (done.alerts && !$("v-alerts").hidden) drawAlertLog(al);
  } catch(e){ SRV.fails++; renderConn(); }
}
async function connect(){
  if (!/^https?:/.test(location.protocol)){ renderConn(); return; }
  try{
    SRV.status=await (await fetch("/api/status",{cache:"no-store"})).json(); SRV.on=true;
    $("refreshBtn").hidden=false;
    if (!$("v-alerts").hidden) renderAlertsTab();
    await poll();
    const loop=async()=>{ await poll(); setTimeout(loop, V.mode==="demo" ? 3000 : 30000); };
    setTimeout(loop, 30000);
    setInterval(async ()=>{ try{ SRV.status=await (await fetch("/api/status",{cache:"no-store"})).json(); }catch(e){} }, 120000);
  } catch(e){ SRV.on=false; renderConn(); }
}
$("refreshBtn").addEventListener("click", async ()=>{ try{ await fetch("/api/refresh",{method:"POST"}); toast("Reading the gauges now. The page updates in about a minute."); setTimeout(poll,20000); setTimeout(poll,45000); }catch(e){ toast("The FloodGuard service did not answer."); } });

/* ---------- demo menu ---------- */
function renderDemoPop(){
  const pop=$("demoPop");
  if (!SRV.on){ pop.innerHTML=`<p class="small">The demo replays a real 2026 flood through the live system, so alerts and the flood map can be shown on a dry day.</p><p class="small muted">It needs the FloodGuard service: double-click <b>START_FLOODGUARD.bat</b>, then open <b>http://localhost:8765</b>.</p>`; return; }
  const demos=(SRV.status&&SRV.status.demos)||{};
  pop.innerHTML=`<p class="small">Replay a real 2026 flood, one day every few seconds, through the same models and alerts as live. Alerts are marked DEMO.</p>`+
    Object.entries(demos).map(([k,l])=>`<button class="opt" type="button" data-k="${k}"><b>Start:</b> ${esc(l)}</button>`).join("")+
    (V.mode==="demo"?`<button class="btn" type="button" id="stopDemo">Stop demo, back to live</button>`:"");
  pop.querySelectorAll(".opt").forEach(b=>b.addEventListener("click", async ()=>{ await fetch("/api/demo/start?scenario="+encodeURIComponent(b.dataset.k),{method:"POST"}); pop.hidden=true; $("demoBtn").setAttribute("aria-expanded","false"); toast("Demo starting."); setTimeout(poll,1500); }));
  const sd=$("stopDemo"); if (sd) sd.addEventListener("click", stopDemo);
}
async function stopDemo(){ try{ await fetch("/api/demo/stop",{method:"POST"}); toast("Back to live."); $("demoPop").hidden=true; SRV.mapKey=null; setTimeout(poll,800); }catch(e){} }
$("demoBtn").addEventListener("click", ()=>{ const p=$("demoPop"); p.hidden=!p.hidden; $("demoBtn").setAttribute("aria-expanded",String(!p.hidden)); if(!p.hidden) renderDemoPop(); });
document.addEventListener("click", e=>{ if (!e.target.closest(".menu")) { $("demoPop").hidden=true; $("demoBtn").setAttribute("aria-expanded","false"); } });

/* ---------- theme ---------- */
function applyTheme(t){ if (t==="light"||t==="dark") document.documentElement.setAttribute("data-theme",t); else document.documentElement.removeAttribute("data-theme"); }
applyTheme(store.get("theme",""));
$("themeBtn").addEventListener("click",()=>{ const dark = document.documentElement.getAttribute("data-theme")==="dark" || (!document.documentElement.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
  const t = dark ? "light" : "dark"; applyTheme(t); store.set("theme",t); });

/* ---------- season 2026 ---------- */
let seasonSt="Naraj";
function renderSeason(){
  const st=seasonSt, S=D.series[st].filter(r=>r[0]>="2026-06-01"), th=D.thresholds[st], Q=D.verification_2026[st];
  const last=D.series[st].filter(r=>fin(r[4])).slice(-1)[0];
  $("seasonIntro").textContent=`The live models learned from 2014–2025 only. Each 2026 forecast was issued one day ahead from the CWC readings and rain available that day, then compared with the gauge the next day. Scored to ${dLong(last[0])}.`;
  const P=S.map(r=>[dParse(r[0]).getTime(),r[1],r[2],r[3],r[4]]);
  timeChart($("seasonChart"),{w:1100,h:320,lines:[{pts:P.map(p=>[p[0],p[4]]),col:"var(--chart-obs)",w:2},{pts:P.map(p=>[p[0],p[1]]),col:"var(--chart-fc)",w:1.8,dash:"5 3"}],
    bands:[{pts:P.map(p=>[p[0],p[2],p[3]]),col:"var(--chart-band)"}],hl:[{y:th.warning,col:"var(--warning)",label:"warning "+f2(th.warning),force:true},{y:th.danger,col:"var(--danger)",label:"danger "+f2(th.danger),force:true}],aria:`${st} 2026 forecast versus observed`});
  const ci=Q.warning_ci;
  const k=[["Typical error (RMSE)",f2(Q.RMSE,2)+" m",`"tomorrow = today": ${f2(Q.RMSE_persistence,2)} m`],["Skill",f2(Q.skill_vs_persistence,2),`monsoon Jun–Oct ${f2(Q.skill_monsoon,2)}`],
    ["Warning days caught",`${Q.warning.hits} of ${Q.days_above_warning_obs}`,`${Q.warning.false_alarms} false alarm${Q.warning.false_alarms===1?"":"s"}${ci?`, 95% range ${(ci.pod_lo*100).toFixed(0)}–${(ci.pod_hi*100).toFixed(0)}%`:""}`],["Inside the 80% range",(Q.inside_80pct_band*100).toFixed(0)+"%",`${Q.n_days} days, peak ${f2(Q.peak_obs)} m on ${dLab(dParse(Q.peak_obs_date))}`]];
  $("seasonKpis").innerHTML=k.map(x=>`<div class="kpi"><span class="l">${x[0]}</span><span class="v">${x[1]}</span><span class="s">${x[2]}</span></div>`).join("");
}
function renderSeasonStatic(){
  let h=`<table><thead><tr><th>Gauge</th><th>Method</th><th class="n">Caught</th><th class="n">Missed</th><th class="n">False alarms</th><th class="n">CSI</th></tr></thead><tbody>`;
  TG.forEach(st=>{ const Q=D.verification_2026[st]; [["FloodGuard",Q.warning,true],["Tomorrow = today",Q.warning_persistence,false]].forEach(([n,c,b])=>{
    h+=`<tr class="${b?"best":""}"><td>${b?st:""}</td><td>${n}</td><td class="n">${c.hits}</td><td class="n">${c.misses}</td><td class="n">${c.false_alarms}</td><td class="n">${f2(c.CSI)}</td></tr>`; }); });
  $("seasonTable").innerHTML=h+"</tbody></table>";
  const A=D.area_2026.filter(a=>a.valid>="2026-06-01"), obs=D.series.Naraj.filter(r=>r[0]>="2026-06-01");
  timeChart($("areaChart"),{w:620,h:240,bars:A.map(a=>[dParse(a.valid).getTime(),a.km2]),barCol:"var(--danger)",
    right:{pts:obs.map(r=>[dParse(r[0]).getTime(),r[4]]),col:"var(--chart-obs)",hl:[{y:D.thresholds.Naraj.warning,col:"var(--warning)",label:"Naraj warning"}]},aria:"forecast flood-likely area in 2026"});
  const L=[]; TG.forEach(st=>D.verification_2026[st].lead_time.forEach(e=>L.push(Object.assign({st},e))));
  L.sort((a,b)=>a.onset<b.onset?-1:a.onset>b.onset?1:TG.indexOf(a.st)-TG.indexOf(b.st));
  const n=L.length, watch24=L.filter(e=>e.watch_h>=24).length, warn24=L.filter(e=>e.lead_h>=24).length;
  const sorted=L.map(e=>e.watch_h).sort((x,y)=>x-y), med=!n?0:sorted.length%2?sorted[(sorted.length-1)/2]:(sorted[sorted.length/2-1]+sorted[sorted.length/2])/2;
  $("leadKpis").innerHTML=[["Watch at least 24 h ahead",`${watch24} of ${n}`,"times a gauge crossed its warning level"],["Typical (median) notice",`${med} h`,"before the crossing day"],["Warning at least 24 h ahead",`${warn24} of ${n}`,"forecast already above the level"]]
    .map(x=>`<div class="kpi"><span class="l">${x[0]}</span><span class="v">${x[1]}</span><span class="s">${x[2]}</span></div>`).join("");
  const cell=h=>h?`<span class="cls ${h>=48?"lead2":"lead1"}">${h} h ahead</span>`:`<span class="cls c0">not ahead</span>`;
  $("leadTable").innerHTML=`<table><thead><tr><th>Gauge</th><th>Crossed warning</th><th class="n">Peak</th><th>Watch</th><th>Warning</th></tr></thead><tbody>`+
    L.map(e=>`<tr><td>${e.st}</td><td>${dLong(e.onset)}</td><td class="n">${f2(e.peak)} m</td><td>${cell(e.watch_h)}</td><td>${cell(e.lead_h)}</td></tr>`).join("")+
    `</tbody></table><p class="tiny muted" style="padding-top:6px">"Not ahead": no forecast issued before the crossing day flagged it. The "tomorrow = today" rule can never give advance notice.</p>`;
}
function radarViewer(ids, cases, initial){
  let cur=initial, layer="agreement";
  const render=()=>{
    const C=cases[cur], cut=C.cut||D.p_likely;
    const L={observed:[C.observed_png],forecast:[C.forecast_png],agreement:[C.agreement_png]}[layer];
    buildMap($(ids.map),[C.nodata_png].concat(L),{places:false});
    $(ids.key).innerHTML={observed:`<span><i style="background:rgba(37,99,235,.86)"></i>Radar: river-connected flood</span>`,
      forecast:`<span><i style="background:rgba(189,0,38,.92)"></i>Flood likely (P ≥ ${f2(cut,2)})</span><span><i style="background:rgba(253,141,60,.8)"></i>Elevated</span><span><i style="background:rgba(254,217,118,.75)"></i>Watch</span>`,
      agreement:`<span><i style="background:rgb(26,152,80)"></i>Correct: flooded in both</span><span><i style="background:rgb(245,158,11)"></i>False alarm</span><span><i style="background:rgb(118,42,131)"></i>Missed</span>`}[layer]+`<span><i style="background:rgba(120,120,120,.4)"></i>Outside the radar scene</span>`;
    const sc=C.scores;
    $(ids.kpis).innerHTML=[["Overlap (F1)",f2(sc.F1_Dice,2)],["IoU",f2(sc.IoU_CSI,2)],["Mapped area ÷ real area",f2(sc.area_ratio,2)],["ROC-AUC",f2(sc.ROC_AUC,3)]]
      .map(x=>`<div class="kpi"><span class="l">${x[0]}</span><span class="v">${x[1]}</span></div>`).join("");
    const o=C.original;
    $(ids.note).innerHTML=esc(`Issued ${dLong(C.issue)} for ${dLong(C.valid)}. Forecast Naraj ${f2(C.naraj_fc)} m; observed ${f2(C.naraj_obs)} m. Precision ${f2(sc.precision,2)}, recall ${f2(sc.recall,2)} over ${sc.obs_flooded_cells} radar-flooded cells`+
      (fin(C.coverage)?`; this radar scene covered ${(C.coverage*100).toFixed(0)}% of the study area.`:"."))+
      (o?`<br>Before the Stage 2 improvement this map scored F1 ${f2(o.F1_Dice,2)} with ${f2(o.area_ratio,2)} of the real area.`:"");
  };
  seg($(ids.seg), Object.keys(cases).map(k=>[k,dLong(cases[k].valid)]), cur, k=>{cur=k;render();});
  seg($(ids.layer), [["agreement","Right and wrong"],["forecast","Forecast"],["observed","Radar"]], layer, k=>{layer=k;render();});
  render();
}
function renderS1Table(){
  const C=Object.values(D.s1_2026), mean=(k,o)=>C.reduce((a,c)=>a+(o?c.original[k]:c.scores[k]),0)/C.length, I=D.improvement.stage2;
  $("s1Table").innerHTML=`<table><thead><tr><th>Scenes</th><th class="n">F1</th><th class="n">IoU</th></tr></thead><tbody>
    <tr class="best"><td>2026, unseen year (${C.length} scenes)</td><td class="n">${f2(mean("F1_Dice"),2)}</td><td class="n">${f2(mean("IoU_CSI"),2)}</td></tr>
    ${C[0].original?`<tr><td>2026, before the Stage 2 improvement</td><td class="n">${f2(mean("F1_Dice",1),2)}</td><td class="n">${f2(mean("IoU_CSI",1),2)}</td></tr>`:""}
    <tr><td>2015–2025 cross-validation (${I.n_flood_scenes} floods)</td><td class="n">${f2(I.loyo.F1_flood,2)}</td><td class="n">${f2(I.loyo.IoU_flood,2)}</td></tr></tbody></table>`;
}

/* ---------- validation ---------- */
let valSt="Naraj";
function renderValTable(){
  const rows=D.validation.stage1.filter(r=>r.target===valSt), E=D.validation.ensemble[valSt], best=Math.min(...rows.map(r=>r.RMSE),E.RMSE);
  const O=(D.improvement.stage1||[]).filter(r=>r.target===valSt);
  let h=`<table><thead><tr><th>Model</th><th class="n">Error (RMSE)</th><th class="n">Skill</th><th class="n">Warning CSI</th></tr></thead><tbody>`;
  rows.forEach(r=>{ h+=`<tr class="${r.RMSE===best?"best":""}"><td>${esc(r.model==="RandomForest"?"Random forest":r.model==="Persistence"?"Tomorrow = today":r.model)}</td><td class="n">${f2(r.RMSE,3)} m</td><td class="n">${f2(r.skill_vs_persistence,2)}</td><td class="n">${f2(r.CSI,2)}</td></tr>`; });
  h+=`<tr class="${E.RMSE===best?"best":""}"><td><b>Ensemble (Ridge + RF + XGBoost)</b></td><td class="n">${f2(E.RMSE,3)} m</td><td class="n">${f2(E.skill_vs_persistence,2)}</td><td class="n">${f2(E.CSI,2)}</td></tr>`;
  O.filter(r=>r.model.startsWith("rolling")).forEach(r=>{ h+=`<tr><td>Ensemble, retrained every year (live protocol)</td><td class="n">${f2(r.RMSE,3)} m</td><td class="n">${f2(r.skill_vs_persistence,2)}</td><td class="n">${f2(r.CSI,2)}</td></tr>`; });
  $("valTable").innerHTML=h+`</tbody></table><p class="tiny muted" style="padding-top:6px">${E.n} test days. Highlighted: lowest error. Upper rows: models trained on 2014–2020. Last row: each test year forecast by models trained only on the years before it, the same way the live models (trained on 2014–2025) are used in 2026.</p>`;
  $("valIntro").textContent="Error of tomorrow's highest level. The live system uses the ensemble.";
}
function renderLead(){
  const L=D.validation.lead_time, W=560,H=240,m={l:40,r:10,t:16,b:30}, el=$("leadChart"); el.innerHTML="";
  const s=svgEl("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"skill by lead time"},el);
  const Y=v=>H-m.b-v*(H-m.t-m.b), g=svgEl("g",{class:"grid"},s);
  [0,.2,.4,.6,.8,1].forEach(t=>{ svgEl("line",{x1:m.l,x2:W-m.r,y1:Y(t),y2:Y(t)},g); const tx=svgEl("text",{x:m.l-6,y:Y(t)+4,"text-anchor":"end"},s); tx.textContent=t.toFixed(1); });
  const cols=["var(--chart-fc)","var(--warning)","var(--normal)"], gw=(W-m.l-m.r)/3, bw=gw/4.2;
  [1,2,3].forEach((hz,i)=>{ TG.forEach((st,j)=>{ const r=L.find(x=>x.target===st&&x.horizon_days===hz), x=m.l+i*gw+gw*.12+j*bw*1.1;
    svgEl("rect",{x,y:Y(r.skill_vs_persistence),width:bw,height:Y(0)-Y(r.skill_vs_persistence),rx:2,style:`fill:${cols[j]}`},s);
    const tx=svgEl("text",{x:x+bw/2,y:Y(r.skill_vs_persistence)-4,"text-anchor":"middle",style:"font-size:9px"},s); tx.textContent=r.skill_vs_persistence.toFixed(2); });
    const tx=svgEl("text",{x:m.l+i*gw+gw/2,y:H-8,"text-anchor":"middle",style:"font-weight:700"},s); tx.textContent="+"+hz*24+" h"; });
  $("leadLegend").innerHTML=TG.map((st,j)=>`<span><i style="background:${cols[j]}"></i>${st}</span>`).join("");
}
function renderValidation(){
  radarViewer({map:"caseMap",key:"caseKey",kpis:"caseKpis",note:"caseNote",seg:"caseSeg",layer:"caseLayer"}, D.validation.cases, Object.keys(D.validation.cases)[0]);
  seg($("valSeg"), TG.map(t=>[t,t]), valSt, k=>{valSt=k;renderValTable();}); renderValTable(); renderLead();
  const I=D.improvement.stage2;
  $("s2Kpis").innerHTML=[["Overlap with radar (F1), floods",f2(I.loyo.F1_flood,2),`before: ${f2(I.original.F1_flood,2)}`],["IoU, floods",f2(I.loyo.IoU_flood,2),`before: ${f2(I.original.IoU_flood,2)}`],
    ["2022 record flood, F1",f2(I.loyo.F1_2022,2),`before: ${f2(I.original.F1_2022,2)}`],["2022, mapped ÷ real area",f2(I.loyo.area_ratio_2022,2),`before: ${f2(I.original.area_ratio_2022,2)}`]]
    .map(x=>`<div class="kpi"><span class="l">${x[0]}</span><span class="v">${x[1]}</span><span class="s">${x[2]}</span></div>`).join("");
  const cv=D.validation.stage2_cv, nm={Bathtub:"Bathtub (river level above ground)",LogReg:"Logistic regression",RandomForest:"Random forest",XGBoost:"XGBoost"};
  let h=`<table><thead><tr><th>Map model</th><th class="n">F1</th><th class="n">IoU</th><th class="n">Area ratio</th><th class="n">2022 F1</th></tr></thead><tbody>`;
  cv.forEach(r=>{ h+=`<tr><td>${esc(nm[r.model]||r.model)}, cut-off 0.5</td><td class="n">${f2(r.F1_Dice_flood_events,2)}</td><td class="n">${f2(r.IoU_CSI_flood_events,2)}</td><td class="n">${f2(r.area_ratio_flood_events,2)}</td><td class="n">–</td></tr>`; });
  (D.improvement.stage2_variants||[]).forEach(r=>{ const best = r.model.includes("monotone")&&r.connectivity&&r.cutoff==="level-aware";
    const orig = r.cutoff.startsWith("fixed");
    if (!best && !orig) return;
    h+=`<tr class="${best?"best":""}"><td>${best?"Used live: XGBoost that can only rise with the river, cut-off that eases above warning, river-connected patches only":"Before: XGBoost, fixed cut-off 0.90"}</td><td class="n">${f2(r.F1_flood,2)}</td><td class="n">${f2(r.IoU_flood,2)}</td><td class="n">${f2(r.area_ratio_flood_median,2)}</td><td class="n">${f2(r.F1_2022,2)}</td></tr>`; });
  $("s2Table").innerHTML=h+`</tbody></table><p class="tiny muted" style="padding-top:6px">Averages over the ${I.n_flood_scenes} radar scenes with Naraj above the warning level; area ratio is the median. Every variant of the improvement was scored the same way; they have the same average accuracy, and the one used live is the only one that is physically consistent and it maps the largest floods best. Same data, same stations, same inputs.</p>`;
  const E=(D.improvement.stage2_events||[]).filter(r=>r.kind==="flood_above_warning").sort((a,b)=>b.naraj-a.naraj);
  $("s2Events").innerHTML=`<table><thead><tr><th>Flood scene</th><th class="n">Naraj</th><th class="n">F1 before</th><th class="n">F1 now</th><th class="n">Area before</th><th class="n">Area now</th></tr></thead><tbody>`+
    E.map(r=>`<tr><td>${esc(r.event_id.slice(1,5)+"-"+r.event_id.slice(5,7)+"-"+r.event_id.slice(7,9))}</td><td class="n">${f2(r.naraj)} m</td><td class="n">${f2(r.F1_original,2)}</td><td class="n"><b>${f2(r.F1,2)}</b></td><td class="n">${f2(r.area_ratio_original,2)}</td><td class="n"><b>${f2(r.area_ratio,2)}</b></td></tr>`).join("")+`</tbody></table>`;
  const Gl=D.validation.shap_global.slice(0,10), mx=Gl[0].mean_abs_shap;
  $("shapGlobal").innerHTML=Gl.map(r=>{ const k=Object.values(r)[0]; return `<div class="bar"><span class="name">${esc(FEAT[k]||k)}</span><span class="track"><span class="fill" style="left:0;width:${r.mean_abs_shap/mx*100}%;background:var(--river)"></span></span><span class="v">${f2(r.mean_abs_shap,2)}</span></div>`; }).join("");
}

/* ---------- method ---------- */
function renderMethod(){
  const rc=D.sources.rain_calibration, lc=D.sources.levels_overlap_check, R=D.stage2_rule;
  const s1=[["1","River readings","Hourly levels from the CWC Flood Forecasting System for Tikarpara, Naraj, Kishan Nagar and Tarpur, turned into daily average, highest and lowest."],
    ["2","Checks",`Readings outside each gauge's plausible range and single-hour spikes are removed; late gauges are flagged. The live feed matches the archive: ${(lc.Naraj.share_identical*100).toFixed(1)}% of 2025 Naraj days have the identical highest level.`],
    ["3","77 inputs per day","Seven days of levels at the gauge and upstream, 1–2 day changes, 3 and 7-day highs, rain over four sub-catchments (today, lags, 3/5/7/10-day totals) and season."],
    ["4","Three models, one forecast","Ridge regression, random forest and XGBoost, trained on 2014–2025, each predict the change in tomorrow's highest level. Their average is the forecast; past errors give the 80% range."]];
  const s2=[["5","100 m grid","219,017 cells with ground height, slope, height above the river, distance to the channel, past water and land cover."],
    ["6","Flood probability","XGBoost trained on 48 Sentinel-1 radar scenes (2015–2025), constrained so the probability can only rise when the river rises. Inputs add the forecast levels and recent rain."],
    ["7","Flood-likely land",`Cut-off ${f2(R.cut.c0,2)} up to the Naraj warning level, easing by ${f2(R.cut.slope,2)} per metre above it (never below ${f2(R.cut.floor,2)}). Only patches connected to the river count. Normal monsoon water is shown separately.`],
    ["8","Warn","Areas at risk are named after the nearest town or village. Alerts go out when the level changes, to the screen, Telegram, e-mail or SMS."]];
  const card=([n,h,p])=>`<div class="stepc"><span class="no">${n}</span><h3>${esc(h)}</h3><p>${esc(p)}</p></div>`;
  $("flow1").innerHTML=s1.map(card).join(""); $("flow2").innerHTML=s2.map(card).join("");
  $("eqs").textContent=
`Stage 1, per gauge, issue day t
  Δ_m    = f_m(x_t)                  m ∈ {Ridge, RF, XGBoost}
  ŷ(t+1) = max(t) + (Δ_Ridge + Δ_RF + Δ_XGB) / 3
  80% range = ŷ + [q10, q90] of out-of-sample errors
              (each year 2022–25 forecast by models trained
               only on earlier years; days within 2 m of warning
               kept separately)
  P(exceed) = share of those errors e with ŷ + e ≥ warning

Live rainfall, per zone
  R = Open-Meteo mean over zone points × k
  k = ${Object.values(D.rain_scale).map(x=>x.toFixed(3)).join(", ")}   (CHIRPS ÷ Open-Meteo, Jun–Oct 2022–25)
  7-day correlation with CHIRPS = ${Object.values(rc.corr_7day_vs_chirps).map(x=>x.toFixed(2)).join(", ")}

Stage 2, per 100 m cell i
  depth_i = level at nearest gauge − ground height_i
  P_i     = XGBoost(terrain_i, land cover_i, levels, depth_i, rain)
            monotone: P_i never falls when a river level rises
  cut(L)  = max(${f2(R.cut.floor,2)}, ${f2(R.cut.c0,2)} − ${f2(R.cut.slope,2)} × max(0, L − ${f2(R.cut.pivot_naraj_m,2)}))
  flood likely ⇔ P_i ≥ cut(Naraj forecast), the patch touches the
                 river, and the cell is not normal monsoon water`;
  $("liveHow").innerHTML=`<p>The FloodGuard service runs on the presentation laptop (START_FLOODGUARD.bat, or automatically at log-in). Every 30 minutes it reads the four CWC gauges and the rainfall, checks them, runs the river-level models and decides the alert level. When the level changes it sends the alert to every channel that is switched on.</p>
    <p>This page asks the service for news every 30 seconds and draws each update by itself. The flood map is computed in the page with the same model file; a check shows the page and the Python pipeline give identical results on every cell.</p>`;
  $("sourcesTable").innerHTML=`<table><thead><tr><th>Input</th><th>Source</th></tr></thead><tbody>
    <tr><td>River level</td><td>CWC Flood Forecasting System, stations ${Object.values(D.sources.cwc.codes).join(", ")}</td></tr>
    <tr><td>Rain, live and forecast</td><td>Open-Meteo, ${Object.values(D.zone_points).reduce((a,p)=>a+p.length,0)} points in 4 sub-catchments</td></tr>
    <tr><td>Rain, 2014–2025</td><td>CHIRPS v3 daily (training record)</td></tr>
    <tr><td>Terrain, land cover, past water</td><td>SRTM 30 m, ESA WorldCover 2021, JRC Global Surface Water</td></tr>
    <tr><td>Flood maps for training</td><td>Sentinel-1 radar: 48 scenes 2015–2025; 3 scenes 2026 for testing only</td></tr>
    <tr><td>Place names</td><td>OpenStreetMap (${G.places.length} towns and villages in the study area)</td></tr></tbody></table>`;
  const lim=["Live updates need the FloodGuard service running on a computer that is switched on and online. Viewers of the saved file see the run it was made from, labelled with its time.",
    "The current day's highest level uses the readings so far; on a rising river the full-day value can still climb, so evening forecasts are the most reliable.",
    `Record floods are still mapped a little small (2022: ${f2(D.improvement.stage2.loyo.area_ratio_2022,2)} of the real area, up from ${f2(D.improvement.stage2.original.area_ratio_2022,2)}); only two training scenes are above Naraj 26.5 m.`,
    "The warning and danger levels at Kishan Nagar and Tarpur are derived from Naraj; CWC does not publish them.",
    "Releases from Hirakud dam are not a direct input; they reach Naraj through Tikarpara about a day later, which limits the 2 and 3-day outlook.",
    "Satellite radar cannot see water between buildings, so the training maps (and therefore the model) rarely mark built-up cells as flooded. The page therefore reports homes and buildings within 500 m of flood-likely land.",
    "Areas at risk are named after the nearest of the 26 OpenStreetMap places, so one name can cover several villages. The terrain is SRTM, without embankments."];
  $("limits").innerHTML="<ul>"+lim.map(x=>`<li>${esc(x)}</li>`).join("")+"</ul>";
}

/* ---------- tabs and boot ---------- */
const VIEWS=["live","alerts","season","validation","method"], done={live:true};
function show(v){
  if(!VIEWS.includes(v)) v="live";
  VIEWS.forEach(k=>{ $("v-"+k).hidden=k!==v; });
  document.querySelectorAll(".tab").forEach(t=>t.setAttribute("aria-selected",String(t.dataset.v===v)));
  if (v==="alerts") renderAlertsTab();
  if(!done[v]){ done[v]=true;
    if(v==="season"){ seg($("seasonSeg"),TG.map(t=>[t,t]),seasonSt,k=>{seasonSt=k;renderSeason();}); renderSeason(); renderSeasonStatic();
      radarViewer({map:"s1Map",key:"s1Key",kpis:"s1Kpis",note:"s1Note",seg:"s1Seg",layer:"s1Layer"}, D.s1_2026, Object.keys(D.s1_2026)[0]); renderS1Table(); }
    if(v==="validation") renderValidation();
    if(v==="method") renderMethod(); }
}
document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>{ show(t.dataset.v); try{ history.replaceState(null,"","#"+t.dataset.v);}catch(e){} }));
seg($("hydroSeg"),TG.map(t=>[t,t]),hydroSt,k=>{hydroSt=k;renderHydro();});
seg($("rainSeg"),Object.entries(ZN),rainZ,k=>{rainZ=k;renderRain();});
initMapControls(); renderLive();
show((location.hash||"#live").slice(1));
connect();
})();
