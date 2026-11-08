#!/usr/bin/env python3
"""News Monitor: new headlines for your currency pairs, central-bank releases,
and the high-impact events coming up for their currencies.

    python3 news.py --pairs EURUSD GBPJPY          # only what is new since the last check
    python3 news.py --pairs XAUUSD --hours 24 --all
    python3 news.py                                # watchlist + open positions
    python3 news.py --no-banks                     # skip Fed / ECB / BoE press releases
    python3 news.py watch add EURUSD USDJPY | watch list | watch remove USDJPY

Sources: Yahoo Finance RSS per pair (EURUSD=X), and the press-release feeds of
the Federal Reserve (USD), the ECB (EUR) and the Bank of England (GBP). If
economic-analyst has fetched the calendar, the next high-impact events for the
pairs' currencies are listed too. Every item gets keyword tags and a rough
hint for the currency; what it really means is left to Claude and to you.
Seen items are remembered in trader/news_seen.json so nothing repeats.
"""
import argparse
import datetime as dt
import email.utils
import hashlib
import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

BANK_FEEDS = {
    "USD": ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    "EUR": ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
    "GBP": ("BoE", "https://www.bankofengland.co.uk/rss/news"),
}

TAGS = [
    ("rates", r"\b(rate (cut|hike|decision)|interest rate|basis points?|bps|monetary policy|fomc|policy meeting)\b"),
    ("inflation", r"\b(inflation|cpi|pce|prices? index|ppi)\b"),
    ("jobs", r"\b(payrolls?|nfp|jobs|unemployment|jobless|employment|labou?r market)\b"),
    ("growth", r"\b(gdp|growth|recession|pmi|retail sales|manufacturing)\b"),
    ("central bank", r"\b(fed|powell|ecb|lagarde|boe|bailey|boj|ueda|snb|rba|rbnz|boc|central bank|governor)\b"),
    ("intervention", r"\b(intervention|intervene|verbal warning|excessive moves|mof)\b"),
    ("risk", r"\b(war|tariffs?|sanctions?|crisis|shutdown|election|geopolitic\w*|safe[- ]haven)\b"),
    ("technical", r"\b(support|resistance|breakout|breaks? (above|below)|technical)\b"),
]
HAWK = r"\b(hawkish|hikes?|raise[sd]? rates?|tighten\w*|hot(ter)?|beats?|stronger|strong|surges?|rall(y|ies)|jumps?|rises?|gains?|higher)\b"
DOVE = r"\b(dovish|cuts?|lower(s|ed)? rates?|eas(e|ing)|cool(s|ing|er)?|miss(es|ed)?|weak(er|ens)?|slump\w*|falls?|drops?|slides?|lower)\b"


def tag(text):
    t = text.lower()
    tags = [name for name, rx in TAGS if re.search(rx, t)]
    up, dn = len(re.findall(HAWK, t)), len(re.findall(DOVE, t))
    return tags, "+" if up > dn else ("-" if dn > up else "?")


def parse_rss(raw, label, ccy=None, severity=1):
    out = []
    root = ET.fromstring(raw)
    items = list(root.iter("item")) or list(root.iter("{http://purl.org/rss/1.0/}item"))
    for it in items:
        g = lambda k: (it.findtext(k) or it.findtext("{http://purl.org/rss/1.0/}" + k) or "")  # noqa: E731
        title = html.unescape(g("title").strip())
        link = g("link").strip()
        pub = g("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
        ts = None
        try:
            ts = email.utils.parsedate_to_datetime(pub).timestamp() if pub else None
        except (TypeError, ValueError):
            try:
                ts = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = None
        desc = re.sub(r"<[^>]+>", "", html.unescape(g("description"))).strip()
        tags, hint = tag(title + " " + desc)
        sev = severity
        if ccy and ("rates" in tags or "intervention" in tags):
            sev = max(sev, 3)
        out.append({"source": label, "about": ccy or label, "title": title, "link": link, "ts": ts,
                    "summary": desc[:300], "tags": tags, "hint": hint, "severity": sev})
    return out


def fetch(pairs, banks=True):
    items, errors = [], []
    for p in pairs:
        url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US" % T.yahoo_symbol(p).replace("=", "%3D")
        try:
            for it in parse_rss(T.http_get(url), p):
                it["about"] = p
                items.append(it)
        except Exception as e:
            errors.append("yahoo %s: %s" % (p, e))
        time.sleep(0.3)
    if banks:
        ccys = {c for p in pairs for c in T.split_pair(p) if c}
        for c in sorted(ccys & set(BANK_FEEDS)):
            name, url = BANK_FEEDS[c]
            try:
                items += parse_rss(T.http_get(url), name, c, severity=2)
            except Exception as e:
                errors.append("%s feed: %s" % (name, e))
    return items, errors


def select(items, seen, hours, show_all, now=None):
    now = now or time.time()
    out, keys = [], set()
    for it in items:
        k = hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()[:16]
        it["key"] = k
        if k in keys:
            continue
        keys.add(k)
        if hours and it["ts"] and now - it["ts"] > hours * 3600:
            continue
        if not show_all and k in seen:
            continue
        out.append(it)
    out.sort(key=lambda x: (-(x["severity"] >= 3), -(x["ts"] or 0)))
    return out


def upcoming(pairs, hours=48, now=None):
    cal = T.load_json(T.tpath("calendar.json"), None)
    if not cal:
        return None
    ccys = {c for p in pairs for c in T.split_pair(p) if c}
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for e in cal.get("events", []):
        if e.get("impact") != "High" or e.get("currency") not in ccys:
            continue
        try:
            t = dt.datetime.fromisoformat(e["time"])
        except (KeyError, ValueError):
            continue
        if now - dt.timedelta(hours=1) <= t <= now + dt.timedelta(hours=hours):
            out.append(dict(e, _t=t))
    return sorted(out, key=lambda e: e["_t"])


def default_pairs(cfg):
    wl = T.load_json(T.tpath("watchlist.json"), {"symbols": []})["symbols"]
    try:
        held = [p["symbol"] for p in T.account_snapshot(cfg)["positions"]]
    except SystemExit:
        held = []
    return sorted({T.norm_symbol(s) for s in wl + held}), {T.norm_symbol(s) for s in held}


def render(items, held, errors, events, cfg):
    tz = T.user_tz(cfg)
    L = []
    if events:
        L.append("coming up (high impact, your time):")
        for e in events:
            L.append("  %s  %s %s  forecast %s, previous %s" % (e["_t"].astimezone(tz).strftime("%a %d %b %H:%M"),
                                                              e["currency"], e["title"], e.get("forecast") or "-",
                                                              e.get("previous") or "-"))
        L.append("")
    elif events is None:
        L.append("(calendar not fetched: run economic-analyst calendar to see upcoming events)")
    for it in items:
        when = dt.datetime.fromtimestamp(it["ts"], tz).strftime("%m-%d %H:%M") if it["ts"] else "?"
        flag = "!!" if it["severity"] >= 3 else ("! " if it["severity"] == 2 else "  ")
        mine = " [OPEN TRADE]" if it["about"] in held or any(it["about"] in T.split_pair(h) for h in held) else ""
        L.append("%s %s %-7s (%s) %s%s" % (flag, when, it["about"], it["hint"], it["title"], mine))
        if it["tags"]:
            L.append("      tags: %s" % ", ".join(it["tags"]))
        L.append("      %s" % it["link"])
    if not items:
        L.append("no new headlines")
    L += ["error: " + e for e in errors]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", choices=["watch"], help="manage the watchlist")
    ap.add_argument("watch_args", nargs="*")
    ap.add_argument("--pairs", nargs="*")
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--all", action="store_true", help="include items already shown")
    ap.add_argument("--no-banks", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    if a.action == "watch":
        p = T.tpath("watchlist.json")
        wl = T.load_json(p, {"symbols": []})
        sub, *syms = a.watch_args or ["list"]
        if sub == "add":
            wl["symbols"] = sorted(set(wl["symbols"]) | {T.norm_symbol(s) for s in syms})
        elif sub == "remove":
            wl["symbols"] = [s for s in wl["symbols"] if s not in {T.norm_symbol(x) for x in syms}]
        T.save_json(p, wl)
        T.emit(dict(wl, text="watchlist: " + (", ".join(wl["symbols"]) or "empty")), a.json)
        return 0
    wl, held = default_pairs(cfg)
    pairs = [T.norm_symbol(x) for x in a.pairs] if a.pairs else wl
    if not pairs:
        T.die("no pairs: pass --pairs or add a watchlist (news.py watch add EURUSD GBPUSD)")
    items, errors = fetch(pairs, not a.no_banks)
    sp = T.tpath("news_seen.json")
    seen_d = T.load_json(sp, {"seen": {}})
    new = select(items, seen_d["seen"], a.hours, a.all)
    now = int(time.time())
    for it in new:
        seen_d["seen"][it["key"]] = now
    seen_d["seen"] = {k: v for k, v in seen_d["seen"].items() if v >= now - 30 * 86400}
    seen_d["last_check"] = T.ny_now().isoformat(timespec="seconds")
    T.save_json(sp, seen_d)
    ev = upcoming(pairs)
    head = "NEWS  %s  (%d new, last %gh)" % (", ".join(pairs), len(new), a.hours)
    T.emit({"items": new, "events": [{k: v for k, v in e.items() if k != "_t"} for e in (ev or [])], "errors": errors,
            "text": head + "\n" + render(new, held, errors, ev, cfg)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
