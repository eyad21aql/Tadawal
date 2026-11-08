#!/usr/bin/env python3
"""Market Structure (Smart Money Concepts) for a currency pair or gold.

    python3 smc.py EURUSD                        # M15 entry structure, H4 bias
    python3 smc.py XAUUSD --tf H1 --htf D1
    python3 smc.py GBPUSD --tf M15 --csv gbpusd_m15.csv --htf-csv gbpusd_h4.csv
    python3 smc.py EURUSD --swing 5              # bigger swings (default 3 bars each side)

Finds, on closed candles only:
  * swing highs/lows and the structure (HH HL / LH LL)
  * BOS (break of structure, with the trend) and CHoCH (change of character,
    first break against it), by candle CLOSE beyond the swing
  * order blocks: the last opposite candle before the move that broke structure,
    with mitigated / unmitigated status
  * fair value gaps (3-candle imbalances) still open
  * liquidity: equal highs / equal lows not yet taken, and recent sweeps
    (wick through a level, close back inside = stop hunt)
  * premium / discount of the current dealing range and the OTE zone (62-79%)
and, when the pieces line up, an SMC setup with entry, stop, target and R:R.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

HTF = {"M1": "M15", "M5": "H1", "M15": "H4", "M30": "H4", "H1": "D1", "H4": "D1", "D1": "W1", "W1": None}


def structure(b, k=3):
    """Walk the bars; return swings, break events and the final trend."""
    piv = T.pivots(b, k, k)
    events, swings = [], []
    trend = None
    hi = lo = None          # latest confirmed, unbroken swing high / low: (index, price)
    p = 0
    for i in range(len(b)):
        while p < len(piv) and piv[p][0] + k <= i:
            idx, kind, price = piv[p]
            label = ""
            prev = [s for s in swings if s["type"] == kind]
            if prev:
                label = ("HH" if price > prev[-1]["price"] else "LH") if kind == "H" else ("HL" if price > prev[-1]["price"] else "LL")
            swings.append({"index": idx, "type": kind, "price": price, "label": label})
            if kind == "H":
                hi = (idx, price)
            else:
                lo = (idx, price)
            p += 1
        c = b.c[i]
        if hi and c > hi[1]:
            kind = "CHoCH" if trend == "down" else "BOS"
            events.append({"index": i, "type": kind, "dir": "bull", "level": hi[1], "swing": hi[0]})
            trend, hi = "up", None
        elif lo and c < lo[1]:
            kind = "CHoCH" if trend == "up" else "BOS"
            events.append({"index": i, "type": kind, "dir": "bear", "level": lo[1], "swing": lo[0]})
            trend, lo = "down", None
    return swings, events, trend


def order_blocks(b, events, atr):
    obs = []
    for e in events:
        i, s = e["index"], e["swing"]
        if e["dir"] == "bull":
            origin = min(range(s, i + 1), key=lambda j: b.l[j])
            cand = next((j for j in range(origin, max(origin - 10, -1), -1) if b.c[j] < b.o[j]), None)
        else:
            origin = max(range(s, i + 1), key=lambda j: b.h[j])
            cand = next((j for j in range(origin, max(origin - 10, -1), -1) if b.c[j] > b.o[j]), None)
        if cand is None:
            continue
        top, bot = b.h[cand], b.l[cand]
        state = "unmitigated"
        for j in range(i + 1, len(b)):
            if e["dir"] == "bull":
                if b.c[j] < bot:
                    state = "invalidated"
                    break
                if b.l[j] <= top:
                    state = "mitigated"
            else:
                if b.c[j] > top:
                    state = "invalidated"
                    break
                if b.h[j] >= bot:
                    state = "mitigated"
        obs.append({"dir": e["dir"], "index": cand, "top": top, "bottom": bot, "state": state, "from": e["type"],
                    "date": b.date(cand)})
    return obs


def fvgs(b, atr, look=150):
    out = []
    n = len(b)
    for j in range(max(1, n - look), n - 1):
        if b.l[j + 1] > b.h[j - 1] and b.l[j + 1] - b.h[j - 1] >= 0.1 * atr:
            top, bot, d = b.l[j + 1], b.h[j - 1], "bull"
        elif b.h[j + 1] < b.l[j - 1] and b.l[j - 1] - b.h[j + 1] >= 0.1 * atr:
            top, bot, d = b.l[j - 1], b.h[j + 1], "bear"
        else:
            continue
        state = "open"
        for k in range(j + 2, n):
            if d == "bull":
                if b.l[k] <= bot:
                    state = "filled"
                    break
                if b.l[k] < top:
                    state = "partly filled"
            else:
                if b.h[k] >= top:
                    state = "filled"
                    break
                if b.h[k] > bot:
                    state = "partly filled"
        if state != "filled":
            out.append({"dir": d, "index": j, "top": top, "bottom": bot, "state": state, "date": b.date(j)})
    return out


def liquidity(b, swings, atr, look=150, recent=10):
    n = len(b)
    tol = max(0.1 * atr, 2 * T.pip_size(b.symbol))
    pools, sweeps = [], []
    for kind in ("H", "L"):
        pts = [s for s in swings if s["type"] == kind and s["index"] >= n - look]
        used = set()
        for x in range(len(pts)):
            group = [pts[x]] + [pts[y] for y in range(x + 1, len(pts)) if abs(pts[y]["price"] - pts[x]["price"]) <= tol]
            if len(group) >= 2 and pts[x]["index"] not in used:
                used |= {g["index"] for g in group}
                lvl = max(g["price"] for g in group) if kind == "H" else min(g["price"] for g in group)
                last = max(g["index"] for g in group)
                after = range(last + 1, n)
                taken = any(b.h[j] > lvl for j in after) if kind == "H" else any(b.l[j] < lvl for j in after)
                if not taken:
                    pools.append({"side": "buy-side" if kind == "H" else "sell-side", "level": lvl, "touches": len(group)})
    levels = [(s["price"], s["type"], s["index"]) for s in swings if s["index"] < n - 1][-8:]
    for j in range(max(0, n - recent), n):
        for lvl, kind, si in levels:
            if si >= j:
                continue
            if kind == "H" and b.h[j] > lvl and b.c[j] < lvl and max(b.h[si + 1:j] or [lvl]) <= lvl:
                sweeps.append({"index": j, "date": b.date(j), "side": "buy-side", "level": lvl, "bias": "bear"})
            if kind == "L" and b.l[j] < lvl and b.c[j] > lvl and min(b.l[si + 1:j] or [lvl]) >= lvl:
                sweeps.append({"index": j, "date": b.date(j), "side": "sell-side", "level": lvl, "bias": "bull"})
    uniq = {}
    for s in sweeps:  # one sweep per bar and side: the most extreme level taken
        k = (s["index"], s["side"])
        if k not in uniq or (s["level"] > uniq[k]["level"]) == (s["side"] == "buy-side"):
            uniq[k] = s
    return pools, sorted(uniq.values(), key=lambda s: s["index"])


def dealing_range(swings, price):
    hs = [s for s in swings if s["type"] == "H"]
    ls = [s for s in swings if s["type"] == "L"]
    if not hs or not ls:
        return None
    h, l = hs[-1]["price"], ls[-1]["price"]
    if h <= l:
        return None
    pos = (price - l) / (h - l)
    return {"high": h, "low": l, "position": round(pos, 2),
            "zone": "premium" if pos > 0.5 else "discount", "eq": (h + l) / 2,
            "ote_buy": (h - 0.79 * (h - l), h - 0.62 * (h - l)), "ote_sell": (l + 0.62 * (h - l), l + 0.79 * (h - l))}


def setup(b, trend, htf_trend, rng, obs, gaps, pools, sweeps, events, atr, min_rr=2.0):
    price = b.c[-1]
    buf = max(0.1 * atr, 2 * T.pip_size(b.symbol))
    bias = htf_trend or trend
    if bias not in ("up", "down"):
        return None, "structure unclear: no setup"
    bull = bias == "up"
    if trend and trend != bias:
        return None, "entry timeframe structure (%s) is against the higher-timeframe bias (%s): wait for a CHoCH back" % (trend, bias)
    if rng and ((bull and rng["zone"] != "discount") or (not bull and rng["zone"] != "premium")):
        return None, "price is in %s: %s setups need %s. Wait for a pullback toward %s" % (
            rng["zone"], "buy" if bull else "sell", "discount" if bull else "premium", T.px(rng["eq"]))
    want = "bull" if bull else "bear"
    zones = [dict(z, kind="order block") for z in obs if z["dir"] == want and z["state"] == "unmitigated"]
    zones += [dict(z, kind="FVG") for z in gaps if z["dir"] == want]
    zones = [z for z in zones if (z["top"] < price if bull else z["bottom"] > price)]
    if not zones:
        return None, "no fresh %s order block or FVG on the way: wait" % ("bullish" if bull else "bearish")
    z = max(zones, key=lambda z: z["top"]) if bull else min(zones, key=lambda z: z["bottom"])
    entry = z["top"] if bull else z["bottom"]
    stop = (z["bottom"] - buf) if bull else (z["top"] + buf)
    targets = [p["level"] for p in pools if (p["side"] == "buy-side") == bull and (p["level"] - entry) * (1 if bull else -1) > 0]
    if rng:
        targets.append(rng["high"] if bull else rng["low"])
    targets = sorted(targets, key=lambda x: abs(x - entry))
    risk = abs(entry - stop)
    tp = next((t for t in targets if abs(t - entry) >= min_rr * risk), entry + (1 if bull else -1) * min_rr * risk)
    swept = [s for s in sweeps if s["bias"] == want]
    choch = [e for e in events[-3:] if e["type"] == "CHoCH" and e["dir"] == want]
    quality = "A (sweep + CHoCH + zone)" if swept and choch else ("B (zone in trend)" if not swept else "B+ (sweep + zone)")
    return {"side": "buy" if bull else "sell", "type": "limit", "entry": entry, "stop": stop, "target": tp,
            "rr": round(abs(tp - entry) / risk, 2), "stop_pips": T.pips(risk, b.symbol),
            "target_pips": T.pips(abs(tp - entry), b.symbol), "zone": z["kind"], "zone_date": z["date"],
            "quality": quality}, None


def analyze(b, htf=None, k=3, min_rr=2.0):
    if len(b) < 60:
        T.die("need at least 60 bars, got %d" % len(b))
    T.set_symbol(b.symbol)
    atr = T.atr(b, 14)[-1]
    swings, events, trend = structure(b, k)
    htf_trend = None
    if htf is not None and len(htf) >= 30:
        _, _, htf_trend = structure(htf, k)
    obs = order_blocks(b, events[-12:], atr)
    gaps = fvgs(b, atr)
    pools, sweeps = liquidity(b, swings, atr)
    rng = dealing_range(swings, b.c[-1])
    st, why = setup(b, trend, htf_trend, rng, obs, gaps, pools, sweeps, events, atr, min_rr)
    out = {"symbol": b.symbol, "tf": b.tf, "source": b.source, "bar": b.date(len(b) - 1), "price": b.c[-1],
           "trend": trend, "htf_trend": htf_trend, "htf": htf.tf if htf is not None else None,
           "swings": [dict(s, date=b.date(s["index"])) for s in swings[-6:]],
           "events": [dict(e, date=b.date(e["index"])) for e in events[-5:]],
           "order_blocks": [o for o in obs if o["state"] != "invalidated"][-6:], "fvgs": gaps[-6:],
           "liquidity": pools, "sweeps": sweeps, "range": rng, "setup": st, "no_setup": why}
    out["text"] = render(out)
    return out


def render(o):
    s, P = o["symbol"], T.px
    price = o["price"]
    d = lambda x: "%+.0f pips" % T.pips(x - price, s)  # noqa: E731
    L = ["MARKET STRUCTURE  %s %s  (%s, bar %s UTC)" % (s, o["tf"], o["source"], o["bar"]),
         "price %s | structure %s | %s bias %s" % (P(price), o["trend"] or "unclear", o["htf"] or "HTF",
                                                  o["htf_trend"] or "n/a"),
         "swings: " + " ".join("%s %s" % (x["label"] or x["type"], P(x["price"])) for x in o["swings"])]
    for e in o["events"]:
        L.append("  %s %s %s: close beyond %s" % (e["date"], e["type"], "bullish" if e["dir"] == "bull" else "bearish", P(e["level"])))
    if o["range"]:
        r = o["range"]
        L.append("dealing range %s - %s: price at %d%% = %s (equilibrium %s)" % (
            P(r["low"]), P(r["high"]), r["position"] * 100, r["zone"].upper(), P(r["eq"])))
    for z in o["order_blocks"]:
        L.append("  OB %s %s-%s (%s, from %s, %s) %s" % ("bull" if z["dir"] == "bull" else "bear", P(z["bottom"]),
                                                         P(z["top"]), z["date"], z["from"], z["state"], d((z["top"] + z["bottom"]) / 2)))
    for g in o["fvgs"]:
        L.append("  FVG %s %s-%s (%s, %s) %s" % (g["dir"], P(g["bottom"]), P(g["top"]), g["date"], g["state"],
                                                 d((g["top"] + g["bottom"]) / 2)))
    for p in o["liquidity"]:
        L.append("  liquidity %s at %s (equal %s x%d) %s" % (p["side"], P(p["level"]),
                                                            "highs" if p["side"] == "buy-side" else "lows", p["touches"], d(p["level"])))
    for w in o["sweeps"]:
        L.append("  SWEEP %s %s liquidity at %s: wick through, close back (%s)" % (
            w["date"], w["side"], P(w["level"]), "bearish" if w["bias"] == "bear" else "bullish"))
    if o["setup"]:
        x = o["setup"]
        L.append("SETUP %s: %s limit %s, stop %s (%s pips), target %s (%s pips), R:R %s, entry zone = %s from %s" % (
            x["quality"], x["side"].upper(), P(x["entry"]), P(x["stop"]), x["stop_pips"], P(x["target"]),
            x["target_pips"], x["rr"], x["zone"], x["zone_date"]))
    else:
        L.append("no setup: " + (o["no_setup"] or ""))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    T.add_data_args(ap, "M15", 500)
    ap.add_argument("--htf", help="bias timeframe (default: M15->H4, H1->D1...); 'none' to skip")
    ap.add_argument("--htf-csv")
    ap.add_argument("--swing", type=int, default=3, help="bars each side for a swing point")
    ap.add_argument("--min-rr", type=float, default=2.0)
    a = ap.parse_args(argv)
    cfg = T.load_config()
    b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv, cfg)
    htf = (a.htf or HTF.get(a.tf.upper()) or "none").upper()
    hb = None
    if htf != "NONE" and (a.htf_csv or not a.csv):
        try:
            hb = T.get_bars(a.symbol, htf, 300, a.source, a.htf_csv, cfg)
        except SystemExit:
            hb = None
    T.set_symbol(b.symbol)
    o = analyze(b, hb, a.swing, a.min_rr)
    if a.json:
        for key in ("order_blocks", "fvgs", "events", "swings"):
            for x in o[key]:
                x.pop("index", None)
    T.emit(o, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
