#!/usr/bin/env python3
"""Entry Timer: is now a good moment to enter SYMBOL, and if not, what to wait for.

    python3 timer.py AAPL                  # daily bars, higher timeframe = weekly
    python3 timer.py AAPL --tf H1          # hourly, higher timeframe = daily
    python3 timer.py AAPL --csv aapl.csv   # bars exported from MetaTrader 5
    python3 timer.py --clock               # NYSE open/closed only

Checklist scoring, not a prediction. Prints a long and a short score, the
missing conditions, a pullback plan and a breakout plan with a reference stop.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

HTF = {"M1": "M15", "M5": "H1", "M15": "H1", "M30": "H4", "H1": "D1", "H4": "D1", "D1": "W1", "W1": None}


def trend_of(c, e50, e200):
    i = len(c) - 1
    if e50[i] is None:
        return "unknown"
    s50 = e50[i] - e50[max(0, i - 5)] if e50[max(0, i - 5)] is not None else 0
    if e200[i] is not None:
        if c[i] > e50[i] > e200[i] and s50 > 0:
            return "up"
        if c[i] < e50[i] < e200[i] and s50 < 0:
            return "down"
        return "sideways"
    if c[i] > e50[i] and s50 > 0:
        return "up"
    if c[i] < e50[i] and s50 < 0:
        return "down"
    return "sideways"


def analyze(b, htf_bars=None, clock=None):
    if len(b) < 60:
        T.die("need at least 60 bars, got %d" % len(b))
    c, i = b.c, len(b) - 1
    e20, e50, e200 = T.ema(c, 20), T.ema(c, 50), T.ema(c, 200)
    rs = T.rsi(c, 14)
    _, _, hist = T.macd(c)
    a = T.atr(b, 14)[i]
    vavg = T.sma(b.v, 20)[i] or 0
    px = c[i]
    trend = trend_of(c, e50, e200)
    htrend = None
    if htf_bars is not None and len(htf_bars) >= 60:
        htrend = trend_of(htf_bars.c, T.ema(htf_bars.c, 50), T.ema(htf_bars.c, 200))
    lv = T.levels(b, a)
    sup = max((x for x in lv if x["price"] < px), key=lambda x: x["price"], default=None)
    res = min((x for x in lv if x["price"] > px), key=lambda x: x["price"], default=None)
    hist_up = hist[i] is not None and hist[i - 1] is not None and hist[i] > hist[i - 1]
    hist_dn = hist[i] is not None and hist[i - 1] is not None and hist[i] < hist[i - 1]
    cross_up = any(hist[k] is not None and hist[k - 1] is not None and hist[k - 1] <= 0 < hist[k]
                   for k in range(i - 2, i + 1))
    cross_dn = any(hist[k] is not None and hist[k - 1] is not None and hist[k - 1] >= 0 > hist[k]
                   for k in range(i - 2, i + 1))
    ext = (px - e20[i]) / a if a else 0
    vol_ok = vavg and b.v[i] >= vavg
    broke_up = res is None and px >= max(b.h[-21:-1])
    broke_dn = sup is None and px <= min(b.l[-21:-1])

    def side_score(long):
        sc, met, miss = 0.0, [], []

        def chk(cond, pts, txt_ok, txt_miss):
            nonlocal sc
            if cond:
                sc += pts
                met.append(txt_ok)
            else:
                miss.append(txt_miss)

        want = "up" if long else "down"
        chk(trend == want, 2, "trend is %s (price vs EMA50/EMA200)" % want,
            "trend is %s, not %s" % (trend, want))
        if htrend is not None:
            chk(htrend == want, 1, "higher timeframe agrees (%s)" % htrend,
                "higher timeframe is %s" % htrend)
        lvl = sup if long else res
        near_ma = abs(px - e20[i]) <= a or (e50[i] is not None and abs(px - e50[i]) <= a)
        near_lvl = lvl is not None and abs(px - lvl["price"]) <= a
        chk(near_ma or near_lvl, 1, "price is at value (within 1 ATR of EMA20/50 or a level)",
            "price is %.1f ATR %s EMA20 and not at a level; wait for price to come to value"
            % (abs(ext), "above" if ext > 0 else "below"))
        if long:
            chk(rs[i] is not None and (40 <= rs[i] <= 65 or (rs[i] < 40 and rs[i] > rs[i - 1])), 1,
                "RSI %.0f has room" % (rs[i] or 0), "RSI %.0f is not in the 40-65 zone" % (rs[i] or 0))
            chk(hist_up or cross_up, 1, "MACD momentum turning up", "MACD momentum still falling")
            room = (res["price"] - px) / a if res and a else None
            chk(room is None or room >= 2 or broke_up, 1,
                "room to resistance: %s" % ("open sky (new 20-bar high)" if room is None else "%.1f ATR" % room),
                "resistance %.2f is only %.1f ATR away" % (res["price"], room) if res else "")
            chk(vol_ok and c[i] > b.o[i], 0.5, "volume above average on an up bar", "no volume confirmation")
            if rs[i] is not None and rs[i] > 75:
                sc -= 1
                miss.append("RSI %.0f overbought: chasing" % rs[i])
            if ext > 3:
                sc -= 1
                miss.append("price %.1f ATR above EMA20: extended" % ext)
        else:
            chk(rs[i] is not None and (35 <= rs[i] <= 60 or (rs[i] > 60 and rs[i] < rs[i - 1])), 1,
                "RSI %.0f has room down" % (rs[i] or 0), "RSI %.0f not in the 35-60 zone" % (rs[i] or 0))
            chk(hist_dn or cross_dn, 1, "MACD momentum turning down", "MACD momentum still rising")
            room = (px - sup["price"]) / a if sup and a else None
            chk(room is None or room >= 2 or broke_dn, 1,
                "room to support: %s" % ("open (new 20-bar low)" if room is None else "%.1f ATR" % room),
                "support %.2f is only %.1f ATR away" % (sup["price"], room) if sup else "")
            chk(vol_ok and c[i] < b.o[i], 0.5, "volume above average on a down bar", "no volume confirmation")
            if rs[i] is not None and rs[i] < 25:
                sc -= 1
                miss.append("RSI %.0f oversold: chasing" % rs[i])
            if ext < -3:
                sc -= 1
                miss.append("price %.1f ATR below EMA20: extended" % -ext)
        top = 7.5 if htrend is not None else 6.5
        verdict = "ENTRY ZONE" if sc >= top - 2 else ("WAIT" if sc >= top - 4 else "NO TRADE")
        return {"score": round(sc, 1), "of": top, "verdict": verdict, "met": met,
                "missing": [m for m in miss if m]}

    long_s, short_s = side_score(True), side_score(False)
    pull = None
    if sup or e20[i] < px:
        cands = [x for x in ([sup["price"]] if sup else []) + [e20[i]] if x < px]
        lvl = max(cands) if cands else px - a
        stop = (sup["price"] if sup else lvl) - 0.5 * a
        if stop >= lvl - 0.3 * a:
            stop = lvl - 1.5 * a
        pull = {"entry_limit": T.r(lvl), "stop_ref": T.r(stop), "risk_per_share": T.r(lvl - stop)}
    brk_lvl = res["price"] if res else max(b.h[-21:])
    brk = {"entry_stop": T.r(brk_lvl + 0.1 * a), "stop_ref": T.r(brk_lvl - 1.5 * a),
           "note": "only on a close above with above-average volume"}

    timing = []
    if clock:
        if not clock["open"]:
            timing.append("%s; next open %s. Orders placed now wait for the open." %
                          (clock["status"], clock.get("next_open")))
        elif b.tf not in ("D1", "W1") and clock.get("minutes_since_open", 99) < 15:
            timing.append("first 15 minutes of the session: let the opening range form before entering")
        elif clock.get("minutes_to_close", 99) < 15:
            timing.append("last 15 minutes of the session: avoid new intraday entries")
        else:
            timing.append(clock["status"])
    if b.tf in ("D1", "W1"):
        timing.append("daily signals are read on the close; act near the next open or with a limit order")

    out = {
        "symbol": b.symbol, "tf": b.tf, "source": b.source, "bar": b.date(i),
        "price": T.r(px), "atr14": T.r(a), "rsi14": T.r(rs[i], 1),
        "ema20": T.r(e20[i]), "ema50": T.r(e50[i]), "ema200": T.r(e200[i]),
        "trend": trend, "higher_tf_trend": htrend,
        "support": sup, "resistance": res, "levels": lv,
        "long": long_s, "short": short_s,
        "pullback_plan": pull, "breakout_plan": brk, "timing": timing,
    }
    out["text"] = render(out)
    return out


def render(o):
    L = []
    L.append("ENTRY TIMER  %s %s  (%s, bar %s)" % (o["symbol"], o["tf"], o["source"], o["bar"]))
    L.append("price %s | ATR %s | RSI %s | EMA20 %s EMA50 %s EMA200 %s" %
             (o["price"], o["atr14"], o["rsi14"], o["ema20"], o["ema50"], o["ema200"]))
    L.append("trend %s | higher TF %s | support %s | resistance %s" % (
        o["trend"], o["higher_tf_trend"] or "n/a",
        T.r(o["support"]["price"]) if o["support"] else "none below",
        T.r(o["resistance"]["price"]) if o["resistance"] else "none above"))
    for side in ("long", "short"):
        s = o[side]
        L.append("")
        L.append("%s: %s  (score %s / %s)" % (side.upper(), s["verdict"], s["score"], s["of"]))
        for m in s["met"]:
            L.append("  + " + m)
        for m in s["missing"]:
            L.append("  - " + m)
    L.append("")
    if o["pullback_plan"]:
        p = o["pullback_plan"]
        L.append("pullback plan: buy limit %s, reference stop %s (risk %s/share)" %
                 (p["entry_limit"], p["stop_ref"], p["risk_per_share"]))
    bp = o["breakout_plan"]
    L.append("breakout plan: buy stop %s, reference stop %s (%s)" % (bp["entry_stop"], bp["stop_ref"], bp["note"]))
    for t in o["timing"]:
        L.append("timing: " + t)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?")
    T.add_data_args(ap, "D1", 400)
    ap.add_argument("--htf", help="higher timeframe (default: one step up); 'none' to skip")
    ap.add_argument("--htf-csv", help="CSV for the higher timeframe")
    ap.add_argument("--clock", action="store_true", help="only print NYSE open/closed")
    a = ap.parse_args(argv)
    clock = T.market_clock()
    if a.clock or not a.symbol:
        T.emit(dict(clock, text=clock["status"] + ("" if clock["open"] else ", next open " + clock["next_open"])),
               a.json)
        return 0
    b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv)
    htf = (a.htf or HTF.get(a.tf.upper()) or "none").upper()
    hb = None
    if htf != "NONE" and (a.htf_csv or not a.csv):
        try:
            hb = T.get_bars(a.symbol, htf, 300, a.source, a.htf_csv)
        except SystemExit:
            hb = None
    T.emit(analyze(b, hb, clock), a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
