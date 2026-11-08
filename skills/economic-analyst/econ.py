#!/usr/bin/env python3
"""Economic Analyst: the data releases that move currencies, before and after.

    python3 econ.py calendar                          # this week, high + medium impact, your time
    python3 econ.py calendar --currencies USD EUR --impact high
    python3 econ.py today                             # what is left today
    python3 econ.py surprises                         # released numbers vs forecast, and the effect
    python3 econ.py actual "Non-Farm Employment Change" USD 254K   # record a result you read
    python3 econ.py bias EURUSD                       # fundamental tilt for a pair from recent surprises
    python3 econ.py calendar --file ff.json           # offline: a saved copy of the weekly feed

Source: the free weekly calendar feed at nfs.faireconomy.media (the data behind
ForexFactory's calendar): title, currency, time, impact, forecast, previous,
and actual when the feed has it. Results the feed does not carry can be added
with `actual` (Claude can look them up). Every fetch is merged into
trader/calendar_history.json so surprises build up over the weeks.
trader/calendar.json is what entry-timer and news-monitor read.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# higher-than-forecast is BAD for the currency for these
INVERTED = re.compile(r"unemployment|jobless|claimant|claims|trade deficit|budget deficit|inventories", re.I)
WEIGHTS = [  # how much a release usually matters, by keyword
    (re.compile(r"rate decision|cash rate|bank rate|policy rate|interest rate|main refinancing|fomc statement", re.I), 3.0),
    (re.compile(r"non-farm|nfp|employment change|cpi|inflation|core pce", re.I), 2.0),
    (re.compile(r"gdp|retail sales|pmi|unemployment|average hourly|wage", re.I), 1.5),
]


def num(x):
    """'254K' -> 254000, '0.3%' -> 0.3, '-1.2B' -> -1.2e9, '<0.1%' -> 0.1."""
    if x in (None, ""):
        return None
    s = str(x).strip().replace(",", "").lstrip("<>~")
    m = re.match(r"^([-+]?\d*\.?\d+)\s*([KMBT%]?)", s, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2).upper(), 1)


def weight(title):
    for rx, w in WEIGHTS:
        if rx.search(title):
            return w
    return 1.0


def normalize(raw):
    """Feed rows -> events with ISO UTC time."""
    out = []
    for e in raw:
        try:
            t = dt.datetime.fromisoformat(e["date"]).astimezone(dt.timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"title": e.get("title", "").strip(), "currency": (e.get("country") or "").upper(),
                    "time": t.isoformat(), "impact": (e.get("impact") or "").capitalize(),
                    "forecast": e.get("forecast") or None, "previous": e.get("previous") or None,
                    "actual": e.get("actual") or None})
    return sorted(out, key=lambda e: e["time"])


def key(e):
    return "%s|%s|%s" % (e["time"][:10], e["currency"], e["title"])


def fetch(path=None, max_age=1800):
    cache = T.tpath("calendar.json")
    if path:
        with open(path, encoding="utf-8") as f:
            events = normalize(json.load(f))
    else:
        cur = T.load_json(cache, None)
        if cur and time.time() - os.path.getmtime(cache) < max_age:
            return cur
        try:
            events = normalize(json.loads(T.http_get(FEED, timeout=30)))
        except Exception as e:
            if cur:
                cur["stale"] = "could not refresh (%s); showing the saved copy" % e
                return cur
            T.die("could not load the economic calendar (%s). Try again later, or pass --file." % e)
    hist = T.load_json(T.tpath("calendar_history.json"), {"events": {}})
    for e in events:
        old = hist["events"].get(key(e), {})
        if old.get("actual") and not e.get("actual"):
            e["actual"] = old["actual"]
        hist["events"][key(e)] = dict(old, **{k: v for k, v in e.items() if v is not None})
    T.save_json(T.tpath("calendar_history.json"), hist)
    data = {"fetched": T.ny_now().isoformat(timespec="seconds"), "events": events}
    T.save_json(cache, data)
    return data


def surprise(e):
    a, f = num(e.get("actual")), num(e.get("forecast"))
    if a is None or f is None:
        return None
    diff = a - f
    p = num(e.get("previous"))
    # a "normal" surprise is about the size of the expected change, or 5% of the level
    scale = max(abs(f - p) if p is not None else 0.0, abs(f) * 0.05, 1e-9)
    rel = diff / scale
    good = (diff > 0) != bool(INVERTED.search(e["title"]))
    if abs(diff) < 1e-12:
        effect = 0
    else:
        effect = 1 if good else -1
    return {"diff": diff, "rel": round(rel, 3), "effect": effect, "score": round(effect * min(abs(rel), 1.0) * weight(e["title"]), 3)}


def filt(events, currencies=None, impact="medium", start=None, end=None):
    levels = {"high": {"High"}, "medium": {"High", "Medium"}, "low": {"High", "Medium", "Low"}, "all": None}[impact]
    out = []
    for e in events:
        if currencies and e["currency"] not in currencies:
            continue
        if levels and e["impact"] not in levels:
            continue
        t = dt.datetime.fromisoformat(e["time"])
        if start and t < start or end and t > end:
            continue
        out.append(e)
    return out


def line(e, tz):
    t = dt.datetime.fromisoformat(e["time"]).astimezone(tz)
    s = surprise(e)
    res = ""
    if e.get("actual"):
        res = "  ACTUAL %s" % e["actual"]
        if s:
            res += " (%s, %s for %s)" % ("above forecast" if s["diff"] > 0 else ("below forecast" if s["diff"] < 0 else "as expected"),
                                        {1: "positive", -1: "negative", 0: "neutral"}[s["effect"]], e["currency"])
    imp = {"High": "!!", "Medium": "! "}.get(e["impact"], "  ")
    return "%s %s  %s %-38s forecast %-7s previous %-7s%s" % (imp, t.strftime("%a %d %b %H:%M"), e["currency"],
                                                             e["title"][:38], e.get("forecast") or "-",
                                                             e.get("previous") or "-", res)


def bias(symbol, hist_events, days=14, now=None):
    base, quote = T.split_pair(symbol)
    if not base:
        T.die("%s is not a currency pair" % symbol)
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    sc = {base: 0.0, quote: 0.0}
    used = []
    for e in hist_events:
        if e["currency"] not in sc or not e.get("actual"):
            continue
        t = dt.datetime.fromisoformat(e["time"])
        if t < since or t > now:
            continue
        s = surprise(e)
        if s and s["effect"]:
            sc[e["currency"]] += s["score"]
            used.append((e, s))
    net = sc[base] - sc[quote]
    tilt = "bullish %s" % symbol if net > 0.5 else ("bearish %s" % symbol if net < -0.5 else "neutral / mixed")
    return {"pair": symbol, "base": base, "quote": quote, "scores": {k: round(v, 2) for k, v in sc.items()},
            "net": round(net, 2), "tilt": tilt, "events_used": len(used),
            "detail": [{"currency": e["currency"], "title": e["title"], "actual": e["actual"], "forecast": e.get("forecast"),
                        "score": s["score"]} for e, s in used]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", help="read the weekly feed from a saved JSON file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calendar")
    c.add_argument("--currencies", nargs="*")
    c.add_argument("--pairs", nargs="*", help="use the currencies of these pairs")
    c.add_argument("--impact", choices=["high", "medium", "low", "all"], default="medium")
    c.add_argument("--past", action="store_true", help="include events already released this week")
    t = sub.add_parser("today")
    t.add_argument("--currencies", nargs="*")
    t.add_argument("--impact", choices=["high", "medium", "low", "all"], default="medium")
    s = sub.add_parser("surprises")
    s.add_argument("--currencies", nargs="*")
    s.add_argument("--days", type=int, default=14)
    r = sub.add_parser("actual", help="record a released number")
    r.add_argument("title")
    r.add_argument("currency")
    r.add_argument("value")
    r.add_argument("--date", help="YYYY-MM-DD (default: the most recent matching event)")
    b = sub.add_parser("bias")
    b.add_argument("pairs", nargs="+")
    b.add_argument("--days", type=int, default=14)
    a = ap.parse_args(argv)
    cfg = T.load_config()
    tz = T.user_tz(cfg)
    now = dt.datetime.now(dt.timezone.utc)

    if a.cmd == "actual":
        hist = T.load_json(T.tpath("calendar_history.json"), {"events": {}})
        cands = [k for k, e in hist["events"].items() if e["currency"] == a.currency.upper()
                 and a.title.lower() in e["title"].lower() and (not a.date or k.startswith(a.date))
                 and dt.datetime.fromisoformat(e["time"]) <= now + dt.timedelta(hours=1)]
        if not cands:
            T.die("no %s event matching %r in the saved calendar (run calendar first)" % (a.currency, a.title))
        k = sorted(cands)[-1]
        hist["events"][k]["actual"] = a.value
        T.save_json(T.tpath("calendar_history.json"), hist)
        cal = T.load_json(T.tpath("calendar.json"), None)
        if cal:
            for e in cal["events"]:
                if key(e) == k:
                    e["actual"] = a.value
            T.save_json(T.tpath("calendar.json"), cal)
        e = hist["events"][k]
        T.emit({"event": e, "text": "recorded: " + line(e, tz)}, a.json)
        return 0

    data = fetch(a.file)
    events = data["events"]
    head = []
    if data.get("stale"):
        head.append("note: " + data["stale"])

    if a.cmd in ("calendar", "today"):
        ccys = set(x.upper() for x in (a.currencies or []))
        for p in getattr(a, "pairs", None) or []:
            ccys |= {x for x in T.split_pair(p) if x}
        if a.cmd == "today":
            loc = now.astimezone(tz)
            end = loc.replace(hour=23, minute=59).astimezone(dt.timezone.utc)
            sel = filt(events, ccys or None, a.impact, now - dt.timedelta(hours=2), end)
            title = "TODAY (your time %s)" % loc.strftime("%a %d %b %H:%M")
        else:
            sel = filt(events, ccys or None, a.impact, None if a.past else now - dt.timedelta(hours=2))
            title = "ECONOMIC CALENDAR  this week, %s impact%s (your time)" % (
                a.impact, " for " + " ".join(sorted(ccys)) if ccys else "")
        L = head + [title]
        L += [line(e, tz) for e in sel] or ["nothing scheduled"]
        hi = [e for e in sel if e["impact"] == "High" and dt.datetime.fromisoformat(e["time"]) > now]
        if hi:
            nxt = hi[0]
            mins = int((dt.datetime.fromisoformat(nxt["time"]) - now).total_seconds() // 60)
            L.append("next high-impact: %s %s in %dh%02dm" % (nxt["currency"], nxt["title"], mins // 60, mins % 60))
        T.emit({"events": sel, "text": "\n".join(L)}, a.json)
        return 0

    hist = list(T.load_json(T.tpath("calendar_history.json"), {"events": {}})["events"].values())
    if a.cmd == "surprises":
        ccys = set(x.upper() for x in (a.currencies or []))
        since = now - dt.timedelta(days=a.days)
        rows = [e for e in hist if e.get("actual") and (not ccys or e["currency"] in ccys)
                and since <= dt.datetime.fromisoformat(e["time"]) <= now]
        rows.sort(key=lambda e: e["time"], reverse=True)
        L = head + ["SURPRISES  last %d days (actual vs forecast)" % a.days]
        L += [line(e, tz) for e in rows] or ["no released results stored yet. The feed may not carry actuals: "
                                             "look them up and record them with `econ.py actual`."]
        missing = [e for e in hist if not e.get("actual") and e["impact"] == "High"
                   and since <= dt.datetime.fromisoformat(e["time"]) <= now]
        if missing:
            L.append("released but no actual stored (%d): %s" % (len(missing), "; ".join(
                "%s %s" % (e["currency"], e["title"]) for e in missing[:8])))
        T.emit({"events": rows, "missing_actuals": missing, "text": "\n".join(L)}, a.json)
        return 0

    outs, L = [], head[:]
    for p in a.pairs:
        o = bias(T.norm_symbol(p), hist, a.days, now)
        outs.append(o)
        L.append("BIAS %s (last %d days, %d results): %s | %s %+.2f vs %s %+.2f" % (
            o["pair"], a.days, o["events_used"], o["tilt"], o["base"], o["scores"][o["base"]], o["quote"],
            o["scores"][o["quote"]]))
        for d in o["detail"]:
            L.append("   %s %s: %s vs %s (%+.2f)" % (d["currency"], d["title"], d["actual"], d["forecast"], d["score"]))
        upcoming = [e for e in filt(events, {o["base"], o["quote"]}, "high", now)]
        if upcoming:
            L.append("   next risk: " + "; ".join("%s %s %s" % (
                dt.datetime.fromisoformat(e["time"]).astimezone(tz).strftime("%a %H:%M"), e["currency"], e["title"])
                for e in upcoming[:4]))
    L.append("A data tilt is one input, not a signal: price action and central-bank tone can outweigh it.")
    T.emit({"bias": outs, "text": "\n".join(L)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
