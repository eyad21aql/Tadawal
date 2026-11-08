"""Shared helpers for the Tadawal skill pack (standard library only).

Data sources, in order of preference when source="auto":
  1. MetaTrader 5 terminal (Windows, `pip install MetaTrader5`, terminal running)
  2. Yahoo Finance chart JSON (free, delayed, unofficial)
A CSV file (MT5 "Export bars" format or plain date,open,high,low,close,volume)
can always be passed with --csv.

Everything a skill writes goes into ./trader/ (override with TADAWAL_DIR).
"""
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - very old Python / missing tzdata
    NY = None

# --------------------------------------------------------------------------
# files and config
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "mode": "paper",              # paper = simulate fills locally; mt5 = send to MetaTrader 5
    "allow_live": False,          # MT5 REAL accounts are refused unless this is true
    "data_source": "auto",        # auto | mt5 | yahoo
    "symbol_suffix": "",          # e.g. ".US" or ".NAS" if your broker renames US stocks
    "symbol_map": {},             # {"AAPL": "#AAPL"} explicit overrides
    "account_balance": 10000.0,   # used when MT5 is not connected
    "currency": "USD",
    "risk_per_trade_pct": 1.0,    # max % of balance lost if the stop is hit
    "max_open_positions": 5,
    "max_daily_loss_pct": 3.0,    # stop trading for the day after this realized loss
    "max_position_pct": 25.0,     # max notional of one position as % of balance
    "min_rr": 1.5,                # minimum reward:risk when a target is set
    "blocked_symbols": [],
    "allow_short": False,
    "sec_user_agent": "",         # "Your Name you@example.com" (SEC requires it)
    "mt5": {"path": None, "login": None, "server": None},  # password: env MT5_PASSWORD
    "magic": 2126,
}


def trader_dir():
    d = os.environ.get("TADAWAL_DIR") or os.path.join(os.getcwd(), "trader")
    os.makedirs(d, exist_ok=True)
    return d


def tpath(*parts):
    p = os.path.join(trader_dir(), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        die("%s is not valid JSON (%s). Fix or delete it." % (path, e))


def save_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    os.replace(tmp, path)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = load_json(tpath("config.json"), {}) or {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def die(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def emit(obj, as_json):
    """Print JSON when asked, otherwise the 'text' field (or pretty JSON)."""
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    elif isinstance(obj, dict) and "text" in obj:
        print(obj["text"])
    else:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def order_hash(order):
    """Stable hash of the fields that matter, so any edit invalidates approvals."""
    keys = ("symbol", "side", "type", "volume", "entry", "sl", "tp")
    norm = {k: order.get(k) for k in keys}
    for k in ("volume", "entry", "sl", "tp"):
        if norm[k] is not None:
            norm[k] = round(float(norm[k]), 6)
    norm["symbol"] = str(norm["symbol"] or "").upper()
    s = json.dumps(norm, sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url, ua=None, timeout=30, retries=3, accept=None):
    headers = {"User-Agent": ua or "Mozilla/5.0 (Tadawal skill pack)",
               "Accept-Encoding": "gzip"}
    if accept:
        headers["Accept"] = accept
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise last


def sec_user_agent(cli=None):
    ua = (cli or os.environ.get("SEC_USER_AGENT") or load_config().get("sec_user_agent") or "").strip()
    if not ua or "@" not in ua:
        die('SEC requires a contact: set SEC_USER_AGENT="Your Name you@example.com" '
            'or "sec_user_agent" in trader/config.json')
    return ua


# --------------------------------------------------------------------------
# NYSE calendar
# --------------------------------------------------------------------------

NYSE_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}  # 1:00 p.m. close
CALENDAR_YEARS = (2026, 2027)


def ny_now():
    return dt.datetime.now(NY) if NY else dt.datetime.utcnow() - dt.timedelta(hours=5)


def session(day):
    """(open, close) datetimes in New York for a date, or None if closed."""
    if day.weekday() >= 5 or day.isoformat() in NYSE_HOLIDAYS:
        return None
    o = dt.datetime.combine(day, dt.time(9, 30), NY)
    c = dt.datetime.combine(day, dt.time(13 if day.isoformat() in NYSE_EARLY_CLOSE else 16, 0), NY)
    return o, c


def market_clock(now=None):
    now = now or ny_now()
    if now.tzinfo is None and NY:
        now = now.replace(tzinfo=NY)
    now = now.astimezone(NY) if NY else now
    today = now.date()
    s = session(today)
    out = {"now_ny": now.strftime("%Y-%m-%d %H:%M %a"), "open": False}
    if today.year not in CALENDAR_YEARS:
        out["warning"] = "holiday calendar only covers %s; weekends still respected" % (CALENDAR_YEARS,)
    if s and s[0] <= now < s[1]:
        out["open"] = True
        out["closes_at"] = s[1].strftime("%Y-%m-%d %H:%M")
        out["minutes_since_open"] = int((now - s[0]).total_seconds() // 60)
        out["minutes_to_close"] = int((s[1] - now).total_seconds() // 60)
        out["early_close"] = today.isoformat() in NYSE_EARLY_CLOSE
        out["status"] = "NYSE open"
        return out
    if s and now < s[0]:
        nxt = s[0]
        why = "pre-market"
    else:
        why = "after hours" if s else ("weekend" if today.weekday() >= 5 else "holiday")
        d = today + dt.timedelta(days=1)
        while session(d) is None:
            d += dt.timedelta(days=1)
        nxt = session(d)[0]
    out["status"] = "NYSE closed (%s)" % why
    out["next_open"] = nxt.strftime("%Y-%m-%d %H:%M %a")
    out["minutes_to_open"] = int((nxt - now).total_seconds() // 60)
    return out


# --------------------------------------------------------------------------
# bars
# --------------------------------------------------------------------------

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600,
              "H4": 14400, "D1": 86400, "W1": 604800}


class Bars:
    """Parallel lists: t (epoch seconds, UTC), o, h, l, c, v."""

    def __init__(self, symbol, tf, t, o, h, l, c, v, source=""):
        self.symbol, self.tf, self.source = symbol, tf, source
        self.t, self.o, self.h, self.l, self.c, self.v = t, o, h, l, c, v

    def __len__(self):
        return len(self.c)

    def slice(self, start=None, end=None):
        return Bars(self.symbol, self.tf, self.t[start:end], self.o[start:end], self.h[start:end],
                    self.l[start:end], self.c[start:end], self.v[start:end], self.source)

    def date(self, i, fmt=None):
        d = dt.datetime.fromtimestamp(self.t[i], dt.timezone.utc)
        if NY:
            d = d.astimezone(NY)
        if fmt:
            return d.strftime(fmt)
        return d.strftime("%Y-%m-%d") if self.tf in ("D1", "W1") else d.strftime("%Y-%m-%d %H:%M")


def broker_symbol(symbol, cfg):
    s = symbol.upper()
    return cfg.get("symbol_map", {}).get(s) or (s + (cfg.get("symbol_suffix") or ""))


def _mt5_module():
    try:
        import MetaTrader5 as mt5  # noqa: N813
        return mt5
    except Exception:
        return None


_MT5_READY = None


def mt5_connect(cfg=None, required=False):
    """Return the initialized MetaTrader5 module, or None."""
    global _MT5_READY
    if _MT5_READY is not None:
        if not _MT5_READY and required:
            die("MetaTrader 5 is not connected. On Windows: pip install MetaTrader5, keep the MT5 terminal "
                "open and logged in, or set \"mode\": \"paper\" in trader/config.json.")
        return _MT5_READY or None
    mt5 = _mt5_module()
    if mt5 is None:
        _MT5_READY = False
        if required:
            die("MetaTrader5 Python package not available. On Windows: pip install MetaTrader5, "
                "and keep the MT5 terminal open and logged in.")
        return None
    cfg = cfg or load_config()
    m = cfg.get("mt5") or {}
    kw = {}
    if m.get("path"):
        kw["path"] = m["path"]
    if m.get("login"):
        kw["login"] = int(m["login"])
        kw["server"] = m.get("server")
        if os.environ.get("MT5_PASSWORD"):
            kw["password"] = os.environ["MT5_PASSWORD"]
    if not mt5.initialize(**kw):
        _MT5_READY = False
        if required:
            die("MT5 initialize() failed: %s. Is the terminal open and logged in?" % (mt5.last_error(),))
        return None
    _MT5_READY = mt5
    return mt5


def _bars_mt5(symbol, tf, count, cfg):
    mt5 = mt5_connect(cfg)
    if not mt5:
        return None
    sym = broker_symbol(symbol, cfg)
    mt5.symbol_select(sym, True)
    rates = mt5.copy_rates_from_pos(sym, getattr(mt5, "TIMEFRAME_" + tf), 0, count)
    if rates is None or len(rates) == 0:
        return None
    t, o, h, l, c, v = [], [], [], [], [], []
    for r in rates:
        t.append(int(r["time"]))
        o.append(float(r["open"]))
        h.append(float(r["high"]))
        l.append(float(r["low"]))
        c.append(float(r["close"]))
        rv = float(r["real_volume"]) if "real_volume" in r.dtype.names else 0.0
        v.append(rv or float(r["tick_volume"]))
    return Bars(symbol.upper(), tf, t, o, h, l, c, v, "mt5:" + sym)


YAHOO_TF = {"M1": ("1m", "7d"), "M5": ("5m", "60d"), "M15": ("15m", "60d"), "M30": ("30m", "60d"),
            "H1": ("60m", "730d"), "H4": ("60m", "730d"), "D1": ("1d", "10y"), "W1": ("1wk", "max")}


def parse_yahoo_chart(raw, symbol, tf):
    js = json.loads(raw)
    res = (js.get("chart") or {}).get("result")
    if not res:
        err = (js.get("chart") or {}).get("error")
        raise ValueError("Yahoo returned no data for %s: %s" % (symbol, err))
    r = res[0]
    q = r["indicators"]["quote"][0]
    t, o, h, l, c, v = [], [], [], [], [], []
    for i, ts in enumerate(r.get("timestamp") or []):
        vals = [q["open"][i], q["high"][i], q["low"][i], q["close"][i]]
        if any(x is None for x in vals):
            continue
        t.append(int(ts))
        o.append(float(vals[0]))
        h.append(float(vals[1]))
        l.append(float(vals[2]))
        c.append(float(vals[3]))
        v.append(float(q["volume"][i] or 0))
    return Bars(symbol.upper(), tf, t, o, h, l, c, v, "yahoo")


def resample(b, tf):
    """Group bars into fixed buckets of TF_SECONDS[tf] (aligned to NY 9:30 for H4)."""
    step = TF_SECONDS[tf]
    out = Bars(b.symbol, tf, [], [], [], [], [], [], b.source)
    key_prev = None
    for i in range(len(b)):
        if tf == "H4":
            d = dt.datetime.fromtimestamp(b.t[i], NY) if NY else dt.datetime.utcfromtimestamp(b.t[i])
            mins = d.hour * 60 + d.minute - 570
            key = (d.date(), mins // 240)
        else:
            key = b.t[i] // step
        if key != key_prev:
            out.t.append(b.t[i]); out.o.append(b.o[i]); out.h.append(b.h[i])
            out.l.append(b.l[i]); out.c.append(b.c[i]); out.v.append(b.v[i])
            key_prev = key
        else:
            out.h[-1] = max(out.h[-1], b.h[i]); out.l[-1] = min(out.l[-1], b.l[i])
            out.c[-1] = b.c[i]; out.v[-1] += b.v[i]
    return out


def _bars_yahoo(symbol, tf, count):
    interval, rng = YAHOO_TF[tf]
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"
           "&includePrePost=false&events=div%%2Csplit" % (symbol.upper(), rng, interval))
    b = parse_yahoo_chart(http_get(url), symbol, tf)
    if tf == "H4":
        b = resample(b, "H4")
    return b.slice(-count) if count else b


def load_csv(path, symbol="CSV", tf="D1"):
    """Read MT5-exported bars (<DATE>\t<TIME>\t<OPEN>...) or a plain OHLCV CSV."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    delim = "\t" if text.count("\t") > text.count(",") else ("," if "," in text.splitlines()[0] else ";")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    head = [h.strip().strip("<>").lower() for h in rows[0]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None

    ci = {k: col(*names) for k, names in {
        "date": ("date", "datetime", "time", "timestamp"), "time": ("time",),
        "o": ("open",), "h": ("high",), "l": ("low",), "c": ("close", "adj close"),
        "v": ("volume", "vol", "tickvol", "tick_volume", "real_volume")}.items()}
    if None in (ci["date"], ci["o"], ci["h"], ci["l"], ci["c"]):
        die("CSV needs date/open/high/low/close columns; got %s" % head)
    if ci["time"] == ci["date"]:
        ci["time"] = None
    t, o, h, l, c, v = [], [], [], [], [], []
    for r in rows[1:]:
        if not r or not r[ci["date"]].strip():
            continue
        s = r[ci["date"]].strip()
        if ci["time"] is not None:
            s += " " + r[ci["time"]].strip()
        t.append(_parse_ts(s))
        o.append(float(r[ci["o"]])); h.append(float(r[ci["h"]]))
        l.append(float(r[ci["l"]])); c.append(float(r[ci["c"]]))
        v.append(float(r[ci["v"]]) if ci["v"] is not None and r[ci["v"]] else 0.0)
    order = sorted(range(len(t)), key=lambda i: t[i])
    pick = lambda a: [a[i] for i in order]  # noqa: E731
    return Bars(symbol.upper(), tf, pick(t), pick(o), pick(h), pick(l), pick(c), pick(v), "csv:" + path)


def _parse_ts(s):
    s = s.strip()
    if s.isdigit():
        n = int(s)
        return n // 1000 if n > 10 ** 11 else n
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %H:%M"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return int(d.replace(tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            continue
    die("cannot parse date %r in CSV" % s)


def get_bars(symbol, tf="D1", count=500, source=None, csv_path=None, cfg=None):
    tf = tf.upper()
    if tf not in TIMEFRAMES:
        die("timeframe must be one of %s" % (TIMEFRAMES,))
    if csv_path:
        b = load_csv(csv_path, symbol, tf)
        return b.slice(-count) if count else b
    cfg = cfg or load_config()
    source = (source or cfg.get("data_source") or "auto").lower()
    if source in ("auto", "mt5"):
        b = _bars_mt5(symbol, tf, count, cfg)
        if b is not None and len(b):
            return b
        if source == "mt5":
            die("MT5 returned no bars for %s %s (symbol name at your broker? see symbol_suffix)"
                % (broker_symbol(symbol, cfg), tf))
    try:
        return _bars_yahoo(symbol, tf, count)
    except Exception as e:
        die("could not load %s %s from Yahoo (%s). Pass --csv with exported bars instead." % (symbol, tf, e))


def get_price(symbol, cfg=None, source=None):
    """Last price and previous close: MT5 tick if connected, otherwise Yahoo (delayed)."""
    cfg = cfg or load_config()
    source = (source or cfg.get("data_source") or "auto").lower()
    if source in ("auto", "mt5"):
        mt5 = mt5_connect(cfg)
        if mt5:
            sym = broker_symbol(symbol, cfg)
            mt5.symbol_select(sym, True)
            tk = mt5.symbol_info_tick(sym)
            if tk:
                px = tk.last or (tk.bid + tk.ask) / 2
                d1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, 1)
                prev = float(d1[0]["close"]) if d1 is not None and len(d1) else None
                return {"symbol": symbol.upper(), "price": float(px), "bid": float(tk.bid), "ask": float(tk.ask),
                        "prev_close": prev, "time": int(tk.time), "source": "mt5"}
            if source == "mt5":
                die("no MT5 tick for %s" % sym)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m" % symbol.upper()
    try:
        js = json.loads(http_get(url))
        meta = js["chart"]["result"][0]["meta"]
    except Exception as e:
        die("no price for %s from Yahoo (%s)" % (symbol, e))
    return {"symbol": symbol.upper(), "price": float(meta.get("regularMarketPrice")),
            "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "time": meta.get("regularMarketTime"), "source": "yahoo (delayed)"}


def add_data_args(ap, tf_default="D1", count_default=400):
    ap.add_argument("--tf", default=tf_default, help="M1 M5 M15 M30 H1 H4 D1 W1")
    ap.add_argument("--count", type=int, default=count_default, help="bars to load")
    ap.add_argument("--source", choices=["auto", "mt5", "yahoo"], default=None)
    ap.add_argument("--csv", help="read bars from a CSV (e.g. exported from MT5)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")


# --------------------------------------------------------------------------
# indicators (lists aligned with bars; None during warm-up)
# --------------------------------------------------------------------------

def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    out = [None] * len(x)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(x[:n]) / n
    out[n - 1] = e
    for i in range(n, len(x)):
        e = x[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(x, n=14):
    out = [None] * len(x)
    if len(x) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = x[i] - x[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(x)):
        d = x[i] - x[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def true_range(b):
    return [b.h[0] - b.l[0]] + [max(b.h[i] - b.l[i], abs(b.h[i] - b.c[i - 1]), abs(b.l[i] - b.c[i - 1]))
                                for i in range(1, len(b))]


def atr(b, n=14):
    tr = true_range(b)
    out = [None] * len(tr)
    if len(tr) < n:
        return out
    a = sum(tr[:n]) / n
    out[n - 1] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def macd(x, fast=12, slow=26, signal=9):
    ef, es = ema(x, fast), ema(x, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_part = ema(line[start:], signal) if start < len(line) else []
    sig = [None] * start + sig_part
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(x, n=20, k=2.0):
    mid = sma(x, n)
    up, lo = [None] * len(x), [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        m = mid[i]
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
        up[i], lo[i] = m + k * sd, m - k * sd
    return mid, up, lo


def highest(x, n):
    """Highest of the previous n values, excluding the current bar."""
    return [None if i < n else max(x[i - n:i]) for i in range(len(x))]


def lowest(x, n):
    return [None if i < n else min(x[i - n:i]) for i in range(len(x))]


def slope(y):
    """Least-squares slope per step."""
    n = len(y)
    if n < 2:
        return 0.0
    mx, my = (n - 1) / 2, sum(y) / n
    num = sum((i - mx) * (v - my) for i, v in enumerate(y))
    den = sum((i - mx) ** 2 for i in range(n))
    return num / den if den else 0.0


def slope_points(pts):
    """Least-squares slope through [(x, y), ...]."""
    n = len(pts)
    if n < 2:
        return 0.0
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else 0.0


def pivots(b, left=3, right=3):
    """Fractal swing points: [(index, 'H'|'L', price)], confirmed only (needs `right` bars after)."""
    out = []
    for i in range(left, len(b) - right):
        hw = b.h[i - left:i + right + 1]
        lw = b.l[i - left:i + right + 1]
        if b.h[i] == max(hw) and hw.count(b.h[i]) == 1:
            out.append((i, "H", b.h[i]))
        if b.l[i] == min(lw) and lw.count(b.l[i]) == 1:
            out.append((i, "L", b.l[i]))
    return out


def zigzag(b, threshold, include_last=False):
    """Alternating confirmed swing points; each leg moves at least `threshold` (price units).
    With include_last=True the still-forming extreme is appended (marked tentative)."""
    n = len(b)
    if n < 2:
        return []
    pts, direction, hi_i, lo_i = [], 0, 0, 0
    for i in range(1, n):
        if direction == 0:
            if b.h[i] > b.h[hi_i]:
                hi_i = i
            if b.l[i] < b.l[lo_i]:
                lo_i = i
            if b.h[hi_i] - b.l[lo_i] >= threshold:
                if hi_i > lo_i:
                    pts.append((lo_i, "L", b.l[lo_i]))
                    direction = 1
                else:
                    pts.append((hi_i, "H", b.h[hi_i]))
                    direction = -1
        elif direction == 1:
            if b.h[i] > b.h[hi_i]:
                hi_i = i
            elif b.h[hi_i] - b.l[i] >= threshold:
                pts.append((hi_i, "H", b.h[hi_i]))
                direction, lo_i = -1, i
        else:
            if b.l[i] < b.l[lo_i]:
                lo_i = i
            elif b.h[i] - b.l[lo_i] >= threshold:
                pts.append((lo_i, "L", b.l[lo_i]))
                direction, hi_i = 1, i
    if include_last and direction:
        pts.append((hi_i, "H*", b.h[hi_i]) if direction == 1 else (lo_i, "L*", b.l[lo_i]))
    return pts


def levels(b, atr_value, lookback=250, left=3, right=3, max_levels=8):
    """Support/resistance: cluster swing points within 0.5*ATR; strength = touches."""
    start = max(0, len(b) - lookback)
    pts = [p for p in pivots(b.slice(start), left, right)]
    tol = max(atr_value * 0.5, 1e-9)
    clusters = []
    for i, kind, price in sorted(pts, key=lambda p: p[2]):
        if clusters and price - clusters[-1]["prices"][-1] <= tol:
            clusters[-1]["prices"].append(price)
            clusters[-1]["last"] = max(clusters[-1]["last"], i + start)
        else:
            clusters.append({"prices": [price], "last": i + start})
    out = []
    for c in clusters:
        out.append({"price": round(sum(c["prices"]) / len(c["prices"]), 4),
                    "touches": len(c["prices"]), "last_touch": b.date(c["last"])})
    out.sort(key=lambda d: (-d["touches"], d["price"]))
    return sorted(out[:max_levels], key=lambda d: d["price"])


def r(x, n=2):
    return None if x is None else round(float(x), n)


# --------------------------------------------------------------------------
# account, positions, contract specs (MT5 or local paper account)
# --------------------------------------------------------------------------

def use_mt5(cfg):
    return (cfg.get("mode") or "paper").lower() == "mt5"


def paper_state(cfg=None):
    cfg = cfg or load_config()
    st = load_json(tpath("paper.json"), None)
    if st is None:
        st = {"balance": float(cfg.get("account_balance", 10000)), "positions": [], "next_id": 1,
              "created": ny_now().isoformat()}
    return st


def save_paper(st):
    save_json(tpath("paper.json"), st)


def symbol_spec(symbol, cfg=None):
    """Contract specification. Stocks on the paper account: 1 lot = 1 share."""
    cfg = cfg or load_config()
    if use_mt5(cfg) or (cfg.get("data_source") == "mt5"):
        mt5 = mt5_connect(cfg)
        if mt5:
            sym = broker_symbol(symbol, cfg)
            mt5.symbol_select(sym, True)
            si = mt5.symbol_info(sym)
            if si is None:
                die("symbol %s not found at your broker (set symbol_suffix / symbol_map in trader/config.json)" % sym)
            return {"symbol": sym, "tick_size": si.trade_tick_size or si.point, "tick_value": si.trade_tick_value,
                    "contract_size": si.trade_contract_size or 1, "volume_min": si.volume_min,
                    "volume_max": si.volume_max, "volume_step": si.volume_step, "digits": si.digits,
                    "currency_profit": getattr(si, "currency_profit", ""), "source": "mt5"}
    step = float(cfg.get("paper_volume_step", 1))
    return {"symbol": symbol.upper(), "tick_size": 0.01, "tick_value": 0.01, "contract_size": 1,
            "volume_min": step, "volume_max": 1e9, "volume_step": step, "digits": 2, "source": "paper (shares)"}


def money_per_point(spec):
    """Profit/loss in account currency for a 1.0 price move on 1 lot."""
    return spec["tick_value"] / spec["tick_size"] if spec["tick_size"] else spec["contract_size"]


def round_volume(vol, spec, down=True):
    step = spec["volume_step"] or 1
    k = math.floor(vol / step + 1e-9) if down else round(vol / step)
    v = round(k * step, 8)
    return min(v, spec["volume_max"])


def account_snapshot(cfg=None):
    cfg = cfg or load_config()
    if use_mt5(cfg):
        mt5 = mt5_connect(cfg, required=True)
        ai = mt5.account_info()
        if ai is None:
            die("MT5 account_info() is empty: log in to your account in the terminal")
        modes = {0: "demo", 1: "contest", 2: "real"}
        pos = []
        for p in mt5.positions_get() or []:
            pos.append({"id": int(p.ticket), "symbol": p.symbol, "side": "buy" if p.type == 0 else "sell",
                        "volume": float(p.volume), "entry": float(p.price_open), "sl": float(p.sl) or None,
                        "tp": float(p.tp) or None, "price": float(p.price_current), "profit": float(p.profit)})
        return {"mode": "mt5", "account": int(ai.login), "server": ai.server,
                "trade_mode": modes.get(ai.trade_mode, str(ai.trade_mode)),
                "balance": float(ai.balance), "equity": float(ai.equity), "free_margin": float(ai.margin_free),
                "currency": ai.currency, "positions": pos}
    st = paper_state(cfg)
    pos = [dict(p) for p in st["positions"]]
    upl = 0.0
    for p in pos:
        px = p.get("price") or p["entry"]
        sign = 1 if p["side"] == "buy" else -1
        p["profit"] = round(sign * (px - p["entry"]) * p["volume"] * p.get("mpp", 1), 2)
        upl += p["profit"]
    return {"mode": "paper", "trade_mode": "paper", "balance": round(st["balance"], 2),
            "equity": round(st["balance"] + upl, 2), "currency": cfg.get("currency", "USD"), "positions": pos}


def journal():
    return load_json(tpath("journal.json"), {"trades": []})


def save_journal(j):
    save_json(tpath("journal.json"), j)


def realized_today(cfg=None):
    """Realized P&L for today's New York date."""
    cfg = cfg or load_config()
    today = ny_now().date()
    if use_mt5(cfg):
        mt5 = mt5_connect(cfg, required=True)
        start = dt.datetime.combine(today, dt.time(0, 0), NY).astimezone(dt.timezone.utc).replace(tzinfo=None)
        deals = mt5.history_deals_get(start, dt.datetime.utcnow() + dt.timedelta(days=1)) or []
        return round(sum(d.profit + d.commission + d.swap for d in deals if d.entry in (1, 2, 3)), 2)
    tot = 0.0
    for t in journal()["trades"]:
        if t.get("status") == "closed" and str(t.get("closed_at", ""))[:10] == today.isoformat():
            tot += float(t.get("pnl") or 0)
    return round(tot, 2)


def load_orders():
    return load_json(tpath("orders.json"), {"orders": []})


def save_orders(o):
    save_json(tpath("orders.json"), o)


# --------------------------------------------------------------------------
# SEC EDGAR
# --------------------------------------------------------------------------

def sec_json(url, ua, cache_name=None, max_age=12 * 3600):
    if cache_name:
        p = tpath("cache", "sec", cache_name)
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age:
            return load_json(p)
    data = json.loads(http_get(url, ua=ua))
    if cache_name:
        save_json(tpath("cache", "sec", cache_name), data)
    time.sleep(0.12)  # SEC asks for at most 10 requests per second
    return data


def sec_cik(ticker, ua):
    data = sec_json("https://www.sec.gov/files/company_tickers.json", ua, "company_tickers.json", 7 * 86400)
    t = ticker.upper().replace(".", "-")
    for v in data.values():
        if v["ticker"].upper() == t:
            return int(v["cik_str"]), v.get("title", "")
    die("ticker %s not found in SEC's list (US-listed companies only)" % ticker)
