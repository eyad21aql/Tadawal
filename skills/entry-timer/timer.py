#!/usr/bin/env python3
"""Entry Timer: is now a good moment to enter a currency pair, and if not, what to wait for.

    python3 timer.py EURUSD                 # H1 bars, D1 as higher timeframe
    python3 timer.py GBPJPY --tf M15        # 15 minutes, H1 as higher timeframe
    python3 timer.py XAUUSD --tf H4
    python3 timer.py EURUSD --csv eurusd_h1.csv --htf-csv eurusd_d1.csv   # bars exported from MT5
    python3 timer.py --clock                # forex open/closed, active sessions

Checklist scoring, not a prediction. Prints a buy and a sell score, the
missing conditions, a pullback plan and a breakout plan (with stop distance in
pips), the trading session, and high-impact news coming up for the pair's
currencies (from economic-analyst's calendar, if it has been fetched).
"""
import argparse
import datetime as dt
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
    s50 = e50[i] - e50[i - 5] if e50[i - 5] is not None else 0
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


def upcoming_news(symbol, hours=4, now=None):
    """High-impact events for the pair's currencies in the next `hours`, from trader/calendar.json."""
    cal = T.load_json(T.tpath("calendar.json"), None)
    if not cal:
        return None
    base, quote = T.split_pair(symbol)
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for e in cal.get("events", []):
        if e.get("impact") != "High" or e.get("currency") not in (base, quote):
            continue
        try:
            t = dt.datetime.fromisoformat(e["time"])
        except (KeyError, ValueError):
            continue
        mins = (t - now).total_seconds() / 60
        if -30 <= mins <= hours * 60:
            out.append({"currency": e["currency"], "title": e["title"], "in_minutes": int(mins)})
    return out


def analyze(b, htf_bars=None, clock=None, news=None):
    if len(b) < 60:
        T.die("need at least 60 bars, got %d" % len(b))
    T.set_symbol(b.symbol)
    P = lambda x: T.px(x, b.symbol)  # noqa: E731
    c, i = b.c, len(b) - 1
    e20, e50, e200 = T.ema(c, 20), T.ema(c, 50), T.ema(c, 200)
    rs = T.rsi(c, 14)
    _, _, hist = T.macd(c)
    a = T.atr(b, 14)[i]
    vol = b.has_volume()
    vavg = T.sma(b.v, 20)[i] or 0
    price = c[i]
    trend = trend_of(c, e50, e200)
    htrend = None
    if htf_bars is not None and len(htf_bars) >= 60:
        htrend = trend_of(htf_bars.c, T.ema(htf_bars.c, 50), T.ema(htf_bars.c, 200))
    lv = T.levels(b, a)
    sup = max((x for x in lv if x["price"] < price), key=lambda x: x["price"], default=None)
    res = min((x for x in lv if x["price"] > price), key=lambda x: x["price"], default=None)
    ok = lambda k: hist[k] is not None and hist[k - 1] is not None  # noqa: E731
    hist_up = ok(i) and hist[i] > hist[i - 1]
    hist_dn = ok(i) and hist[i] < hist[i - 1]
    cross_up = any(ok(k) and hist[k - 1] <= 0 < hist[k] for k in range(i - 2, i + 1))
    cross_dn = any(ok(k) and hist[k - 1] >= 0 > hist[k] for k in range(i - 2, i + 1))
    ext = (price - e20[i]) / a if a else 0
    broke_up = res is None and price >= max(b.h[-21:-1])
    broke_dn = sup is None and price <= min(b.l[-21:-1])
    atr_pips = T.pips(a, b.symbol)

    def side_score(long):
        sc, met, miss = 0.0, [], []

        def chk(cond, pts, txt_ok, txt_miss):
            nonlocal sc
            if cond:
                sc += pts
                met.append(txt_ok)
            elif txt_miss:
                miss.append(txt_miss)

        want = "up" if long else "down"
        chk(trend == want, 2, "trend is %s (price vs EMA50/EMA200)" % want, "trend is %s, not %s" % (trend, want))
        if htrend is not None:
            chk(htrend == want, 1, "higher timeframe agrees (%s)" % htrend, "higher timeframe is %s" % htrend)
        lvl = sup if long else res
        near_ma = abs(price - e20[i]) <= a or (e50[i] is not None and abs(price - e50[i]) <= a)
        near_lvl = lvl is not None and abs(price - lvl["price"]) <= a
        chk(near_ma or near_lvl, 1, "price is at value (within 1 ATR of EMA20/50 or a level)",
            "price is %.1f ATR %s EMA20 and not at a level: wait for price to come to value"
            % (abs(ext), "above" if ext > 0 else "below"))
        if long:
            chk(rs[i] is not None and (40 <= rs[i] <= 65 or (rs[i] < 40 and rs[i] > rs[i - 1])), 1,
                "RSI %.0f has room" % (rs[i] or 0), "RSI %.0f is not in the 40-65 zone" % (rs[i] or 0))
            chk(hist_up or cross_up, 1, "MACD momentum turning up", "MACD momentum still falling")
            room = (res["price"] - price) / a if res and a else None
            chk(room is None or room >= 2 or broke_up, 1,
                "room to resistance: %s" % ("open (new 20-bar high)" if room is None else "%.1f ATR" % room),
                ("resistance %s is only %.1f ATR (%.0f pips) away" % (
                    P(res["price"]), room, T.pips(res["price"] - price, b.symbol))) if res else "")
            if vol:
                chk(vavg and b.v[i] >= vavg and c[i] > b.o[i], 0.5, "tick volume above average on an up bar",
                    "no volume confirmation")
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
            room = (price - sup["price"]) / a if sup and a else None
            chk(room is None or room >= 2 or broke_dn, 1,
                "room to support: %s" % ("open (new 20-bar low)" if room is None else "%.1f ATR" % room),
                ("support %s is only %.1f ATR (%.0f pips) away" % (
                    P(sup["price"]), room, T.pips(price - sup["price"], b.symbol))) if sup else "")
            if vol:
                chk(vavg and b.v[i] >= vavg and c[i] < b.o[i], 0.5, "tick volume above average on a down bar",
                    "no volume confirmation")
            if rs[i] is not None and rs[i] < 25:
                sc -= 1
                miss.append("RSI %.0f oversold: chasing" % rs[i])
            if ext < -3:
                sc -= 1
                miss.append("price %.1f ATR below EMA20: extended" % -ext)
        top = 6.0 + (1 if htrend is not None else 0) + (0.5 if vol else 0)
        verdict = "ENTRY ZONE" if sc >= top - 2 else ("WAIT" if sc >= top - 4 else "NO TRADE")
        return {"score": round(sc, 1), "of": top, "verdict": verdict, "met": met, "missing": miss}

    buy, sell = side_score(True), side_score(False)

    def plan(long):
        lvl_obj = sup if long else res
        sgn = 1 if long else -1
        cands = [x for x in ([lvl_obj["price"]] if lvl_obj else []) + [e20[i]] if (price - x) * sgn > 0]
        lvl = (max(cands) if long else min(cands)) if cands else price - sgn * a
        stop = (lvl_obj["price"] if lvl_obj else lvl) - sgn * 0.5 * a
        if (lvl - stop) * sgn < 0.3 * a:
            stop = lvl - sgn * 1.5 * a
        brk = (res["price"] if res else max(b.h[-21:])) if long else (sup["price"] if sup else min(b.l[-21:]))
        return {"pullback": {"entry_limit": T.r(lvl), "stop_ref": T.r(stop),
                             "stop_pips": T.pips(abs(lvl - stop), b.symbol)},
                "breakout": {"entry_stop": T.r(brk + sgn * 0.1 * a), "stop_ref": T.r(brk - sgn * 1.5 * a),
                             "stop_pips": T.pips(1.6 * a, b.symbol), "note": "only on a candle close beyond the level"}}

    timing = []
    if clock:
        if not clock["open"]:
            timing.append("%s; opens %s (your time)" % (clock["status"], clock.get("next_open")))
        else:
            timing.append(clock["status"])
            timing += clock.get("warnings", [])
            sn = T.session_note(b.symbol, clock)
            if sn:
                timing.append(sn)
    if news:
        for n in news:
            when = "in %d min" % n["in_minutes"] if n["in_minutes"] >= 0 else "%d min ago" % -n["in_minutes"]
            timing.append("HIGH-IMPACT NEWS %s: %s %s: wait until it is out and the spike settles" % (
                when, n["currency"], n["title"]))
    elif news is None:
        timing.append("news not checked: run economic-analyst calendar first")
    if b.tf in ("D1", "W1"):
        timing.append("daily signals are read on the candle close (17:00 New York)")

    out = {"symbol": b.symbol, "tf": b.tf, "source": b.source, "bar": b.date(i), "price": T.r(price),
           "atr14": T.r(a), "atr_pips": atr_pips, "rsi14": T.r(rs[i], 1), "ema20": T.r(e20[i]),
           "ema50": T.r(e50[i]), "ema200": T.r(e200[i]), "trend": trend, "higher_tf_trend": htrend,
           "support": sup, "resistance": res, "levels": lv, "buy": buy, "sell": sell,
           "buy_plan": plan(True), "sell_plan": plan(False), "timing": timing}
    out["text"] = render(out)
    return out


def render(o):
    s = o["symbol"]
    P = lambda x: T.px(x, s)  # noqa: E731
    L = ["ENTRY TIMER  %s %s  (%s, bar %s UTC)" % (s, o["tf"], o["source"], o["bar"]),
         "price %s | ATR %s (%s pips) | RSI %s | EMA20 %s EMA50 %s EMA200 %s" % (
             P(o["price"]), P(o["atr14"]), o["atr_pips"], o["rsi14"], P(o["ema20"]), P(o["ema50"]), P(o["ema200"])),
         "trend %s | higher TF %s | support %s | resistance %s" % (
             o["trend"], o["higher_tf_trend"] or "n/a", P(o["support"]["price"]) if o["support"] else "none below",
             P(o["resistance"]["price"]) if o["resistance"] else "none above")]
    for side in ("buy", "sell"):
        x = o[side]
        L += ["", "%s: %s  (score %s / %s)" % (side.upper(), x["verdict"], x["score"], x["of"])]
        L += ["  + " + m for m in x["met"]] + ["  - " + m for m in x["missing"]]
    best = "buy" if o["buy"]["score"] >= o["sell"]["score"] else "sell"
    p = o[best + "_plan"]
    L += ["", "%s plans:" % best.upper(),
          "  pullback: %s limit %s, stop %s (%s pips)" % (
              best, P(p["pullback"]["entry_limit"]), P(p["pullback"]["stop_ref"]), p["pullback"]["stop_pips"]),
          "  breakout: %s stop %s, stop %s (%s pips, %s)" % (
              best, P(p["breakout"]["entry_stop"]), P(p["breakout"]["stop_ref"]), p["breakout"]["stop_pips"],
              p["breakout"]["note"])]
    L += ["timing: " + t for t in o["timing"]]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?")
    T.add_data_args(ap, "H1", 400)
    ap.add_argument("--htf", help="higher timeframe (default: one step up); 'none' to skip")
    ap.add_argument("--htf-csv", help="CSV for the higher timeframe")
    ap.add_argument("--clock", action="store_true", help="only print forex open/closed and sessions")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    clock = T.market_clock(cfg=cfg)
    if a.clock or not a.symbol:
        txt = "%s | your time %s" % (clock["status"], clock["now_local"])
        txt += (" | closes %s" % clock["closes_at"]) if clock["open"] else (" | opens %s" % clock["next_open"])
        T.emit(dict(clock, text="\n".join([txt] + clock.get("warnings", []))), a.json)
        return 0
    b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv, cfg)
    htf = (a.htf or HTF.get(a.tf.upper()) or "none").upper()
    hb = None
    if htf != "NONE" and (a.htf_csv or not a.csv):
        try:
            hb = T.get_bars(a.symbol, htf, 300, a.source, a.htf_csv, cfg)
        except SystemExit:
            hb = None
    T.emit(analyze(b, hb, clock, upcoming_news(b.symbol)), a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
