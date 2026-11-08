#!/usr/bin/env python3
"""COT Tracker: what the big speculators hold in each currency (CFTC Commitments of Traders).

    python3 cot.py report                         # all major currencies + gold
    python3 cot.py report --currencies EUR JPY
    python3 cot.py pair EURUSD GBPJPY XAUUSD      # base vs quote positioning for a pair
    python3 cot.py history EUR --weeks 26         # week by week
    python3 cot.py report --file cot.json         # offline: rows saved from the CFTC API

Every Friday (15:30 New York) the CFTC publishes the positions held the previous
Tuesday in the currency futures on the CME. "Non-commercial" traders are the
large speculators (hedge funds, CTAs). For each currency this shows their net
position (long minus short contracts), the weekly change, the COT index
(where the net sits in its 52-week range, 0-100: above 80 = crowded long,
below 20 = crowded short) and the trend of the last weeks.
Source: publicreporting.cftc.gov (Legacy futures-only report), free, no key.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
CODES = {  # CFTC contract market codes (CME futures)
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "CHF": "092741", "CAD": "090741",
    "AUD": "232741", "NZD": "112741", "MXN": "095741", "USD": "098662",  # USD = ICE US Dollar Index
    "XAU": "088691",  # gold (COMEX)
}
NAMES = {"USD": "US Dollar Index", "XAU": "Gold"}


def fetch_rows(ccy, weeks=60, max_age=12 * 3600):
    code = CODES[ccy]
    cache = T.tpath("cache", "cot", "%s.json" % code)
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < max_age:
        return T.load_json(cache)
    q = urllib.parse.urlencode({"cftc_contract_market_code": code, "$order": "report_date_as_yyyy_mm_dd DESC",
                                "$limit": weeks})
    rows = json.loads(T.http_get(API + "?" + q, timeout=40))
    T.save_json(cache, rows)
    return rows


def _i(row, *keys):
    for k in keys:
        if row.get(k) not in (None, ""):
            return int(float(row[k]))
    return None


def series(rows):
    """Oldest first: [{date, long, short, net, oi, comm_net}]."""
    out = []
    for r in rows:
        lo = _i(r, "noncomm_positions_long_all")
        sh = _i(r, "noncomm_positions_short_all")
        if lo is None or sh is None:
            continue
        cl, cs = _i(r, "comm_positions_long_all"), _i(r, "comm_positions_short_all")
        out.append({"date": str(r.get("report_date_as_yyyy_mm_dd", ""))[:10], "long": lo, "short": sh,
                    "net": lo - sh, "oi": _i(r, "open_interest_all"),
                    "comm_net": (cl - cs) if cl is not None and cs is not None else None})
    out.sort(key=lambda x: x["date"])
    return out


def summarize(ccy, s, weeks=52):
    if not s:
        return {"currency": ccy, "error": "no data"}
    last = s[-1]
    win = s[-weeks:]
    lo, hi = min(x["net"] for x in win), max(x["net"] for x in win)
    idx = round((last["net"] - lo) / (hi - lo) * 100) if hi > lo else 50
    chg = last["net"] - s[-2]["net"] if len(s) >= 2 else None
    chg4 = last["net"] - s[-5]["net"] if len(s) >= 5 else None
    streak = 0
    for k in range(len(s) - 1, 0, -1):
        d = s[k]["net"] - s[k - 1]["net"]
        if streak == 0:
            streak = 1 if d > 0 else (-1 if d < 0 else 0)
        elif (d > 0 and streak > 0) or (d < 0 and streak < 0):
            streak += 1 if streak > 0 else -1
        else:
            break
    flags = []
    if idx >= 80:
        flags.append("crowded long (index %d): late to join, reversal risk if news turns" % idx)
    elif idx <= 20:
        flags.append("crowded short (index %d): squeeze risk on good news" % idx)
    if len(s) >= 2 and (s[-2]["net"] > 0) != (last["net"] > 0):
        flags.append("speculators flipped %s this week" % ("LONG" if last["net"] > 0 else "SHORT"))
    if abs(streak) >= 3:
        flags.append("%d weeks in a row of %s" % (abs(streak), "buying" if streak > 0 else "selling"))
    return {"currency": ccy, "name": NAMES.get(ccy, ccy), "date": last["date"], "net": last["net"],
            "long": last["long"], "short": last["short"], "change_1w": chg, "change_4w": chg4,
            "pct_long": round(last["long"] / (last["long"] + last["short"]) * 100, 1) if last["long"] + last["short"] else None,
            "cot_index": idx, "range_52w": [lo, hi], "streak": streak, "commercial_net": last["comm_net"],
            "open_interest": last["oi"], "flags": flags}


def pair_view(symbol, summ):
    base, quote = T.split_pair(symbol)
    if not base:
        T.die("%s is not a currency pair" % symbol)
    b, q = summ.get(base), summ.get(quote)
    if not b or not q or "error" in b or "error" in q:
        return {"pair": symbol, "error": "no COT data for %s or %s" % (base, quote)}
    diff = b["cot_index"] - q["cot_index"]
    if diff >= 30:
        view = "speculators favour %s over %s: supports %s upside, but watch for crowding" % (base, quote, symbol)
    elif diff <= -30:
        view = "speculators favour %s over %s: supports %s downside" % (quote, base, symbol)
    else:
        view = "no strong positioning edge between %s and %s" % (base, quote)
    mom = (b["change_1w"] or 0) / max(abs(b["net"]), 1) - (q["change_1w"] or 0) / max(abs(q["net"]), 1)
    return {"pair": symbol, "base_index": b["cot_index"], "quote_index": q["cot_index"], "difference": diff,
            "view": view, "weekly_flow": "toward %s" % (base if mom > 0 else quote) if abs(mom) > 0.02 else "flat"}


def load_all(currencies, weeks, path=None):
    out = {}
    if path:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for c in currencies:
            rows = [r for r in raw if r.get("cftc_contract_market_code") == CODES[c]]
            out[c] = series(rows)
        return out, []
    errs = []
    for c in currencies:
        try:
            out[c] = series(fetch_rows(c, max(weeks + 8, 60)))
        except Exception as e:
            errs.append("%s: %s" % (c, e))
            out[c] = []
        time.sleep(0.2)
    return out, errs


def fmt_k(n):
    return "-" if n is None else "%+.1fk" % (n / 1000.0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", help="CFTC rows saved as JSON (offline)")
    ap.add_argument("--weeks", type=int, default=52)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--currencies", nargs="*", default=["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "USD", "XAU"])
    p = sub.add_parser("pair")
    p.add_argument("pairs", nargs="+")
    h = sub.add_parser("history")
    h.add_argument("currency")
    h.add_argument("--last", type=int, default=12)
    a = ap.parse_args(argv)

    if a.cmd == "history":
        c = a.currency.upper()
        if c not in CODES:
            T.die("currency must be one of %s" % ", ".join(CODES))
        data, errs = load_all([c], a.weeks, a.file)
        s = data[c]
        L = ["COT %s  speculators' net position, week by week" % NAMES.get(c, c),
             "%-11s %9s %9s %9s %9s" % ("date", "long", "short", "net", "change")]
        for k in range(max(1, len(s) - a.last), len(s)):
            L.append("%-11s %9d %9d %9s %9s" % (s[k]["date"], s[k]["long"], s[k]["short"], fmt_k(s[k]["net"]),
                                               fmt_k(s[k]["net"] - s[k - 1]["net"])))
        L += ["error: " + e for e in errs]
        T.emit({"series": s[-a.last:], "text": "\n".join(L)}, a.json)
        return 0

    if a.cmd == "pair":
        ccys = sorted({x for pr in a.pairs for x in T.split_pair(pr) if x} & set(CODES))
    else:
        ccys = [c.upper() for c in a.currencies if c.upper() in CODES]
    data, errs = load_all(ccys, a.weeks, a.file)
    summ = {c: summarize(c, data[c], a.weeks) for c in ccys}
    if a.cmd == "report":
        dates = sorted({x.get("date") for x in summ.values() if x.get("date")})
        L = ["COT  large speculators (non-commercial), report of %s" % (dates[-1] if dates else "?"),
             "%-16s %9s %8s %8s %6s %6s  %s" % ("", "net", "1 week", "4 weeks", "%long", "index", "")]
        for c in ccys:
            x = summ[c]
            if "error" in x:
                L.append("%-16s no data" % NAMES.get(c, c))
                continue
            L.append("%-16s %9s %8s %8s %6s %6s  %s" % (x["name"], fmt_k(x["net"]), fmt_k(x["change_1w"]),
                                                       fmt_k(x["change_4w"]), x["pct_long"], x["cot_index"],
                                                       "; ".join(x["flags"])))
        L.append("index = where the net position sits in its %d-week range (0 = most short, 100 = most long)" % a.weeks)
        L += ["error: " + e for e in errs]
        T.emit({"currencies": summ, "text": "\n".join(L)}, a.json)
        return 0
    views = [pair_view(T.norm_symbol(pr), summ) for pr in a.pairs]
    L = []
    for v in views:
        if "error" in v:
            L.append("%s: %s" % (v["pair"], v["error"]))
            continue
        b, q = T.split_pair(v["pair"])
        L.append("%s: %s index %d vs %s index %d -> %s | this week's flow %s" % (
            v["pair"], b, v["base_index"], q, v["quote_index"], v["view"], v["weekly_flow"]))
    L.append("COT is weekly and 3 days old when published: a background bias, not a timing tool.")
    L += ["error: " + e for e in errs]
    T.emit({"pairs": views, "currencies": summ, "text": "\n".join(L)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
