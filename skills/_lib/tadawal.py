"""Shared helpers for the Tadawal forex skill pack (standard library only).

Price data, in order of preference when data_source="auto":
  1. MetaTrader 5 desktop terminal (Windows, `pip install MetaTrader5`, terminal running)
  2. Yahoo Finance chart JSON for currency pairs (EURUSD=X; free, delayed, no volume)
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
import re
import sys
import time
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
    UTC = dt.timezone.utc
except Exception:  # pragma: no cover
    ZoneInfo, NY, UTC = None, None, dt.timezone.utc

# --------------------------------------------------------------------------
# files and config
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "mode": "paper",              # paper = simulated ledger; manual = you place orders in the MT5 app;
                                  # mt5 = orders sent to the MetaTrader 5 desktop terminal
    "allow_live": False,          # MT5 REAL accounts are refused unless this is true
    "data_source": "auto",        # auto | mt5 | yahoo
    "timezone": "Asia/Riyadh",    # times are shown in this zone
    "symbol_suffix": "",          # e.g. "m" or ".pro" if your broker renames pairs (EURUSDm)
    "symbol_map": {},             # {"XAUUSD": "GOLD"} explicit overrides
    "account_balance": 10000.0,   # paper/manual balance (MT5 reads the real one)
    "currency": "USD",            # account currency
    "contract_size": 100000,      # 1 standard lot (paper/manual)
    "leverage": 100,              # paper/manual margin estimate
    "risk_per_trade_pct": 1.0,    # max % of balance lost if the stop is hit
    "max_total_risk_pct": 5.0,    # all open stops together
    "max_open_positions": 5,
    "max_daily_loss_pct": 3.0,    # stop trading for the day after this realized loss
    "max_leverage": 10.0,         # total notional / balance after the new order
    "max_same_currency": 2,       # warn when more trades push the same currency the same way
    "max_spread_pips": 3.0,       # MT5: refuse to send when the spread is wider
    "min_rr": 1.5,                # minimum reward:risk when a target is set
    "blocked_symbols": [],
    "allow_short": True,          # selling a pair is normal in forex
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
    norm["symbol"] = norm_symbol(norm["symbol"] or "")
    return hashlib.sha256(json.dumps(norm, sort_keys=True).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url, ua=None, timeout=30, retries=3, accept=None):
    headers = {"User-Agent": ua or "Mozilla/5.0 (Tadawal skill pack)", "Accept-Encoding": "gzip"}
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


# --------------------------------------------------------------------------
# symbols, pips, prices
# --------------------------------------------------------------------------

CCYS = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "SGD", "HKD", "MXN",
        "ZAR", "TRY", "PLN", "CNH", "SAR", "AED", "XAU", "XAG"}
DIGITS = 5  # display precision of the symbol being worked on (set by get_bars / set_symbol)


def norm_symbol(s):
    s = str(s or "").upper().replace("/", "").replace("_", "").replace("-", "").strip()
    return s[:-2] if s.endswith("=X") else s


def split_pair(symbol):
    s = norm_symbol(symbol)
    m = re.match(r"^([A-Z]{3})([A-Z]{3})", s)
    if m and m.group(1) in CCYS and m.group(2) in CCYS:
        return m.group(1), m.group(2)
    return None, None


def pip_size(symbol):
    base, quote = split_pair(symbol)
    if base in ("XAU",):
        return 0.1
    if base in ("XAG",):
        return 0.01
    return 0.01 if quote == "JPY" else 0.0001


def digits_for(symbol):
    base, quote = split_pair(symbol)
    if base == "XAU":
        return 2
    if base == "XAG":
        return 3
    return 3 if quote == "JPY" else 5


def set_symbol(symbol):
    global DIGITS
    DIGITS = digits_for(symbol)


def pips(diff, symbol):
    return round(diff / pip_size(symbol), 1)


def r(x, n=None):
    return None if x is None else round(float(x), DIGITS if n is None else n)


def px(x, symbol=None):
    """Price as text with the symbol's digits."""
    if x is None:
        return "-"
    d = digits_for(symbol) if symbol else DIGITS
    return "%.*f" % (d, float(x))


def yahoo_symbol(symbol):
    s = norm_symbol(symbol)
    base, quote = split_pair(s)
    if base == "XAU":
        return "GC=F"
    if base == "XAG":
        return "SI=F"
    return s + "=X" if base else s


def broker_symbol(symbol, cfg):
    s = norm_symbol(symbol)
    return cfg.get("symbol_map", {}).get(s) or (s + (cfg.get("symbol_suffix") or ""))


# --------------------------------------------------------------------------
# forex clock and sessions
# --------------------------------------------------------------------------

SESSIONS = [  # name, zone, local open hour, local close hour
    ("Sydney", "Australia/Sydney", 7, 16),
    ("Tokyo", "Asia/Tokyo", 9, 18),
    ("London", "Europe/London", 8, 17),
    ("New York", "America/New_York", 8, 17),
]
SESSION_CCY = {"Sydney": {"AUD", "NZD"}, "Tokyo": {"JPY", "AUD", "NZD"}, "London": {"EUR", "GBP", "CHF"},
               "New York": {"USD", "CAD"}}


def user_tz(cfg=None):
    name = (cfg or {}).get("timezone") or DEFAULT_CONFIG["timezone"]
    try:
        return ZoneInfo(name) if ZoneInfo else UTC
    except Exception:
        return UTC


def ny_now():
    return dt.datetime.now(NY) if NY else dt.datetime.now(UTC)


def _fx_closed(t_ny):
    wd, hm = t_ny.weekday(), t_ny.hour * 60 + t_ny.minute
    if (wd == 4 and hm >= 17 * 60) or wd == 5 or (wd == 6 and hm < 17 * 60):
        return "weekend"
    if t_ny.strftime("%m-%d") in ("12-25", "01-01"):
        return "holiday"
    return None


def active_sessions(now):
    out = []
    for name, zone, o, c in SESSIONS:
        loc = now.astimezone(ZoneInfo(zone)) if ZoneInfo else now
        if loc.weekday() < 5 and o <= loc.hour < c:
            out.append(name)
    return out


def market_clock(now=None, cfg=None):
    """Forex is open from Sunday 17:00 New York to Friday 17:00 New York."""
    now = now or ny_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY)
    t_ny = now.astimezone(NY)
    tz = user_tz(cfg)
    loc = lambda d: d.astimezone(tz).strftime("%a %Y-%m-%d %H:%M")  # noqa: E731
    out = {"now_ny": t_ny.strftime("%Y-%m-%d %H:%M %a"), "now_local": loc(now), "open": False, "warnings": []}
    why = _fx_closed(t_ny)
    if why:
        nxt = t_ny.replace(minute=0, second=0, microsecond=0)
        while _fx_closed(nxt) or nxt <= t_ny:
            nxt += dt.timedelta(hours=1)
        out["status"] = "forex closed (%s)" % why
        out["next_open"] = loc(nxt)
        out["minutes_to_open"] = int((nxt - t_ny).total_seconds() // 60)
        out["sessions"] = []
        return out
    fri = t_ny + dt.timedelta(days=(4 - t_ny.weekday()) % 7)
    close = fri.replace(hour=17, minute=0, second=0, microsecond=0)
    if close <= t_ny:
        close += dt.timedelta(days=7)
    ses = active_sessions(now)
    out.update(open=True, sessions=ses, closes_at=loc(close),
               minutes_to_close=int((close - t_ny).total_seconds() // 60))
    hm = t_ny.hour * 60 + t_ny.minute
    if 16 * 60 + 50 <= hm < 17 * 60 + 20:
        out["warnings"].append("daily rollover (17:00 New York): spreads are wide, avoid new entries")
    if out["minutes_to_close"] < 180:
        out["warnings"].append("weekly close in %d minutes: weekend gap risk for open trades" % out["minutes_to_close"])
    if "London" in ses and "New York" in ses:
        liq = "London/New York overlap: highest liquidity"
    elif ses == ["Sydney"] or not ses:
        liq = "thin liquidity (only Sydney or between sessions)"
    else:
        liq = "/".join(ses) + " session"
    out["liquidity"] = liq
    out["status"] = "forex open: " + liq
    return out


def session_note(symbol, clock):
    """Is the pair's own currency in an active session?"""
    base, quote = split_pair(symbol)
    if not clock.get("open") or not base:
        return None
    hot = set()
    for s in clock.get("sessions", []):
        hot |= SESSION_CCY.get(s, set())
    if base in hot or quote in hot:
        return None
    return "%s%s is quiet now (its currencies' sessions are closed): smaller moves, false breakouts" % (base, quote)


# --------------------------------------------------------------------------
# bars
# --------------------------------------------------------------------------

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800}


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
        d = dt.datetime.fromtimestamp(self.t[i], UTC)
        if fmt:
            return d.strftime(fmt)
        return d.strftime("%Y-%m-%d") if self.tf in ("D1", "W1") else d.strftime("%Y-%m-%d %H:%M")

    def has_volume(self):
        return any(v > 0 for v in self.v[-50:])


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
            die("MetaTrader 5 desktop is not connected. It needs Windows, `pip install MetaTrader5`, and the MT5 "
                "terminal open and logged in. The phone app cannot be connected: use \"mode\": \"manual\".")
        return _MT5_READY or None
    mt5 = _mt5_module()
    if mt5 is None:
        _MT5_READY = False
        return mt5_connect(cfg, required)
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
    for x in rates:
        t.append(int(x["time"]))
        o.append(float(x["open"]))
        h.append(float(x["high"]))
        l.append(float(x["low"]))
        c.append(float(x["close"]))
        v.append(float(x["tick_volume"]))
    return Bars(norm_symbol(symbol), tf, t, o, h, l, c, v, "mt5:" + sym)


YAHOO_TF = {"M1": ("1m", "7d"), "M5": ("5m", "60d"), "M15": ("15m", "60d"), "M30": ("30m", "60d"),
            "H1": ("60m", "730d"), "H4": ("60m", "730d"), "D1": ("1d", "10y"), "W1": ("1wk", "max")}


def parse_yahoo_chart(raw, symbol, tf):
    js = json.loads(raw)
    res = (js.get("chart") or {}).get("result")
    if not res:
        raise ValueError("Yahoo returned no data for %s: %s" % (symbol, (js.get("chart") or {}).get("error")))
    q = res[0]["indicators"]["quote"][0]
    t, o, h, l, c, v = [], [], [], [], [], []
    for i, ts in enumerate(res[0].get("timestamp") or []):
        vals = [q["open"][i], q["high"][i], q["low"][i], q["close"][i]]
        if any(x is None for x in vals):
            continue
        t.append(int(ts))
        o.append(float(vals[0]))
        h.append(max(float(x) for x in vals))
        l.append(min(float(x) for x in vals))
        c.append(float(vals[3]))
        v.append(float((q.get("volume") or [0] * (i + 1))[i] or 0))
    return Bars(norm_symbol(symbol), tf, t, o, h, l, c, v, "yahoo")


def resample(b, tf):
    step = TF_SECONDS[tf]
    out = Bars(b.symbol, tf, [], [], [], [], [], [], b.source)
    key_prev = None
    for i in range(len(b)):
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
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.request.quote(yahoo_symbol(symbol)), rng, interval)
    b = parse_yahoo_chart(http_get(url), symbol, tf)
    if tf == "H4":
        b = resample(b, "H4")
    return b.slice(-count) if count else b


def load_csv(path, symbol="CSV", tf="D1"):
    """Read MT5-exported bars (<DATE>\t<TIME>\t<OPEN>...) or a plain OHLCV CSV."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    first = text.splitlines()[0]
    delim = "\t" if "\t" in first else ("," if "," in first else ";")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    head = [x.strip().strip("<>").lower() for x in rows[0]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None

    ci = {k: col(*names) for k, names in {
        "date": ("date", "datetime", "time", "timestamp"), "time": ("time",),
        "o": ("open",), "h": ("high",), "l": ("low",), "c": ("close",),
        "v": ("tickvol", "tick_volume", "volume", "vol", "real_volume")}.items()}
    if None in (ci["date"], ci["o"], ci["h"], ci["l"], ci["c"]):
        die("CSV needs date/open/high/low/close columns; got %s" % head)
    if ci["time"] == ci["date"]:
        ci["time"] = None
    t, o, h, l, c, v = [], [], [], [], [], []
    for row in rows[1:]:
        if not row or not row[ci["date"]].strip():
            continue
        s = row[ci["date"]].strip()
        if ci["time"] is not None:
            s += " " + row[ci["time"]].strip()
        t.append(_parse_ts(s))
        o.append(float(row[ci["o"]])); h.append(float(row[ci["h"]]))
        l.append(float(row[ci["l"]])); c.append(float(row[ci["c"]]))
        v.append(float(row[ci["v"]]) if ci["v"] is not None and row[ci["v"]] else 0.0)
    order = sorted(range(len(t)), key=lambda i: t[i])
    pick = lambda a: [a[i] for i in order]  # noqa: E731
    return Bars(norm_symbol(symbol), tf, pick(t), pick(o), pick(h), pick(l), pick(c), pick(v), "csv:" + path)


def _parse_ts(s):
    s = s.strip()
    if s.isdigit():
        n = int(s)
        return n // 1000 if n > 10 ** 11 else n
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %H:%M"):
        try:
            return int(dt.datetime.strptime(s, fmt).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    die("cannot parse date %r in CSV" % s)


def get_bars(symbol, tf="D1", count=500, source=None, csv_path=None, cfg=None):
    tf = tf.upper()
    if tf not in TIMEFRAMES:
        die("timeframe must be one of %s" % (TIMEFRAMES,))
    set_symbol(symbol)
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
        die("could not load %s %s from Yahoo (%s). Export bars from MT5 and pass --csv instead." % (symbol, tf, e))


_PRICE_OVERRIDE = {}  # {"EURUSD": 1.08}: prices read from a screenshot, offline use, tests


def set_prices(text):
    """'EURUSD=1.0850,USDJPY=156.39' -> used instead of fetching (e.g. prices read from the phone app)."""
    for part in re.split(r"[,\s]+", text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            _PRICE_OVERRIDE[norm_symbol(k)] = float(v)


set_prices(os.environ.get("TADAWAL_PRICES", ""))


def get_price(symbol, cfg=None, source=None):
    """Last price and previous daily close: MT5 tick if connected, otherwise Yahoo (delayed)."""
    sym = norm_symbol(symbol)
    if sym in _PRICE_OVERRIDE:
        return {"symbol": sym, "price": _PRICE_OVERRIDE[sym], "prev_close": None, "source": "override"}
    cfg = cfg or load_config()
    source = (source or cfg.get("data_source") or "auto").lower()
    if source in ("auto", "mt5"):
        mt5 = mt5_connect(cfg)
        if mt5:
            bs = broker_symbol(sym, cfg)
            mt5.symbol_select(bs, True)
            tk = mt5.symbol_info_tick(bs)
            if tk:
                d1 = mt5.copy_rates_from_pos(bs, mt5.TIMEFRAME_D1, 1, 1)
                prev = float(d1[0]["close"]) if d1 is not None and len(d1) else None
                return {"symbol": sym, "price": (float(tk.bid) + float(tk.ask)) / 2, "bid": float(tk.bid),
                        "ask": float(tk.ask), "spread_pips": pips(float(tk.ask) - float(tk.bid), sym),
                        "prev_close": prev, "time": int(tk.time), "source": "mt5"}
            if source == "mt5":
                die("no MT5 tick for %s" % bs)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m" % urllib.request.quote(
        yahoo_symbol(sym))
    try:
        meta = json.loads(http_get(url))["chart"]["result"][0]["meta"]
    except Exception as e:
        die("no price for %s from Yahoo (%s)" % (sym, e))
    return {"symbol": sym, "price": float(meta.get("regularMarketPrice")),
            "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "time": meta.get("regularMarketTime"), "source": "yahoo (delayed)"}


def add_data_args(ap, tf_default="H1", count_default=400):
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
    sig = [None] * start + (ema(line[start:], signal) if start < len(line) else [])
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(x, n=20, k=2.0):
    mid = sma(x, n)
    up, lo = [None] * len(x), [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        sd = math.sqrt(sum((v - mid[i]) ** 2 for v in w) / n)
        up[i], lo[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, up, lo


def highest(x, n):
    """Highest of the previous n values, excluding the current bar."""
    return [None if i < n else max(x[i - n:i]) for i in range(len(x))]


def lowest(x, n):
    return [None if i < n else min(x[i - n:i]) for i in range(len(x))]


def slope_points(pts):
    n = len(pts)
    if n < 2:
        return 0.0
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else 0.0


def pivots(b, left=3, right=3):
    """Fractal swing points: [(index, 'H'|'L', price)], confirmed only."""
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
    """Alternating confirmed swing points; each leg moves at least `threshold`."""
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
    pts = pivots(b.slice(start), left, right)
    tol = max(atr_value * 0.5, 1e-12)
    clusters = []
    for i, _, price in sorted(pts, key=lambda p: p[2]):
        if clusters and price - clusters[-1]["prices"][-1] <= tol:
            clusters[-1]["prices"].append(price)
            clusters[-1]["last"] = max(clusters[-1]["last"], i + start)
        else:
            clusters.append({"prices": [price], "last": i + start})
    out = [{"price": r(sum(c["prices"]) / len(c["prices"])), "touches": len(c["prices"]),
            "last_touch": b.date(c["last"])} for c in clusters]
    out.sort(key=lambda d: (-d["touches"], d["price"]))
    return sorted(out[:max_levels], key=lambda d: d["price"])


# --------------------------------------------------------------------------
# account, positions, contract specs
# --------------------------------------------------------------------------

def use_mt5(cfg):
    return (cfg.get("mode") or "paper").lower() == "mt5"


def paper_state(cfg=None):
    cfg = cfg or load_config()
    st = load_json(tpath("paper.json"), None)
    if st is None:
        st = {"balance": float(cfg.get("account_balance", 10000)), "positions": [], "pending": [], "next_id": 1,
              "created": ny_now().isoformat()}
    return st


def save_paper(st):
    save_json(tpath("paper.json"), st)


PRIORITY = ["XAU", "XAG", "EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]


def market_pair(a, b):
    """The way the market quotes two currencies: ('EUR','USD') -> 'EURUSD', ('JPY','USD') -> 'USDJPY'."""
    ra = PRIORITY.index(a) if a in PRIORITY else 99
    rb = PRIORITY.index(b) if b in PRIORITY else 99
    return a + b if ra <= rb else b + a


def conv_rate(ccy, acc, cfg=None, price_fn=None):
    """How many units of `acc` one unit of `ccy` is worth."""
    if ccy == acc:
        return 1.0
    price_fn = price_fn or (lambda s: get_price(s, cfg)["price"])
    pair = market_pair(ccy, acc)
    try:
        p = price_fn(pair)
    except SystemExit:
        p = None
    if p:
        return p if pair.startswith(ccy) else 1.0 / p
    if "USD" not in (ccy, acc):
        return conv_rate(ccy, "USD", cfg, price_fn) * conv_rate("USD", acc, cfg, price_fn)
    die("cannot convert %s to %s: no price for %s" % (ccy, acc, pair))


def symbol_spec(symbol, cfg=None, price_fn=None):
    """Contract specification. tick_value = account-currency P&L of one tick on 1 lot."""
    cfg = cfg or load_config()
    sym = norm_symbol(symbol)
    if use_mt5(cfg) or cfg.get("data_source") == "mt5":
        mt5 = mt5_connect(cfg)
        if mt5:
            bs = broker_symbol(sym, cfg)
            mt5.symbol_select(bs, True)
            si = mt5.symbol_info(bs)
            if si is None:
                die("symbol %s not found at your broker (set symbol_suffix / symbol_map in trader/config.json)" % bs)
            return {"symbol": sym, "broker_symbol": bs, "tick_size": si.trade_tick_size or si.point,
                    "tick_value": si.trade_tick_value, "contract_size": si.trade_contract_size or 1,
                    "volume_min": si.volume_min, "volume_max": si.volume_max, "volume_step": si.volume_step,
                    "digits": si.digits, "base": getattr(si, "currency_base", split_pair(sym)[0]),
                    "quote": getattr(si, "currency_profit", split_pair(sym)[1]), "source": "mt5"}
    base, quote = split_pair(sym)
    if not base:
        die("%s is not a currency pair (like EURUSD, GBPJPY, XAUUSD)" % symbol)
    contract = 100.0 if base == "XAU" else (5000.0 if base == "XAG" else float(cfg.get("contract_size", 100000)))
    point = 10 ** -digits_for(sym)
    acc = cfg.get("currency", "USD")
    return {"symbol": sym, "broker_symbol": sym, "tick_size": point,
            "tick_value": contract * point * conv_rate(quote, acc, cfg, price_fn), "contract_size": contract,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01, "digits": digits_for(sym),
            "base": base, "quote": quote, "source": "standard lots"}


def money_per_point(spec):
    """Account-currency P&L for a 1.0 price move on 1 lot."""
    return spec["tick_value"] / spec["tick_size"] if spec["tick_size"] else spec["contract_size"]


def pip_value(spec):
    """Account-currency value of one pip on 1 lot."""
    return money_per_point(spec) * pip_size(spec["symbol"])


def notional(spec, volume, price):
    """Position size in account currency (base amount converted at `price`)."""
    return volume * spec["contract_size"] * price * money_per_point(spec) / spec["contract_size"]


def round_volume(vol, spec, down=True):
    step = spec["volume_step"] or 0.01
    k = math.floor(vol / step + 1e-9) if down else round(vol / step)
    return min(round(k * step, 8), spec["volume_max"])


def account_snapshot(cfg=None):
    cfg = cfg or load_config()
    if use_mt5(cfg):
        mt5 = mt5_connect(cfg, required=True)
        ai = mt5.account_info()
        if ai is None:
            die("MT5 account_info() is empty: log in to your account in the terminal")
        pos = []
        for p in mt5.positions_get() or []:
            pos.append({"id": int(p.ticket), "symbol": norm_symbol(p.symbol), "broker_symbol": p.symbol,
                        "side": "buy" if p.type == 0 else "sell", "volume": float(p.volume),
                        "entry": float(p.price_open), "sl": float(p.sl) or None, "tp": float(p.tp) or None,
                        "price": float(p.price_current), "profit": float(p.profit),
                        "opened": dt.datetime.fromtimestamp(int(p.time), UTC).isoformat()})
        return {"mode": "mt5", "account": int(ai.login), "server": ai.server,
                "trade_mode": {0: "demo", 1: "contest", 2: "real"}.get(ai.trade_mode, str(ai.trade_mode)),
                "balance": float(ai.balance), "equity": float(ai.equity), "free_margin": float(ai.margin_free),
                "margin": float(getattr(ai, "margin", 0.0)), "leverage": getattr(ai, "leverage", None),
                "currency": ai.currency, "positions": pos}
    st = paper_state(cfg)
    pos = [dict(p) for p in st["positions"]]
    upl = 0.0
    for p in pos:
        cur = p.get("price") or p["entry"]
        sign = 1 if p["side"] == "buy" else -1
        p["profit"] = round(sign * (cur - p["entry"]) * p["volume"] * p.get("mpp", 1), 2)
        upl += p["profit"]
    return {"mode": cfg.get("mode", "paper"), "trade_mode": cfg.get("mode", "paper"), "balance": round(st["balance"], 2),
            "equity": round(st["balance"] + upl, 2), "currency": cfg.get("currency", "USD"),
            "leverage": cfg.get("leverage"), "positions": pos, "pending": st.get("pending", [])}


def journal():
    return load_json(tpath("journal.json"), {"trades": []})


def save_journal(j):
    save_json(tpath("journal.json"), j)


def realized_today(cfg=None):
    """Realized P&L since 17:00 New York (the forex trading day)."""
    cfg = cfg or load_config()
    now = ny_now()
    start = now.replace(hour=17, minute=0, second=0, microsecond=0)
    if now < start:
        start -= dt.timedelta(days=1)
    if use_mt5(cfg):
        mt5 = mt5_connect(cfg, required=True)
        deals = mt5.history_deals_get(start.astimezone(UTC).replace(tzinfo=None),
                                      dt.datetime.utcnow() + dt.timedelta(days=1)) or []
        return round(sum(d.profit + d.commission + d.swap for d in deals if d.entry in (1, 2, 3)), 2)
    tot = 0.0
    for t in journal()["trades"]:
        if t.get("status") == "closed" and t.get("closed_at"):
            try:
                ca = dt.datetime.fromisoformat(str(t["closed_at"]))
            except ValueError:
                continue
            if ca.tzinfo is None:
                ca = ca.replace(tzinfo=NY)
            if ca >= start:
                tot += float(t.get("pnl") or 0)
    return round(tot, 2)


def load_orders():
    return load_json(tpath("orders.json"), {"orders": []})


def save_orders(o):
    save_json(tpath("orders.json"), o)


def exposure(positions):
    """Net direction per currency: long EURUSD = EUR +1, USD -1."""
    ex = {}
    for p in positions:
        b, q = split_pair(p["symbol"])
        if not b:
            continue
        s = 1 if p["side"] == "buy" else -1
        ex[b] = ex.get(b, 0) + s
        ex[q] = ex.get(q, 0) - s
    return ex
