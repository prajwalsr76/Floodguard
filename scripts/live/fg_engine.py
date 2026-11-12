"""
FloodGuard Stage 1 engine in plain Python (standard library only).

A line-by-line mirror of scripts/realtime/engine.js (stage1, stageFeatures, interval,
statusOf) that reads the SAME model file (09_dashboard/engine_assets.json). The live
server uses it so that it needs no numpy / pandas / scikit-learn / xgboost on the
presentation laptop, and so that the server and the dashboard always use one model file.
tests/engine_parity.mjs + tests/server_engine_parity.py check it against the browser
engine and against the Python training pipeline.
"""
import array, base64, gzip, math, re, struct, sys
from datetime import date, timedelta

_LITTLE = sys.byteorder == "little"


def f32(x):
    """Round a float to float32 (Math.fround)."""
    if x != x or x in (math.inf, -math.inf):
        return x
    return struct.unpack("f", struct.pack("f", x))[0]


def _arr(s, code, gz):
    raw = base64.b64decode(s)
    if gz:
        raw = gzip.decompress(raw)
    a = array.array(code)
    a.frombytes(raw)
    if not _LITTLE:
        a.byteswap()
    return a


def _xgb(m):
    return dict(margin=m["margin"], n=m["n_trees"], left=_arr(m["left"], "i", True), right=_arr(m["right"], "i", True),
                feat=_arr(m["feat"], "h", True), cond=_arr(m["cond"], "f", True), dleft=_arr(m["dleft"], "B", True),
                start=_arr(m["start"], "i", True))


def _bank(s):
    return sorted(v / 1e4 for v in _arr(s, "i", False))


def load(assets):
    """assets: parsed engine_assets.json. Returns Stage 1 engine (Stage 2 runs in the browser)."""
    E = {}
    for st, s in assets["stage1"].items():
        E[st] = dict(features=s["features"], fill=s["fill"], ridge=s["ridge"], xgb=_xgb(s["xgb"]),
                     rf=dict(n=s["rf"]["n_trees"], feat=_arr(s["rf"]["feat"], "B", True), val=_arr(s["rf"]["val"], "f", True),
                             roff=_arr(s["rf"]["roff"], "H", True), start=_arr(s["rf"]["start"], "i", False)),
                     res=dict(normal=_bank(s["res_normal"]), high=_bank(s["res_high"])),
                     h={h: dict(xgb=_xgb(v["xgb"]), res=dict(normal=_bank(v["res_normal"]), high=_bank(v["res_high"])))
                        for h, v in s["h"].items()})
    return E


# ----------------------------------------------------------------------------- dates
def add_d(s, k):
    return (date.fromisoformat(s) + timedelta(days=k)).isoformat()


def day_of_year(s):
    return date.fromisoformat(s).timetuple().tm_yday


def round2(x):
    return math.floor(x * 100 + 0.5) / 100


def daily_from_hourly(H):
    """[[isoTime, level], ...] -> {date: [mean, max, min, n]} (same rule as engine.js)."""
    g = {}
    for t, v in H:
        if v is None or not math.isfinite(v):
            continue
        g.setdefault(t[:10], []).append(v)
    out = {}
    for d in sorted(g):
        a = g[d]
        s = 0.0
        for v in a:
            s += v
        out[d] = [round2(s / len(a)), max(a), min(a), len(a)]
    return out


# ----------------------------------------------------------------------------- features
def slug(s):
    return s.lower().replace(" ", "_")


_RX_LEVEL = re.compile(r"^(.*)_(mean_lag\d+|max_lag\d+|min_lag0|chg1|chg2|maxchg1|rmax3|rmax7|intraday_range)$")
_RX_ML = re.compile(r"^(mean|max)_lag(\d+)$")
_RX_RAIN = re.compile(r"^(z\d_[a-z_]+?)_(d0|lag\d|sum\d+)$")
NAN = float("nan")


def stage_features(features, daily, rain, t):
    byslug = {slug(st): v for st, v in daily.items()}

    def get(s, d, k):
        r = byslug.get(s, {}).get(d)
        v = r[k] if r is not None and len(r) > k else None
        return NAN if v is None or not math.isfinite(v) else float(v)

    def rget(z, d):
        v = rain.get(z, {}).get(d)
        return NAN if v is None or not math.isfinite(v) else float(v)

    x = []
    for f in features:
        grp, rest = f.split("__", 1)
        v = NAN
        if grp in ("own", "up"):
            m = _RX_LEVEL.match(rest)
            s, q = m.group(1), m.group(2)
            mm = _RX_ML.match(q)
            if mm:
                v = get(s, add_d(t, -int(mm.group(2))), 0 if mm.group(1) == "mean" else 1)
            elif q == "min_lag0":
                v = get(s, t, 2)
            elif q == "chg1":
                v = get(s, t, 0) - get(s, add_d(t, -1), 0)
            elif q == "chg2":
                v = get(s, t, 0) - get(s, add_d(t, -2), 0)
            elif q == "maxchg1":
                v = get(s, t, 1) - get(s, add_d(t, -1), 1)
            elif q in ("rmax3", "rmax7"):
                n = 3 if q == "rmax3" else 7
                mx = -math.inf
                ok = True
                for k in range(n):
                    a = get(s, add_d(t, -k), 1)
                    if a != a:
                        ok = False
                        break
                    mx = max(mx, a)
                v = mx if ok else NAN
            elif q == "intraday_range":
                v = get(s, t, 1) - get(s, t, 2)
        elif grp == "rain":
            m = _RX_RAIN.match(rest)
            z, q = m.group(1), m.group(2)
            if q == "d0":
                v = rget(z, t)
            elif q.startswith("lag"):
                v = rget(z, add_d(t, -int(q[3:])))
            else:
                n = int(q[3:])
                s_, ok = 0.0, True
                for k in range(n - 1, -1, -1):
                    a = rget(z, add_d(t, -k))
                    if a != a:
                        ok = False
                        break
                    s_ += a
                v = s_ if ok else NAN
        elif grp == "season":
            doy, mo = day_of_year(t), int(t[5:7])
            if rest == "doy_sin":
                v = math.sin(2 * math.pi * doy / 365.25)
            elif rest == "doy_cos":
                v = math.cos(2 * math.pi * doy / 365.25)
            elif rest == "monsoon":
                v = 1.0 if 6 <= mo <= 10 else 0.0
        x.append(v)
    return x


# ----------------------------------------------------------------------------- models
def xgb_predict(M, x):
    s = M["margin"]
    left, right, feat, cond, dleft, start = M["left"], M["right"], M["feat"], M["cond"], M["dleft"], M["start"]
    x32 = [f32(v) for v in x]
    for k in range(M["n"]):
        base = n = start[k]
        while left[n] != -1:
            v = x32[feat[n]]
            go_left = (dleft[n] == 1) if v != v else (v < cond[n])
            n = base + (left[n] if go_left else right[n])
        s += cond[n]
    return s


def rf_predict(R, x):
    feat, val, roff, start = R["feat"], R["val"], R["roff"], R["start"]
    x32 = [f32(v) for v in x]
    acc = 0.0
    for k in range(R["n"]):
        n = start[k]
        while feat[n] != 255:
            n = n + 1 if x32[feat[n]] <= val[n] else n + roff[n]
        acc += val[n]
    return acc / R["n"]


def ridge_predict(G, x):
    s = 0.0
    for w, v in zip(G["w"], x):
        s += w * v
    return G["b"] + s


def quantile(a, q):
    n = len(a)
    pos = (n - 1) * q
    i = int(math.floor(pos))
    g = pos - i
    if i + 1 >= n:
        return a[n - 1]
    lo, hi = a[i], a[i + 1]
    return hi - (hi - lo) * (1 - g) if g >= 0.5 else lo + (hi - lo) * g


def interval(pred, base, warn, bank):
    reg = "high" if base >= warn - 2.0 else "normal"
    r = bank[reg]
    if len(r) < 30:
        r = sorted(bank["normal"] + bank["high"])
    ex = sum(1 for e in r if pred + e >= warn)
    return dict(lo=pred + quantile(r, 0.1), hi=pred + quantile(r, 0.9), p=ex / len(r), regime=reg)


def status_of(fc, hi, th):
    if fc >= th["danger"]:
        return "RED"
    if fc >= th["warning"]:
        return "ORANGE"
    if hi >= th["warning"] or fc >= th["warning"] - 0.5:
        return "YELLOW"
    return "GREEN"


def stage1(E, st, daily, rain, t, th):
    S = E[st]
    x = stage_features(S["features"], daily, rain, t)
    base = x[S["features"].index(f"own__{slug(st)}_max_lag0")]
    if base != base:
        return None
    xf = [S["fill"][j] if v != v else v for j, v in enumerate(x)]
    mX = base + xgb_predict(S["xgb"], x)
    mR = base + rf_predict(S["rf"], xf)
    mG = base + ridge_predict(S["ridge"], xf)
    fc = (mX + mR + mG) / 3
    iv = interval(fc, base, th["warning"], S["res"])
    outlook = {}
    for h in ("2", "3"):
        pr = base + xgb_predict(S["h"][h]["xgb"], x)
        o = interval(pr, base, th["warning"], S["h"][h]["res"])
        outlook[f"+{24 * int(h)}h"] = dict(valid=add_d(t, int(h)), fc=pr, lo=o["lo"], hi=o["hi"], p_warn=o["p"])
    return dict(issue=t, valid=add_d(t, 1), today_max=base, fc=fc, lo=iv["lo"], hi=iv["hi"], p_warn=iv["p"],
                regime=iv["regime"], members=dict(XGBoost=mX, RandomForest=mR, Ridge=mG), outlook=outlook,
                status=status_of(fc, iv["hi"], th), n_missing=sum(1 for v in x if v != v))
