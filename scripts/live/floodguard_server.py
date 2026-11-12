#!/usr/bin/env python3
"""
FloodGuard live server - the part that makes the system run by itself.

Start it with START_FLOODGUARD.bat (Windows) or:   python scripts/live/floodguard_server.py
Then the dashboard is at http://localhost:8765 and updates itself. Nobody has to click.

Every 30 minutes (config/live.json) it:
  1. reads the four CWC gauges (Tikarpara, Naraj, Kishan Nagar, Tarpur) from the CWC Flood
     Forecasting System and the rainfall over the four sub-catchments from Open-Meteo;
  2. checks the readings (plausible range, single-hour spikes, how old the last reading is);
  3. runs the Stage 1 models (+24 h, +48 h, +72 h) with the same model file as the dashboard;
  4. decides the alert level (Normal / Watch / Warning / Danger) and, when it changes, sends
     an alert to every channel switched on in config/alerts.json (desktop pop-up, Telegram,
     e-mail, SMS);
  5. writes 08_results/live/latest.json, which the open dashboard picks up within a minute
     and turns into the flood map (Stage 2 runs in the browser).

Safety rules
  * A gauge whose last reading is older than `stale_hours` is marked DELAYED. The page then
    says so in a grey banner; it never shows "Normal" from old data, and no "All clear" is
    sent while data is delayed.
  * If one gauge is missing, the forecast still runs (the models accept a missing input) and
    the page says which gauge is missing. If a forecast gauge itself is missing, that gauge
    shows "No data".
  * Lowering an alert needs two runs in a row at the lower level (no flapping).
  * Every run is logged in 08_results/live/run_log.csv.

Only the Python standard library is used, so any Python 3.8+ works (Anaconda included).
This is a research prototype, not an official CWC or OSDMA warning.
"""
import csv, json, math, os, platform, smtplib, ssl, subprocess, sys, threading, time, traceback
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fg_engine as FG  # noqa: E402

ROOT = HERE.parents[1]
DASH = ROOT / "09_dashboard"
LIVE = ROOT / "08_results" / "live"
CONF = ROOT / "config"
IST = timezone(timedelta(hours=5, minutes=30))
ALL = ["Tikarpara", "Naraj", "Kishan Nagar", "Tarpur"]
TARGETS = ["Naraj", "Kishan Nagar", "Tarpur"]
CODES = {"Tikarpara": "034-MDBURLA", "Naraj": "036-MDBURLA", "Kishan Nagar": "046-MDBURLA", "Tarpur": "047-MDBURLA"}
ZONES = ["z0_above_hirakud", "z1_hirakud_tikarpara", "z2_tikarpara_naraj", "z3_floodplain"]
RANK = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
LEVEL_NAME = {"GREEN": "Normal", "YELLOW": "Watch", "ORANGE": "Warning", "RED": "Danger"}
UA = {"User-Agent": "FloodGuard research prototype (final-year project)", "Accept": "application/json"}

DEFAULT_LIVE = {
    "port": 8765,
    "allow_phones_on_same_wifi": False,
    "fetch_every_minutes": 30,
    "stale_hours": 6,
    "open_browser_on_start": True,
    "demo_seconds_per_day": 12,
}


# Test mode (tests/test_live_server.py): answer from the recorded CWC / Open-Meteo responses in
# tests/fixtures instead of the internet, with the clock set to the recording time.
FIXTURES = None
if os.environ.get("FG_FIXTURES"):
    _fx = Path(os.environ["FG_FIXTURES"])
    FIXTURES = {"cwc": json.loads((_fx / "ffsgz.json").read_text()), "om": json.loads((_fx / "omgz.json").read_text())}
FAKE_NOW = os.environ.get("FG_NOW")          # e.g. 2026-10-21T10:40:00Z


def now_utc():
    if FAKE_NOW:
        return datetime.fromisoformat(FAKE_NOW.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ist_str(dt):
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, path)


def load_settings():
    s = dict(DEFAULT_LIVE)
    s.update(read_json(CONF / "live.json", {}) or {})
    if os.environ.get("FG_PORT"):
        s["port"] = int(os.environ["FG_PORT"])
    if os.environ.get("FG_LIVE_DIR"):          # tests write their own run folder
        global LIVE
        LIVE = Path(os.environ["FG_LIVE_DIR"])
    return s


def load_alert_config():
    """config/alerts.json (your private copy, created from alerts.example.json)."""
    c = read_json(CONF / "alerts.json")
    if c is None:
        c = read_json(CONF / "alerts.example.json", {}) or {}
    return c


# ============================================================================ static model data
class Static:
    """Everything the server needs that does not change between runs."""

    def __init__(self):
        assets = read_json(DASH / "engine_assets.json")
        if assets is None:
            raise SystemExit("09_dashboard/engine_assets.json is missing. The project folder is incomplete.")
        self.E = FG.load(assets)
        meta = read_json(DASH / "live_static.json")
        if meta is None:
            raise SystemExit("09_dashboard/live_static.json is missing. The project folder is incomplete.")
        self.thresholds = meta["thresholds"]
        self.qc_bounds = meta["qc_bounds"]
        self.rain_scale = meta["rain_scale"]
        self.zone_points = meta["zone_points"]
        self.area_ladder = meta["area_ladder"]          # what-if map: named areas at each Naraj level
        self.demo = meta.get("demo", {})
        self.model_info = meta.get("model_info", {})


# ============================================================================ fetching
def _ssl_contexts():
    """The system certificate store first; if a site's certificate chain cannot be verified with
    it (some government sites omit an intermediate certificate), the certifi bundle that ships
    with Anaconda and requests. Verification is never switched off."""
    ctx = [ssl.create_default_context()]
    try:
        import certifi
        ctx.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    return ctx


SSL_CTX = _ssl_contexts()


def http_json(url, timeout=60, tries=3, data=None, headers=None, method=None):
    last = None
    for k in range(tries):
        for ctx in SSL_CTX:
            try:
                req = Request(url, data=data, headers={**UA, **(headers or {})}, method=method)
                with urlopen(req, timeout=timeout, context=ctx) as r:
                    return json.loads(r.read().decode("utf-8"))
            except ssl.SSLError as e:          # try the next certificate store
                last = e
            except OSError as e:
                if isinstance(getattr(e, "reason", None), ssl.SSLError):
                    last = e
                    continue
                last = e
                break
            except Exception as e:
                last = e
                break
        time.sleep(2 + 3 * k)                  # network hiccup: wait and retry
    raise last


def _enc2(obj):
    s = json.dumps(obj, separators=(",", ":"))
    return quote(quote(s, safe="-_.!~*'()"), safe="-_.!~*'()")


def cwc_url(code, size):
    spec = {"where": {"where": {"expression": {"valueIsRelationField": False, "fieldName": "id.stationCode", "operator": "eq", "value": code}},
                      "and": {"expression": {"valueIsRelationField": False, "fieldName": "id.datatypeCode", "operator": "eq", "value": "HHS"}}},
            "and": {"expression": {"valueIsRelationField": False, "fieldName": "dataValue", "operator": "null", "value": "false"}}}
    sort = {"sortOrderDtos": [{"sortDirection": "DESC", "field": "id.dataTime"}]}
    return ("https://ffs.india-water.gov.in/iam/api/new-entry-data/specification/sorted-page"
            f"?sort-criteria={_enc2(sort)}&page-number=0&page-size={size}&specification={_enc2(spec)}")


def parse_cwc(j):
    """CWC answer -> sorted [[YYYY-MM-DDTHH:MM, level], ...] (IST, as published)."""
    if not isinstance(j, list):
        raise ValueError("CWC answered in an unexpected format")
    out = {}
    for r in j:
        try:
            t = str(r["id"]["dataTime"])[:16]
            v = float(r["dataValue"])
        except Exception:
            continue
        if math.isfinite(v):
            out[t] = v
    return sorted([[t, v] for t, v in out.items()])


def qc_hourly(st, recs, bounds, spike_m=2.5):
    """Remove readings outside the gauge's plausible range and isolated single-hour spikes."""
    lo, hi = bounds
    notes = []
    keep = [r for r in recs if lo <= r[1] <= hi]
    if len(keep) < len(recs):
        notes.append(f"{len(recs) - len(keep)} reading(s) outside {lo:.1f}-{hi:.1f} m removed")
    if st != "Tikarpara" and len(keep) >= 3:      # Tikarpara is exempt, as in the archive QC
        out = [keep[0]]
        for i in range(1, len(keep) - 1):
            a, b, c = keep[i - 1][1], keep[i][1], keep[i + 1][1]
            if abs(b - a) > spike_m and abs(b - c) > spike_m and abs(a - c) < spike_m / 2:
                notes.append(f"spike {keep[i][1]:.2f} m at {keep[i][0].replace('T', ' ')} removed")
                continue
            out.append(keep[i])
        out.append(keep[-1])
        keep = out
    return keep, notes


def fetch_cwc(static):
    hourly, notes, errors = {}, {}, {}
    for st in ALL:
        try:
            j = json.loads(FIXTURES["cwc"][CODES[st]]) if FIXTURES else http_json(cwc_url(CODES[st], 700))
            recs = parse_cwc(j)
            recs, n = qc_hourly(st, recs, static.qc_bounds[st])
            hourly[st], notes[st] = recs, n
        except Exception as e:
            hourly[st], notes[st], errors[st] = [], [], f"{type(e).__name__}: {e}"
    return hourly, notes, errors


def fetch_rain(static):
    """Open-Meteo daily rain (past 14 days + 7-day forecast), averaged over each zone's points
    and rescaled to the CHIRPS record the models learned from (k per zone)."""
    rain, errors = {}, {}
    for z in ZONES:
        pts = static.zone_points[z]
        q = urlencode({"latitude": ",".join(str(a) for a, _ in pts), "longitude": ",".join(str(b) for _, b in pts),
                       "daily": "precipitation_sum", "timezone": "Asia/Kolkata", "past_days": 14, "forecast_days": 7})
        try:
            if FIXTURES:
                j = FIXTURES["om"][z]
                j = json.loads(j) if isinstance(j, str) else j
            else:
                j = http_json("https://api.open-meteo.com/v1/forecast?" + q)
            j = j if isinstance(j, list) else [j]
            days = j[0]["daily"]["time"]
            rain[z] = {}
            for i, d in enumerate(days):
                v = [a["daily"]["precipitation_sum"][i] for a in j]
                v = [x for x in v if x is not None and math.isfinite(x)]
                rain[z][d] = FG.round2(sum(v) / len(v)) * static.rain_scale[z] if v else float("nan")
        except Exception as e:
            rain[z], errors[z] = {}, f"{type(e).__name__}: {e}"
    return rain, errors


# ============================================================================ one forecast run
def interp_areas(ladder, naraj):
    """Named areas at risk at Naraj level L, from the what-if map (linear between steps)."""
    if not ladder or naraj is None or not math.isfinite(naraj):
        return dict(km2_likely=0.0, areas=[], built_km2=0.0, crop_km2=0.0)
    steps = ladder["steps"]
    lv = [s["naraj"] for s in steps]
    if naraj <= lv[0]:
        a = b = steps[0]; w = 0.0
        if naraj < lv[0] - 0.25:
            return dict(km2_likely=0.0, areas=[], built_km2=0.0, crop_km2=0.0)
    elif naraj >= lv[-1]:
        a = b = steps[-1]; w = 0.0
    else:
        i = max(k for k in range(len(lv)) if lv[k] <= naraj)
        a, b = steps[i], steps[i + 1]
        w = (naraj - lv[i]) / (lv[i + 1] - lv[i])
    mix = lambda x, y: round((1 - w) * x + w * y, 1)
    areas = []
    for k, name in enumerate(ladder["areas"]):
        km = mix(a["area_km2"][k], b["area_km2"][k])
        if km >= 0.3:
            areas.append(dict(name=name, km2=km))
    areas.sort(key=lambda r: -r["km2"])
    return dict(km2_likely=mix(a["km2_likely"], b["km2_likely"]), areas=areas,
                built_km2=mix(a["built_km2"], b["built_km2"]), crop_km2=mix(a["crop_km2"], b["crop_km2"]))


def rain_sums(rain_z3, t0):
    out = {}
    for n in (1, 3, 7):
        s = 0.0
        for k in range(n - 1, -1, -1):
            v = rain_z3.get(FG.add_d(t0, -k))
            s += v if v is not None and math.isfinite(v) else 0.0
        out[str(n)] = round(s, 2)
    return out


def build_run(static, hourly, rain, now, mode="live", stale_hours=6, notes=None, errors=None, demo=None):
    """Turn inputs into the bundle the dashboard reads. Used by live and demo runs alike."""
    notes, errors = notes or {}, errors or {}
    daily = {}
    gauges = {}
    for st in ALL:
        H = hourly.get(st) or []
        if demo is not None:          # demo: daily values are given directly
            daily[st] = {d: v[:3] for d, v in demo["daily"][st].items() if d <= demo["day"]}
        else:
            daily[st] = {d: v[:3] for d, v in FG.daily_from_hourly(H).items()}
        if H:
            last_t = datetime.fromisoformat(H[-1][0]).replace(tzinfo=IST)
            age_h = (now - last_t).total_seconds() / 3600
            prev = None
            for r in H:
                if datetime.fromisoformat(r[0]).replace(tzinfo=IST) <= last_t - timedelta(hours=24):
                    prev = r
            today = H[-1][0][:10]
            gauges[st] = dict(last_time=H[-1][0].replace("T", " "), level=H[-1][1], age_h=round(age_h, 1),
                              change_24h=round(H[-1][1] - prev[1], 2) if prev else None,
                              readings_today=sum(1 for r in H if r[0][:10] == today),
                              ok=age_h <= stale_hours, notes=notes.get(st, []), error=errors.get(st))
        else:
            gauges[st] = dict(last_time=None, level=None, age_h=None, change_24h=None, readings_today=0, ok=False,
                              notes=notes.get(st, []), error=errors.get(st) or "no readings received")
    # A late upstream gauge (Tikarpara) would leave tomorrow's inputs empty and push the forecast
    # towards training averages. Carry its last daily values forward for up to 2 days instead
    # ("assume unchanged"), and say so. Forecast gauges are never filled.
    issue_day = max((max(daily[st]) for st in TARGETS if daily[st]), default=None)
    if issue_day and demo is None:
        up = "Tikarpara"
        if daily[up]:
            last = max(daily[up])
            gap = (datetime.fromisoformat(issue_day) - datetime.fromisoformat(last)).days
            if 0 < gap <= 2:
                for k in range(1, gap + 1):
                    daily[up][FG.add_d(last, k)] = list(daily[up][last])
                gauges[up]["notes"] = gauges[up]["notes"] + [f"no reading on {issue_day}: last day's value used (assumed unchanged)"]
    s1 = {}
    for st in TARGETS:
        if not gauges[st]["ok"] and demo is None:
            s1[st] = None
            continue
        t = max(daily[st]) if daily[st] else None
        r = FG.stage1(static.E, st, daily, rain, t, static.thresholds[st]) if t else None
        if r:
            r = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
            r["members"] = {k: round(v, 3) for k, v in r["members"].items()}
            r["outlook"] = {k: {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in o.items()} for k, o in r["outlook"].items()}
        s1[st] = r
    delayed = [st for st in ALL if not gauges[st]["ok"]]
    if demo is not None:
        delayed = []
    up_missing = "Tikarpara" in delayed
    statuses = [s1[st]["status"] for st in TARGETS if s1[st]]
    level = max(statuses, key=lambda s: RANK[s]) if statuses else None
    data_state = "ok" if not delayed else ("delayed" if any(st in delayed for st in TARGETS) or not statuses else "partial")
    t0 = s1["Naraj"]["issue"] if s1.get("Naraj") else None
    rs = rain_sums(rain.get("z3_floodplain", {}), t0) if t0 else {"1": 0, "3": 0, "7": 0}
    risk = interp_areas(static.area_ladder, s1["Naraj"]["fc"] if s1.get("Naraj") else None)
    rain_recent, rain_fc = {}, {}
    cut = t0 or now.astimezone(IST).date().isoformat()
    for z in ZONES:
        days = sorted(rain.get(z, {}))
        rain_recent[z] = [[d, None if not math.isfinite(rain[z][d]) else round(rain[z][d], 1)] for d in days if d <= cut][-14:]
        rain_fc[z] = [[d, None if not math.isfinite(rain[z][d]) else round(rain[z][d], 1)] for d in days if d > cut][:7]
    hourly7 = {}
    for st in ALL:
        H = hourly.get(st) or []
        if H:
            tend = datetime.fromisoformat(H[-1][0])
            hourly7[st] = [r for r in H if datetime.fromisoformat(r[0]) > tend - timedelta(days=7)]
        else:
            hourly7[st] = []
    daily30 = {st: {d: v for d, v in daily[st].items() if d >= FG.add_d(cut, -30)} for st in ALL}
    return dict(mode=mode, generated_utc=iso(now), generated_ist=ist_str(now), demo=None if demo is None else
                dict(scenario=demo["label"], day=demo["day"], step=demo["step"], n_steps=demo["n_steps"]),
                gauges=gauges, data_state=data_state, delayed=delayed, upstream_missing=up_missing,
                stage1=s1, level=level, level_name=LEVEL_NAME.get(level, "No data"), rain_sums=rs,
                rain=dict(recent=rain_recent, forecast=rain_fc), hourly=hourly7, daily=daily30,
                risk_from_ladder=risk, thresholds=static.thresholds, model_info=static.model_info)


# ============================================================================ alerts
def alert_text(run, kind):
    """Plain-language alert for a phone screen."""
    s1, demo = run["stage1"], run.get("demo")
    tag = f"[DEMO - replay of {demo['scenario']}, day {demo['day']}] " if demo else ""
    if kind == "delayed":
        late = ", ".join(run["delayed"])
        return (f"{tag}FloodGuard: DATA DELAYED. No recent CWC reading from {late}. The forecast cannot be trusted "
                f"until data returns. Check CWC/OSDMA bulletins. (Research prototype, not an official warning.)")
    if kind == "restored":
        return f"{tag}FloodGuard: live data restored. Current level: {run['level_name']}."
    lvl = run["level_name"].upper()
    lines = [f"{tag}FloodGuard {lvl} - Mahanadi delta (Cuttack)"]
    for st in TARGETS:
        r = s1.get(st)
        if not r:
            lines.append(f"{st}: no data")
            continue
        th = run["thresholds"][st]
        lines.append(f"{st}: {r['today_max']:.2f} m now -> {r['fc']:.2f} m expected {datetime.fromisoformat(r['valid']).strftime('%d %b')} "
                     f"(warning {th['warning']:.2f}, danger {th['danger']:.2f}; chance above warning {round(100 * r['p_warn'])}%)")
    rk = run["risk_from_ladder"]
    if rk["areas"]:
        top = ", ".join(f"{a['name']} ({a['km2']:.1f} km2)" for a in rk["areas"][:6])
        lines.append(f"Areas at risk of flooding: {top}. Total about {rk['km2_likely']:.0f} km2; {rk['built_km2']:.1f} km2 of homes and buildings within 500 m.")
    advice = {"RED": "Move people and livestock from low-lying land near the river to safe shelters now. Follow district instructions.",
              "ORANGE": "Prepare to move from low-lying areas near the river. Keep phones charged, move valuables up.",
              "YELLOW": "River rising. Stay alert and follow official CWC/OSDMA updates.",
              "GREEN": "River levels are back to normal."}[run["level"]]
    lines.append(advice)
    lines.append("Research prototype, not an official CWC/OSDMA warning.")
    return "\n".join(lines)


def send_desktop(title, text):
    if platform.system() == "Windows":
        t = title.replace("'", "''")
        b = text.replace("'", "''")[:250]
        ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
              "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Warning; "
              f"$n.BalloonTipTitle = '{t}'; $n.BalloonTipText = '{b}'; $n.Visible = $true; "
              "$n.ShowBalloonTip(15000); Start-Sleep -Seconds 16; $n.Dispose()")
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif platform.system() == "Darwin":
        subprocess.Popen(["osascript", "-e", f'display notification "{text[:200]}" with title "{title}"'])
    else:
        subprocess.Popen(["notify-send", title, text[:250]])


def send_telegram(c, text):
    for chat in c.get("chat_ids", []):
        send_telegram_to(c["bot_token"], chat, text)


TELEGRAM_API = os.environ.get("FG_TELEGRAM_API", "https://api.telegram.org")


def send_telegram_to(token, chat, text):
    return http_json(f"{TELEGRAM_API}/bot{token}/sendMessage", timeout=30, tries=2,
                     data=urlencode({"chat_id": chat, "text": text}).encode(), method="POST",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})


def save_alert_config(c):
    """Write config/alerts.json, keeping every other channel's settings."""
    CONF.mkdir(exist_ok=True)
    tmp = CONF / "alerts.json.tmp"
    tmp.write_text(json.dumps(c, indent=2), encoding="utf-8")
    os.replace(tmp, CONF / "alerts.json")


def telegram_connect(token):
    """One-click Telegram set-up from the dashboard: check the bot token, find everyone who has
    pressed Start in the bot, save them in config/alerts.json and send each a welcome message."""
    token = (token or "").strip()
    if not token or ":" not in token or " " in token:
        return dict(ok=False, message="That does not look like a bot token. It looks like 123456789:ABCdef... and comes from @BotFather.")
    try:
        me = http_json(f"{TELEGRAM_API}/bot{token}/getMe", timeout=20, tries=2)
    except Exception as e:
        return dict(ok=False, message=f"Telegram did not accept this token ({type(e).__name__}). Copy it again from @BotFather, or check the internet connection.")
    bot = me.get("result", {}).get("username", "")
    c = load_alert_config()
    tg = dict(c.get("telegram", {}))
    chats = {str(x) for x in tg.get("chat_ids", []) if str(x).lstrip("-").isdigit()}
    names = dict(tg.get("chat_names", {}))
    try:
        up = http_json(f"{TELEGRAM_API}/bot{token}/getUpdates", timeout=20, tries=2)
        for u in up.get("result", []):
            m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
            ch = m.get("chat") or {}
            if "id" in ch:
                chats.add(str(ch["id"]))
                names[str(ch["id"])] = ch.get("first_name") or ch.get("title") or ch.get("username") or "chat"
    except Exception:
        pass
    if not chats:
        tg.update(bot_token=token, bot_username=bot, enabled=False)
        c["telegram"] = tg
        save_alert_config(c)
        return dict(ok=False, need_start=True, bot=bot,
                    message=f"Token works. Now open t.me/{bot} in Telegram, press Start (or send any message), then press Connect again.")
    new = [ch for ch in chats if ch not in {str(x) for x in tg.get("chat_ids", [])}]
    tg.update(enabled=True, bot_token=token, bot_username=bot, chat_ids=sorted(chats), chat_names=names,
              send_demo_alerts=tg.get("send_demo_alerts", True))
    c["telegram"] = tg
    c.setdefault("desktop", {"enabled": True, "send_demo_alerts": True})
    save_alert_config(c)
    sent = []
    for ch in (new or sorted(chats)):
        try:
            send_telegram_to(token, ch, "FloodGuard is connected. You will get a message here when the Mahanadi alert level "
                                        "changes (Watch, Warning, Danger, All clear). Research prototype, not an official warning.")
            sent.append(names.get(ch, ch))
        except Exception:
            pass
    return dict(ok=True, bot=bot, chats=[names.get(ch, ch) for ch in sorted(chats)], welcomed=sent,
                message=f"Connected to @{bot}. Alerts go to: {', '.join(names.get(ch, ch) for ch in sorted(chats))}.")


def send_email(c, subject, text):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, c["username"], ", ".join(c["to"])
    msg.set_content(text)
    with smtplib.SMTP(c.get("smtp_host", "smtp.gmail.com"), int(c.get("smtp_port", 587)), timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(c["username"], c["app_password"])
        s.send_message(msg)


def send_sms(c, text):
    prov = c.get("provider", "fast2sms").lower()
    short = text[:600]
    if prov == "fast2sms":
        http_json("https://www.fast2sms.com/dev/bulkV2", timeout=30, tries=2, method="POST",
                  data=json.dumps({"route": "q", "message": short, "numbers": ",".join(c["numbers"]), "flash": 0}).encode(),
                  headers={"authorization": c["api_key"], "Content-Type": "application/json"})
    elif prov == "twilio":
        import base64
        auth = base64.b64encode(f"{c['account_sid']}:{c['auth_token']}".encode()).decode()
        for n in c["numbers"]:
            http_json(f"https://api.twilio.com/2010-04-01/Accounts/{c['account_sid']}/Messages.json", timeout=30, tries=2,
                      method="POST", data=urlencode({"From": c["from_number"], "To": n, "Body": short}).encode(),
                      headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
    else:
        raise ValueError(f"unknown SMS provider {prov}")


def dispatch(text, subject, is_demo=False):
    """Send to every enabled channel. Returns {channel: 'sent' | 'error ...' | 'off'}."""
    c = load_alert_config()
    res = {}
    if os.environ.get("FG_NO_SEND"):                 # tests: never message real phones
        return {ch: "off (test run)" for ch in ("desktop", "telegram", "email", "sms")}
    for ch, fn in (("desktop", lambda cc: send_desktop(subject, text)),
                   ("telegram", lambda cc: send_telegram(cc, text)),
                   ("email", lambda cc: send_email(cc, subject, text)),
                   ("sms", lambda cc: send_sms(cc, text))):
        cc = c.get(ch, {})
        if not cc.get("enabled"):
            res[ch] = "off"
            continue
        if is_demo and not cc.get("send_demo_alerts", True):
            res[ch] = "off for demo"
            continue
        try:
            fn(cc)
            res[ch] = "sent"
        except Exception as e:
            res[ch] = f"error: {type(e).__name__}: {str(e)[:120]}"
    return res


class AlertEngine:
    """Remembers the current level, decides when to alert, keeps the alert history."""

    def __init__(self, state_file, log_file):
        self.state_file, self.log_file = state_file, log_file
        self.state = read_json(state_file, {"level": None, "pending_lower": 0, "delayed_sent": False})
        self.log = read_json(log_file, [])

    def save(self):
        write_json(self.state_file, self.state)
        write_json(self.log_file, self.log[-300:])

    def record(self, run, kind, text, result):
        e = dict(time_utc=run["generated_utc"], time_ist=run["generated_ist"], kind=kind, level=run["level"],
                 level_name=run["level_name"], demo=bool(run.get("demo")), text=text, channels=result)
        self.log.append(e)
        return e

    def update(self, run):
        st = self.state
        sent = None
        is_demo = bool(run.get("demo"))
        if run["data_state"] == "delayed":
            if not st.get("delayed_sent"):
                text = alert_text(run, "delayed")
                sent = self.record(run, "data delayed", text, dispatch(text, "FloodGuard: data delayed", is_demo))
                st["delayed_sent"] = True
            self.save()
            return sent
        if st.get("delayed_sent"):
            st["delayed_sent"] = False
            text = alert_text(run, "restored")
            self.record(run, "data restored", text, dispatch(text, "FloodGuard: data restored", is_demo))
        new, old = run["level"], st.get("level")
        if new is None:
            self.save()
            return None
        if old is None:                         # first run: remember, alert only if not normal
            st["level"], st["pending_lower"] = new, 0
            if new != "GREEN":
                text = alert_text(run, "level")
                sent = self.record(run, f"{LEVEL_NAME[new]} (first reading)", text, dispatch(text, f"FloodGuard {LEVEL_NAME[new].upper()}", is_demo))
        elif RANK[new] > RANK[old]:
            st["level"], st["pending_lower"] = new, 0
            text = alert_text(run, "level")
            sent = self.record(run, f"raised to {LEVEL_NAME[new]}", text, dispatch(text, f"FloodGuard {LEVEL_NAME[new].upper()}", is_demo))
        elif RANK[new] < RANK[old]:
            st["pending_lower"] = st.get("pending_lower", 0) + 1
            if st["pending_lower"] >= 2:
                st["level"], st["pending_lower"] = new, 0
                kind = "all clear" if new == "GREEN" else f"lowered to {LEVEL_NAME[new]}"
                text = alert_text(run, "level")
                sent = self.record(run, kind, text, dispatch(text, f"FloodGuard {kind.upper()}", is_demo))
        else:
            st["pending_lower"] = 0
        self.save()
        return sent


# ============================================================================ service
class Service:
    def __init__(self):
        self.settings = load_settings()
        LIVE.mkdir(parents=True, exist_ok=True)
        self.static = Static()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.alerts = AlertEngine(LIVE / "alert_state.json", LIVE / "alerts_log.json")
        self.demo_alerts = AlertEngine(LIVE / "alert_state_demo.json", LIVE / "alerts_log.json")
        self.demo_alerts.log = self.alerts.log              # one shared history, so neither overwrites the other
        self.status = dict(started_utc=iso(now_utc()), last_run_utc=None, last_ok_utc=None, last_error=None,
                           next_run_utc=None, runs=0, mode="live")
        self.demo = None
        self.latest = read_json(LIVE / "latest.json")

    # ------------------------------------------------------------------ live loop
    def _load_last_good(self):
        """Last readings received per gauge (from our own archive), used when a fetch fails."""
        out = {st: [] for st in ALL}
        f = LIVE / "cwc_hourly_archive.csv"
        if f.exists():
            with open(f, newline="", encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 3 and row[0] in out:
                        try:
                            out[row[0]].append([row[1], float(row[2])])
                        except ValueError:
                            pass
        return {st: sorted(v)[-800:] for st, v in out.items()}

    def run_once(self):
        t_start = time.time()
        now = now_utc()
        hourly, notes, errors = fetch_cwc(self.static)
        if not hasattr(self, "last_good"):
            self.last_good = self._load_last_good()
        for st in ALL:
            if hourly[st]:
                self.last_good[st] = hourly[st]
            elif self.last_good.get(st):            # fetch failed: keep the last readings (they will show as late)
                hourly[st] = self.last_good[st]
                notes[st] = notes.get(st, []) + ["CWC did not answer; showing the last readings received"]
        rain, rerr = fetch_rain(self.static)
        run = build_run(self.static, hourly, rain, now, "live", self.settings["stale_hours"], notes, {**errors, **rerr})
        run["rain_errors"] = rerr
        run["next_update_utc"] = iso(now + timedelta(minutes=self.settings["fetch_every_minutes"]))
        sent = self.alerts.update(run)
        run["alert_sent"] = sent
        with self.lock:
            if self.demo is None:
                self.latest = run
            write_json(LIVE / "latest_live.json", run)
            if self.demo is None:
                write_json(LIVE / "latest.json", run)
        self._append_archive(hourly)
        self._log_run(run, time.time() - t_start, errors, rerr)
        return run

    def _append_archive(self, hourly):
        """Keep every hourly reading received, so the system builds its own archive."""
        f = LIVE / "cwc_hourly_archive.csv"
        seen = set()
        if f.exists():
            with open(f, newline="", encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2:
                        seen.add((row[0], row[1]))
        new = [(st, t, v) for st in ALL for t, v in hourly.get(st, []) if (st, t) not in seen]
        if new:
            with open(f, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if not seen:
                    w.writerow(["station", "time_ist", "level_m"])
                w.writerows(new)

    def _log_run(self, run, secs, errors, rerr):
        f = LIVE / "run_log.csv"
        first = not f.exists()
        n = run["stage1"].get("Naraj")
        with open(f, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if first:
                w.writerow(["run_utc", "run_ist", "data_state", "level", "naraj_last_reading", "naraj_level_m",
                            "naraj_forecast_m", "seconds", "alert_sent", "problems"])
            g = run["gauges"]["Naraj"]
            probs = "; ".join([f"{k}: {v}" for k, v in {**errors, **rerr}.items()])
            w.writerow([run["generated_utc"], run["generated_ist"], run["data_state"], run["level_name"], g["last_time"],
                        g["level"], n["fc"] if n else "", round(secs, 1), (run["alert_sent"] or {}).get("kind", ""), probs])
        self.status.update(last_run_utc=run["generated_utc"], runs=self.status["runs"] + 1,
                           last_ok_utc=run["generated_utc"] if run["data_state"] != "delayed" else self.status["last_ok_utc"],
                           last_error=probs or None, next_run_utc=run.get("next_update_utc"))
        log(f"run done in {secs:.0f} s: data {run['data_state']}, level {run['level_name']}"
            + (f", Naraj {g['level']} m at {g['last_time']}" if g["level"] is not None else "")
            + (f" | problems: {probs}" if probs else ""))

    def loop(self):
        while True:
            try:
                if self.demo is None:
                    self.run_once()
            except Exception:
                self.status["last_error"] = traceback.format_exc(limit=2)
                log("run failed:", self.status["last_error"])
            self.wake.wait(self.settings["fetch_every_minutes"] * 60)
            self.wake.clear()

    # ------------------------------------------------------------------ demo (replay of a real flood)
    def start_demo(self, key):
        sc = self.static.demo.get(key)
        if not sc:
            raise ValueError("unknown demo scenario")
        self.stop_demo()
        stop = threading.Event()
        self.demo = dict(key=key, stop=stop)
        self.demo_alerts.state = {"level": None, "pending_lower": 0, "delayed_sent": False}
        threading.Thread(target=self._demo_loop, args=(sc, stop), daemon=True).start()

    def stop_demo(self):
        if self.demo:
            self.demo["stop"].set()
        self.demo = None
        with self.lock:
            live = read_json(LIVE / "latest_live.json")
            if live:
                self.latest = live
                write_json(LIVE / "latest.json", live)

    def _demo_loop(self, sc, stop):
        days = sc["days"]
        rain = {z: {d: v for d, v in sc["rain"][z].items()} for z in ZONES}
        for i, day in enumerate(days):
            if stop.is_set():
                return
            # hourly series for the chart: one point per day at 12:00 (the replay has daily values)
            hourly = {st: [[d + "T12:00", v[1]] for d, v in sorted(sc["daily"][st].items()) if d <= day] for st in ALL}
            now = datetime.fromisoformat(day + "T18:00").replace(tzinfo=IST)
            demo = dict(label=sc["label"], day=day, step=i + 1, n_steps=len(days), daily=sc["daily"])
            run = build_run(self.static, hourly, rain, now, "demo", 9999, demo=demo)
            for st in ALL:                      # observed next-day value, to show the forecast was right
                run["gauges"][st]["observed_next_day"] = (sc["daily"][st].get(FG.add_d(day, 1)) or [None, None])[1]
            run["alert_sent"] = self.demo_alerts.update(run)
            run["next_update_utc"] = iso(now_utc() + timedelta(seconds=self.settings["demo_seconds_per_day"]))
            with self.lock:
                if self.demo is not None and not stop.is_set():
                    self.latest = run
                    write_json(LIVE / "latest.json", run)
            stop.wait(self.settings["demo_seconds_per_day"])
        if not stop.is_set():
            stop.wait(30)
            if not stop.is_set():
                self.stop_demo()


# ============================================================================ web server
SERVICE = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):      # keep the console readable
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._file(DASH / "FloodGuard_Live.html", "text/html; charset=utf-8")
        if u.path == "/engine_assets.json":
            return self._file(DASH / "engine_assets.json", "application/json", cache=True)
        if u.path == "/api/latest":
            with SERVICE.lock:
                return self._send(200, SERVICE.latest or {"mode": "starting"})
        if u.path == "/api/alerts":
            return self._send(200, read_json(LIVE / "alerts_log.json", [])[-100:])
        if u.path == "/api/status":
            c = load_alert_config()
            return self._send(200, {**SERVICE.status, "mode": "demo" if SERVICE.demo else "live",
                                    "settings": SERVICE.settings,
                                    "channels": {k: bool(c.get(k, {}).get("enabled")) for k in ("desktop", "telegram", "email", "sms")},
                                    "demos": {k: v["label"] for k, v in SERVICE.static.demo.items()},
                                    "telegram": {"bot": c.get("telegram", {}).get("bot_username"),
                                                 "has_token": bool(c.get("telegram", {}).get("bot_token", "").count(":")),
                                                 "chats": list((c.get("telegram", {}).get("chat_names") or {}).values())
                                                 or [str(x) for x in c.get("telegram", {}).get("chat_ids", []) if str(x).lstrip("-").isdigit()]}})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._local():
            return self._send(403, {"error": "only from this computer"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/refresh":
                SERVICE.wake.set()
                return self._send(200, {"ok": True})
            if u.path == "/api/test-alert":
                text = ("FloodGuard test message. If you can read this, alerts reach you. "
                        "Research prototype, not an official warning.")
                res = dispatch(text, "FloodGuard test")
                SERVICE.alerts.log.append(dict(time_utc=iso(now_utc()), time_ist=ist_str(now_utc()), kind="test", level=None,
                                               level_name="Test", demo=False, text=text, channels=res))
                SERVICE.alerts.save()
                return self._send(200, res)
            if u.path == "/api/demo/start":
                SERVICE.start_demo(q.get("scenario", [""])[0])
                return self._send(200, {"ok": True})
            if u.path == "/api/demo/stop":
                SERVICE.stop_demo()
                return self._send(200, {"ok": True})
            if u.path == "/api/telegram/connect":
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
                return self._send(200, telegram_connect(body.get("token") or load_alert_config().get("telegram", {}).get("bot_token")))
            if u.path == "/api/telegram/off":
                c = load_alert_config()
                c.setdefault("telegram", {})["enabled"] = False
                save_alert_config(c)
                return self._send(200, {"ok": True})
        except Exception as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def _file(self, path, ctype, cache=False):
        try:
            return self._send(200, Path(path).read_bytes(), ctype, cache)
        except FileNotFoundError:
            return self._send(404, {"error": f"{path.name} missing"})


def main():
    global SERVICE
    SERVICE = Service()
    s = SERVICE.settings
    host = "0.0.0.0" if s["allow_phones_on_same_wifi"] else "127.0.0.1"
    try:
        httpd = ThreadingHTTPServer((host, int(s["port"])), Handler)
    except OSError:
        url = f"http://localhost:{s['port']}"
        log(f"FloodGuard is already running (port {s['port']} is in use). Opening {url}")
        if s["open_browser_on_start"]:
            import webbrowser
            webbrowser.open(url)
        return
    threading.Thread(target=SERVICE.loop, daemon=True).start()
    url = f"http://localhost:{s['port']}"
    log("=" * 64)
    log(f"FloodGuard live service started. Dashboard: {url}")
    log(f"Reads CWC + rainfall every {s['fetch_every_minutes']} min. Keep this window open (minimise it).")
    log("=" * 64)
    if s["open_browser_on_start"] and "--no-browser" not in sys.argv:
        import webbrowser
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
