#!/usr/bin/env python3
"""Currency Strength: which currencies are strong, which are weak, and which
pairs line them up. Plus the correlation between pairs.

    python3 strength.py rank                        # 8 majors from 28 pairs, H1, last 24 bars + 96 bars
    python3 strength.py rank --tf D1 --bars 5       # daily: last week vs last month
    python3 strength.py pairs                       # best strong-vs-weak pairs to look at
    python3 strength.py corr EURUSD GBPUSD USDJPY XAUUSD
    python3 strength.py corr --positions            # are my open trades really the same trade?
    python3 strength.py rank --csv-dir bars/        # offline: one CSV per pair (EURUSD.csv ...)

Method: for every pair, the move over the window measured in ATRs (so
volatile JPY crosses do not dominate). A currency's strength is the average
of its moves across the 7 pairs it is in (+ when it is the base currency,
- when it is the quote). Two windows: short (momentum) and long (trend =
4x short). "rising/falling" compares today's short strength with the
reading one window ago.
"""
import argparse
import itertools
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

MAJORS = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]


def all_pairs(ccys=MAJORS):
    return sorted({T.market_pair(a, b) for a, b in itertools.combinations(ccys, 2)})


def load(pairs, tf, count, csv_dir=None, source=None):
    out, missing = {}, []
    cfg = T.load_config()
    files = {}
    if csv_dir:
        for f in os.listdir(csv_dir):
            if f.lower().endswith(".csv"):
                files[T.norm_symbol(os.path.splitext(f)[0].split("_")[0])] = os.path.join(csv_dir, f)
    for p in pairs:
        try:
            if csv_dir:
                if p not in files:
                    missing.append(p)
                    continue
                out[p] = T.get_bars(p, tf, count, csv_path=files[p])
            else:
                out[p] = T.get_bars(p, tf, count, source, cfg=cfg)
                time.sleep(0.15)
        except SystemExit:
            missing.append(p)
    return out, missing


def move(b, look, end=None):
    """Move over `look` bars ending at index `end`, in ATRs (and in %)."""
    end = len(b) - 1 if end is None else end
    if end - look < 14:
        return None
    a = T.atr(b.slice(0, end + 1), 14)[-1]
    d = b.c[end] - b.c[end - look]
    return {"atr": d / a if a else 0.0, "pct": d / b.c[end - look] * 100}


def strength(bars, look, ccys=MAJORS, shift=0):
    tot = {c: [] for c in ccys}
    for p, b in bars.items():
        base, quote = T.split_pair(p)
        if base not in tot or quote not in tot:
            continue
        m = move(b, look, len(b) - 1 - shift)
        if m is None:
            continue
        tot[base].append(m["atr"])
        tot[quote].append(-m["atr"])
    return {c: (round(sum(v) / len(v), 2) if v else None) for c, v in tot.items()}


def rank(bars, look, ccys=MAJORS):
    short = strength(bars, look, ccys)
    long = strength(bars, look * 4, ccys)
    prev = strength(bars, look, ccys, shift=look)
    rows = []
    for c in ccys:
        if short[c] is None:
            continue
        trend = "rising" if prev.get(c) is not None and short[c] > prev[c] + 0.25 else (
            "falling" if prev.get(c) is not None and short[c] < prev[c] - 0.25 else "flat")
        rows.append({"currency": c, "short": short[c], "long": long[c], "prev": prev.get(c), "change": trend,
                     "score": round(short[c] + (long[c] or 0), 2)})
    rows.sort(key=lambda r: -r["score"])
    return rows


def best_pairs(rows, top=6):
    s = {r["currency"]: r for r in rows}
    out = []
    for a, b in itertools.combinations(s, 2):
        p = T.market_pair(a, b)
        base, quote = T.split_pair(p)
        diff_s = s[base]["short"] - s[quote]["short"]
        diff_l = (s[base]["long"] or 0) - (s[quote]["long"] or 0)
        agree = (diff_s > 0) == (diff_l > 0)
        out.append({"pair": p, "side": "buy" if diff_s + diff_l > 0 else "sell", "gap": round(abs(diff_s + diff_l), 2),
                    "short_gap": round(diff_s, 2), "long_gap": round(diff_l, 2), "aligned": agree,
                    "strong": base if diff_s + diff_l > 0 else quote, "weak": quote if diff_s + diff_l > 0 else base})
    out.sort(key=lambda x: (-x["aligned"], -x["gap"]))
    return out[:top]


def returns(b):
    return {b.t[i]: b.c[i] / b.c[i - 1] - 1 for i in range(1, len(b))}


def corr(bars, n=100):
    rets = {p: returns(b) for p, b in bars.items()}
    names = sorted(rets)
    m = {}
    for x, y in itertools.combinations_with_replacement(names, 2):
        common = sorted(set(rets[x]) & set(rets[y]))[-n:]
        if len(common) < 20:
            m[(x, y)] = m[(y, x)] = None
            continue
        a = [rets[x][t] for t in common]
        c = [rets[y][t] for t in common]
        ma, mc = sum(a) / len(a), sum(c) / len(c)
        cov = sum((u - ma) * (v - mc) for u, v in zip(a, c))
        va = math.sqrt(sum((u - ma) ** 2 for u in a))
        vc = math.sqrt(sum((v - mc) ** 2 for v in c))
        m[(x, y)] = m[(y, x)] = round(cov / (va * vc), 2) if va and vc else None
    return names, m


def overlap_warnings(positions, m, limit=0.7):
    out = []
    for p, q in itertools.combinations(positions, 2):
        c = m.get((p["symbol"], q["symbol"]))
        if c is None:
            continue
        same = (p["side"] == q["side"]) == (c > 0)
        if abs(c) >= limit and same:
            out.append("%s %s and %s %s move together (correlation %+.2f): effectively one bigger trade" % (
                p["side"], p["symbol"], q["side"], q["symbol"], c))
        elif abs(c) >= limit:
            out.append("%s %s and %s %s offset each other (correlation %+.2f): one hedges the other" % (
                p["side"], p["symbol"], q["side"], q["symbol"], c))
    return out


def bar(v, width=10):
    if v is None:
        return ""
    k = int(round(min(abs(v), 3) / 3 * width))
    return ("+" if v > 0 else "-") * k


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--bars", type=int, default=24, help="short window in bars (long window = 4x)")
    ap.add_argument("--csv-dir")
    ap.add_argument("--source", choices=["auto", "mt5", "yahoo"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rank")
    r.add_argument("--currencies", nargs="*", default=MAJORS)
    pr = sub.add_parser("pairs")
    pr.add_argument("--top", type=int, default=6)
    c = sub.add_parser("corr")
    c.add_argument("pairs", nargs="*")
    c.add_argument("--positions", action="store_true", help="also check open trades")
    c.add_argument("--n", type=int, default=100, help="bars of returns")
    a = ap.parse_args(argv)
    count = a.bars * 5 + 40

    if a.cmd == "corr":
        pairs = [T.norm_symbol(p) for p in a.pairs]
        pos = []
        if a.positions:
            pos = [p for p in T.account_snapshot()["positions"]]
            pairs += [p["symbol"] for p in pos]
        pairs = sorted(set(pairs))
        if len(pairs) < 2:
            T.die("give at least two pairs (or --positions with 2+ open trades)")
        bars, missing = load(pairs, a.tf, max(count, a.n + 20), a.csv_dir, a.source)
        names, m = corr(bars, a.n)
        w = max(8, max(len(n) for n in names))
        L = ["CORRELATION  %s, last %d bars of returns (+1 = move together, -1 = opposite)" % (a.tf, a.n),
             " " * w + " ".join("%7s" % n for n in names)]
        for x in names:
            L.append("%-*s" % (w, x) + " ".join("%7s" % ("-" if m[(x, y)] is None else "%+.2f" % m[(x, y)]) for y in names))
        warn = overlap_warnings(pos, m) if pos else []
        hi = [(x, y, m[(x, y)]) for x, y in itertools.combinations(names, 2) if m[(x, y)] is not None and abs(m[(x, y)]) >= 0.8]
        L += ["strong: %s %s %+.2f" % h for h in hi]
        L += ["open trades: " + x for x in warn]
        if missing:
            L.append("no data for: " + ", ".join(missing))
        T.emit({"pairs": names, "matrix": {"%s/%s" % k: v for k, v in m.items()}, "warnings": warn,
                "text": "\n".join(L)}, a.json)
        return 0

    ccys = [c.upper() for c in getattr(a, "currencies", MAJORS)]
    bars, missing = load(all_pairs(ccys), a.tf, count, a.csv_dir, a.source)
    if not bars:
        T.die("no price data loaded (connect MT5, check the internet, or pass --csv-dir)")
    rows = rank(bars, a.bars, ccys)
    if a.cmd == "rank":
        L = ["CURRENCY STRENGTH  %s  short %d bars, long %d bars  (moves in ATRs, %d of %d pairs)" % (
            a.tf, a.bars, a.bars * 4, len(bars), len(all_pairs(ccys))),
             "%-4s %7s %7s  %-8s %s" % ("", "short", "long", "change", "")]
        for x in rows:
            L.append("%-4s %+7.2f %+7.2f  %-8s %s" % (x["currency"], x["short"], x["long"] or 0, x["change"], bar(x["short"])))
        if len(rows) >= 2:
            L.append("strongest %s, weakest %s -> look at %s" % (
                rows[0]["currency"], rows[-1]["currency"], T.market_pair(rows[0]["currency"], rows[-1]["currency"])))
        if missing:
            L.append("no data for: " + ", ".join(missing))
        T.emit({"rank": rows, "missing": missing, "text": "\n".join(L)}, a.json)
        return 0
    best = best_pairs(rows, a.top)
    L = ["STRONG vs WEAK  %s (%d bars / %d bars)" % (a.tf, a.bars, a.bars * 4)]
    for b in best:
        L.append("  %-4s %-7s gap %.2f ATR  (%s strong, %s weak)%s" % (
            b["side"].upper(), b["pair"], b["gap"], b["strong"], b["weak"],
            "" if b["aligned"] else "  [short and long disagree: wait]"))
    L.append("strength picks the pair; the chart still picks the entry (chart-scanner / entry-timer).")
    T.emit({"pairs": best, "rank": rows, "text": "\n".join(L)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
