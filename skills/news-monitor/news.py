#!/usr/bin/env python3
"""News Monitor: new headlines and SEC 8-K filings for the stocks you care about.

    python3 news.py --tickers AAPL NVDA TSLA          # only what is new since the last check
    python3 news.py --tickers AAPL --hours 48 --all   # everything from the last 48 hours
    python3 news.py                                   # watchlist + open positions
    python3 news.py --market                          # add broad-market headlines (S&P 500, Nasdaq)
    python3 news.py watch add AAPL MSFT | watch list | watch remove MSFT

Sources: Yahoo Finance RSS per ticker, and each company's 8-K filings on SEC
EDGAR (item codes decoded: earnings, CEO change, restatement, bankruptcy...).
Every item gets keyword tags and a rough hint (+/-); the judgment of what it
means is left to Claude and to you. Seen items are remembered in
trader/news_seen.json so nothing is shown twice.
"""
import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

ITEMS_8K = {
    "1.01": ("material agreement", 1), "1.02": ("agreement terminated", 2), "1.03": ("BANKRUPTCY / receivership", 3),
    "1.05": ("cybersecurity incident", 3), "2.01": ("acquisition or sale completed", 2),
    "2.02": ("EARNINGS / results of operations", 2), "2.03": ("new debt obligation", 1),
    "2.04": ("debt acceleration triggered", 3), "2.05": ("restructuring / layoffs costs", 2),
    "2.06": ("material impairment", 3), "3.01": ("DELISTING notice", 3), "3.02": ("unregistered share sale (dilution)", 2),
    "3.03": ("shareholder rights modified", 2), "4.01": ("auditor changed", 3),
    "4.02": ("RESTATEMENT: past financials unreliable", 3), "5.01": ("change in control", 3),
    "5.02": ("director / officer change (CEO? CFO?)", 2), "5.03": ("bylaws / fiscal year amended", 1),
    "5.07": ("shareholder vote results", 1), "7.01": ("Reg FD disclosure (presentation, guidance)", 1),
    "8.01": ("other events", 1), "9.01": ("financial exhibits", 0),
}

TAGS = [
    ("earnings", r"\b(earnings|results|quarter|q[1-4]\b|eps|revenue|sales)\b"),
    ("guidance", r"\b(guidance|outlook|forecast)\b"),
    ("analyst", r"\b(upgrade|downgrade|price target|initiat\w+ coverage|rating)\b"),
    ("deal", r"\b(acquir\w*|acquisition|merger|buyout|takeover|stake)\b"),
    ("legal", r"\b(lawsuit|sued|probe|investigation|sec charges|doj|antitrust|settle\w*|fine[ds]?)\b"),
    ("product", r"\b(launch\w*|unveil\w*|recall\w*|fda|approval|approved)\b"),
    ("management", r"\b(ceo|cfo|chief executive|resign\w*|steps down|appoint\w*)\b"),
    ("capital", r"\b(buyback|repurchase|dividend|offering|split)\b"),
    ("insider", r"\b(insider|form 4|sells shares|buys shares)\b"),
]
POS = r"\b(beat[s]?|tops?|raise[sd]?|record|surge[sd]?|soar\w*|jump\w*|upgrade[sd]?|strong|approval|approved|buyback|rall\w+|wins?)\b"
NEG = r"\b(miss(es|ed)?|cut[s]?|lower[sd]?|plunge[sd]?|sink[s]?|fall[s]?|drop[s]?|downgrade[sd]?|weak|lawsuit|probe|recall\w*|warn\w*|slump\w*|layoffs?|bankrupt\w*|delist\w*|restat\w*)\b"


def tag(text):
    t = text.lower()
    tags = [name for name, rx in TAGS if re.search(rx, t)]
    p, n = len(re.findall(POS, t)), len(re.findall(NEG, t))
    hint = "+" if p > n else ("-" if n > p else "?")
    return tags, hint


def parse_rss(raw, ticker):
    out = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        try:
            ts = email.utils.parsedate_to_datetime(pub).timestamp() if pub else None
        except (TypeError, ValueError):
            ts = None
        desc = re.sub(r"<[^>]+>", "", html.unescape(it.findtext("description") or "")).strip()
        tags, hint = tag(title + " " + desc)
        out.append({"source": "yahoo", "ticker": ticker, "title": title, "link": link, "ts": ts,
                    "summary": desc[:300], "tags": tags, "hint": hint, "severity": 1})
    return out


def parse_8k_atom(raw, ticker):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    root = ET.fromstring(raw)
    company = root.findtext("a:company-info/a:conformed-name", default="", namespaces=ns) or ticker
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = e.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        upd = e.findtext("a:updated", default="", namespaces=ns)
        summ = html.unescape(e.findtext("a:summary", default="", namespaces=ns) or "")
        summ_txt = re.sub(r"<[^>]+>", " ", summ)
        items = sorted(set(re.findall(r"Item\s+(\d\.\d\d)", summ_txt)))
        cat = e.find("a:category", ns)
        form = cat.get("term") if cat is not None else "8-K"
        try:
            ts = dt.datetime.fromisoformat(upd.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = None
        decoded = [ITEMS_8K.get(i, ("item " + i, 1)) for i in items]
        sev = max([d[1] for d in decoded] or [1])
        what = "; ".join("%s %s" % (i, d[0]) for i, d in zip(items, decoded) if d[1] > 0) or title
        tags, hint = tag(what)
        if "2.02" in items:
            tags = sorted(set(tags + ["earnings"]))
        out.append({"source": "sec", "ticker": ticker, "title": "%s %s: %s" % (company, form, what), "link": link,
                    "ts": ts, "items": items, "summary": " ".join(summ_txt.split())[:300], "tags": tags,
                    "hint": "-" if sev == 3 else hint, "severity": sev})
    return out


def cik_map(ua):
    p = T.tpath("cache", "company_tickers.json")
    data = T.load_json(p)
    if data is None or time.time() - os.path.getmtime(p) > 7 * 86400:
        data = json.loads(T.http_get("https://www.sec.gov/files/company_tickers.json", ua=ua))
        T.save_json(p, data)
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def fetch(tickers, market=False, sec=True, ua=None):
    items, errors = [], []
    feeds = list(tickers) + (["^GSPC", "^IXIC"] if market else [])
    for tk in feeds:
        url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US" % tk.replace("^", "%5E")
        try:
            items += parse_rss(T.http_get(url), tk)
        except Exception as e:
            errors.append("yahoo %s: %s" % (tk, e))
        time.sleep(0.3)
    if sec and tickers:
        try:
            ciks = cik_map(ua)
        except Exception as e:
            ciks = {}
            errors.append("SEC ticker map: %s" % e)
        for tk in tickers:
            cik = ciks.get(tk.upper().replace(".", "-")) or ciks.get(tk.upper())
            if not cik:
                continue
            url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%010d&type=8-K&dateb=&owner=include"
                   "&count=10&output=atom" % cik)
            try:
                items += parse_8k_atom(T.http_get(url, ua=ua), tk)
            except Exception as e:
                errors.append("sec %s: %s" % (tk, e))
            time.sleep(0.15)
    return items, errors


def select(items, seen, hours, show_all, now=None):
    now = now or time.time()
    out = []
    keys = set()
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


def default_tickers(cfg):
    wl = T.load_json(T.tpath("watchlist.json"), {"symbols": []})["symbols"]
    try:
        held = [p["symbol"] for p in T.account_snapshot(cfg)["positions"]]
    except SystemExit:
        held = []
    return sorted({s.upper() for s in wl + held}), set(s.upper() for s in held)


def render(items, held, errors):
    L = []
    for it in items:
        when = dt.datetime.fromtimestamp(it["ts"], T.NY).strftime("%m-%d %H:%M") if it["ts"] and T.NY else "?"
        flag = "!!" if it["severity"] >= 3 else ("! " if it["severity"] == 2 else "  ")
        mine = " [HELD]" if it["ticker"].upper() in held else ""
        L.append("%s %s %-6s (%s) %s%s" % (flag, when, it["ticker"], it["hint"], it["title"], mine))
        if it["tags"]:
            L.append("      tags: %s" % ", ".join(it["tags"]))
        L.append("      %s" % it["link"])
    if not items:
        L.append("nothing new")
    L += ["error: " + e for e in errors]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", choices=["watch"], help="manage the watchlist")
    ap.add_argument("watch_args", nargs="*")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--all", action="store_true", help="include items already shown")
    ap.add_argument("--market", action="store_true")
    ap.add_argument("--no-sec", action="store_true")
    ap.add_argument("--user-agent")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    if a.action == "watch":
        p = T.tpath("watchlist.json")
        wl = T.load_json(p, {"symbols": []})
        sub, *syms = a.watch_args or ["list"]
        if sub == "add":
            wl["symbols"] = sorted(set(wl["symbols"]) | {s.upper() for s in syms})
        elif sub == "remove":
            wl["symbols"] = [s for s in wl["symbols"] if s not in {x.upper() for x in syms}]
        T.save_json(p, wl)
        T.emit(dict(wl, text="watchlist: " + (", ".join(wl["symbols"]) or "empty")), a.json)
        return 0
    tickers = [t.upper() for t in a.tickers] if a.tickers else None
    held = set()
    if not tickers:
        tickers, held = default_tickers(cfg)
    else:
        _, held = default_tickers(cfg)
    if not tickers and not a.market:
        T.die("no tickers: pass --tickers or add a watchlist (news.py watch add AAPL MSFT)")
    ua = None
    if not a.no_sec:
        ua = (a.user_agent or os.environ.get("SEC_USER_AGENT") or cfg.get("sec_user_agent") or "").strip()
        if "@" not in ua:
            print("note: SEC 8-Ks skipped (set SEC_USER_AGENT=\"Your Name you@example.com\")", file=sys.stderr)
            a.no_sec = True
    items, errors = fetch(tickers, a.market, not a.no_sec, ua)
    sp = T.tpath("news_seen.json")
    seen_d = T.load_json(sp, {"seen": {}})
    new = select(items, seen_d["seen"], a.hours, a.all)
    now = int(time.time())
    for it in new:
        seen_d["seen"][it["key"]] = now
    cutoff = now - 30 * 86400
    seen_d["seen"] = {k: v for k, v in seen_d["seen"].items() if v >= cutoff}
    seen_d["last_check"] = T.ny_now().isoformat(timespec="seconds")
    T.save_json(sp, seen_d)
    head = "NEWS  %s  (%d new, last %gh)%s" % (", ".join(tickers), len(new), a.hours, "  + market" if a.market else "")
    T.emit({"items": new, "errors": errors, "text": head + "\n" + render(new, held, errors)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
