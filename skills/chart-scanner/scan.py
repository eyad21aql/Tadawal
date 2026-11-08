#!/usr/bin/env python3
"""Chart Scanner: reads the chart of a currency pair and lists what is on it.

    python3 scan.py EURUSD                   # H1, last 300 bars
    python3 scan.py GBPJPY --tf H4 --svg trader/charts/gbpjpy.svg
    python3 scan.py XAUUSD --tf D1 --csv xauusd_d1.csv --json

Finds: trend structure (HH/HL), support/resistance, candlestick patterns on the
last bars, double tops/bottoms, head and shoulders (and inverse), triangles,
wedges, channels and ranges, level breakouts (with tick volume when the data
has it), RSI divergences and weekend gaps. Each finding says bullish/bearish, forming or confirmed, the key level
and a measured-move target where one exists.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402


def P(x):
    return T.px(x)


def _f(kind, name, bias, idx, b, status="", level=None, target=None, conf="medium", note=""):
    return {"kind": kind, "name": name, "bias": bias, "bar": b.date(idx), "index": idx,
            "status": status, "level": T.r(level), "target": T.r(target), "confidence": conf, "note": note}


# ---------------------------------------------------------------- candles

def candles(b, look=5):
    out = []
    n = len(b)
    bodies = [abs(b.c[i] - b.o[i]) for i in range(n)]
    e20 = T.ema(b.c, 20)
    for i in range(max(3, n - look), n):
        o, h, l, c = b.o[i], b.h[i], b.l[i], b.c[i]
        rng = max(h - l, 1e-9)
        body = bodies[i]
        upper, lower = h - max(o, c), min(o, c) - l
        avgb = sum(bodies[max(0, i - 10):i]) / max(1, min(10, i)) or 1e-9
        prior = b.c[i - 1] - b.c[max(0, i - 8)]
        down_before = prior < 0 or (e20[i] is not None and c < e20[i])
        up_before = prior > 0 or (e20[i] is not None and c > e20[i])
        if body <= 0.1 * rng:
            out.append(_f("candle", "doji", "neutral", i, b, note="indecision; watch the next bar"))
        if lower >= 2 * body and upper <= max(0.3 * body, 0.1 * rng) and body > 0.02 * rng:
            if down_before:
                out.append(_f("candle", "hammer", "bullish", i, b, level=l, note="rejection of lower prices"))
            elif up_before:
                out.append(_f("candle", "hanging man", "bearish", i, b, level=l, conf="low"))
        if upper >= 2 * body and lower <= max(0.3 * body, 0.1 * rng) and body > 0.02 * rng:
            if up_before:
                out.append(_f("candle", "shooting star", "bearish", i, b, level=h, note="rejection of higher prices"))
            elif down_before:
                out.append(_f("candle", "inverted hammer", "bullish", i, b, level=h, conf="low"))
        po, pc = b.o[i - 1], b.c[i - 1]
        if pc < po and c > o and o <= pc and c >= po and body > bodies[i - 1]:
            out.append(_f("candle", "bullish engulfing", "bullish", i, b, level=l,
                          conf="high" if down_before else "medium"))
        if pc > po and c < o and o >= pc and c <= po and body > bodies[i - 1]:
            out.append(_f("candle", "bearish engulfing", "bearish", i, b, level=h,
                          conf="high" if up_before else "medium"))
        o2, c2 = b.o[i - 2], b.c[i - 2]
        if c2 < o2 and abs(c2 - o2) > avgb and bodies[i - 1] < 0.5 * avgb and c > o and c > (o2 + c2) / 2:
            out.append(_f("candle", "morning star", "bullish", i, b, level=min(b.l[i - 2:i + 1]), conf="high"))
        if c2 > o2 and abs(c2 - o2) > avgb and bodies[i - 1] < 0.5 * avgb and c < o and c < (o2 + c2) / 2:
            out.append(_f("candle", "evening star", "bearish", i, b, level=max(b.h[i - 2:i + 1]), conf="high"))
        three = range(i - 2, i + 1)
        if all(b.c[k] > b.o[k] and bodies[k] >= 0.6 * avgb and b.h[k] - b.c[k] <= 0.3 * bodies[k] for k in three) \
                and b.c[i] > b.c[i - 1] > b.c[i - 2]:
            out.append(_f("candle", "three white soldiers", "bullish", i, b))
        if all(b.c[k] < b.o[k] and bodies[k] >= 0.6 * avgb and b.c[k] - b.l[k] <= 0.3 * bodies[k] for k in three) \
                and b.c[i] < b.c[i - 1] < b.c[i - 2]:
            out.append(_f("candle", "three black crows", "bearish", i, b))
        if h < b.h[i - 1] and l > b.l[i - 1]:
            out.append(_f("candle", "inside bar", "neutral", i, b, level=b.h[i - 1],
                          note="breakout of %s / %s sets the direction" % (P(b.h[i - 1]), P(b.l[i - 1]))))
        if body >= 2 * avgb and upper + lower <= 0.2 * rng:
            out.append(_f("candle", "wide-range %s bar" % ("bull" if c > o else "bear"),
                          "bullish" if c > o else "bearish", i, b, note="strong momentum"))
    return out


# ---------------------------------------------------------------- structure

def structure(piv):
    hs = [p for p in piv if p[1].startswith("H")][-2:]
    ls = [p for p in piv if p[1].startswith("L")][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return "not enough swings"
    hh, hl = hs[1][2] > hs[0][2], ls[1][2] > ls[0][2]
    if hh and hl:
        return "uptrend (higher highs, higher lows)"
    if not hh and not hl:
        return "downtrend (lower highs, lower lows)"
    if hh and not hl:
        return "expanding (higher high, lower low)"
    return "contracting (lower high, higher low)"


def _line(p1, p2):
    (i1, v1), (i2, v2) = p1, p2
    s = (v2 - v1) / (i2 - i1) if i2 != i1 else 0.0
    return lambda i: v1 + s * (i - i1)


def patterns(b, a, piv):
    out = []
    n = len(b)
    px = b.c[-1]
    tol = 0.75 * a
    recent = n - max(40, n // 4)

    for k in range(len(piv) - 3, -1, -1):
        p1, p2, p3 = piv[k], piv[k + 1], piv[k + 2]
        if p3[0] < recent:
            break
        t1, t2, t3 = p1[1][0], p2[1][0], p3[1][0]
        if t1 == "H" and t2 == "L" and t3 == "H" and abs(p1[2] - p3[2]) <= tol and min(p1[2], p3[2]) - p2[2] >= 2 * tol:
            neck = p2[2]
            top = max(p1[2], p3[2])
            if px > top + tol:
                continue  # invalidated: price is back above both tops
            st = "confirmed (closed below neckline)" if px < neck else "forming"
            out.append(_f("pattern", "double top", "bearish", p3[0], b, st, neck, neck - (top - neck),
                          "high" if px < neck else "medium", "neckline %s" % P(neck)))
            break
        if t1 == "L" and t2 == "H" and t3 == "L" and abs(p1[2] - p3[2]) <= tol and p2[2] - max(p1[2], p3[2]) >= 2 * tol:
            neck = p2[2]
            bot = min(p1[2], p3[2])
            if px < bot - tol:
                continue  # invalidated: price is back below both bottoms
            st = "confirmed (closed above neckline)" if px > neck else "forming"
            out.append(_f("pattern", "double bottom", "bullish", p3[0], b, st, neck, neck + (neck - bot),
                          "high" if px > neck else "medium", "neckline %s" % P(neck)))
            break

    for k in range(len(piv) - 5, -1, -1):
        s = piv[k:k + 5]
        if s[-1][0] < recent:
            break
        kinds = "".join(p[1][0] for p in s)
        if kinds == "HLHLH":
            l1, h2, l2, h3 = s[1], s[2], s[3], s[4]
            h1 = s[0]
            if h2[2] > h1[2] + tol and h2[2] > h3[2] + tol and abs(h1[2] - h3[2]) <= 1.5 * tol \
                    and px <= h2[2]:
                neck = _line((l1[0], l1[2]), (l2[0], l2[2]))
                nn = neck(n - 1)
                st = "confirmed (closed below neckline)" if px < nn else "forming"
                out.append(_f("pattern", "head and shoulders", "bearish", h3[0], b, st, nn,
                              nn - (h2[2] - neck(h2[0])), "high" if px < nn else "medium",
                              "head %s, neckline now %s" % (P(h2[2]), P(nn))))
                break
        if kinds == "LHLHL":
            l1, h1, l2, h2, l3 = s
            if l2[2] < l1[2] - tol and l2[2] < l3[2] - tol and abs(l1[2] - l3[2]) <= 1.5 * tol \
                    and px >= l2[2]:
                neck = _line((h1[0], h1[2]), (h2[0], h2[2]))
                nn = neck(n - 1)
                st = "confirmed (closed above neckline)" if px > nn else "forming"
                out.append(_f("pattern", "inverse head and shoulders", "bullish", l3[0], b, st, nn,
                              nn + (neck(l2[0]) - l2[2]), "high" if px > nn else "medium",
                              "head %s, neckline now %s" % (P(l2[2]), P(nn))))
                break

    tail = [p for p in piv if p[0] >= max(5, n - 120) and not p[1].endswith("*")][-5:]
    hs = [p for p in tail if p[1][0] == "H"]
    ls = [p for p in tail if p[1][0] == "L"]
    if len(hs) >= 2 and len(ls) >= 2:
        sh = T.slope_points([(p[0], p[2]) for p in hs])
        sl = T.slope_points([(p[0], p[2]) for p in ls])
        nh, nl = sh / a, sl / a
        flat = 0.02
        up_line = lambda i: hs[-1][2] + sh * (i - hs[-1][0])  # noqa: E731
        lo_line = lambda i: ls[-1][2] + sl * (i - ls[-1][0])  # noqa: E731
        u, d = up_line(n - 1), lo_line(n - 1)
        name = bias = None
        if abs(nh) < flat and nl > flat:
            name, bias = "ascending triangle", "bullish"
        elif nh < -flat and abs(nl) < flat:
            name, bias = "descending triangle", "bearish"
        elif nh < -flat and nl > flat:
            name, bias = "symmetrical triangle", "neutral"
        elif nh > flat and nl > flat and nl > nh * 1.3:
            name, bias = "rising wedge", "bearish"
        elif nh < -flat and nl < -flat and nh < nl * 1.3:
            name, bias = "falling wedge", "bullish"
        elif nh > flat and nl > flat:
            name, bias = "rising channel", "bullish"
        elif nh < -flat and nl < -flat:
            name, bias = "falling channel", "bearish"
        elif abs(nh) < flat and abs(nl) < flat:
            name, bias = "range (rectangle)", "neutral"
        if name and u > d:
            height = max(p[2] for p in hs) - min(p[2] for p in ls)
            if px > u + 0.1 * a:
                st, tgt = "broke out upward", u + height
            elif px < d - 0.1 * a:
                st, tgt = "broke down", d - height
            else:
                st, tgt = "inside, between %s and %s" % (P(d), P(u)), None
            out.append(_f("pattern", name, bias, n - 1, b, st, u if px >= (u + d) / 2 else d, tgt,
                          "medium", "upper line %s, lower line %s" % (P(u), P(d))))
    return out


def divergences(b):
    out = []
    rs = T.rsi(b.c, 14)
    piv = [p for p in T.pivots(b, 3, 3) if p[0] >= len(b) - 60 and rs[p[0]] is not None]
    hs = [p for p in piv if p[1] == "H"][-2:]
    ls = [p for p in piv if p[1] == "L"][-2:]
    if len(hs) == 2 and hs[1][2] > hs[0][2] and rs[hs[1][0]] < rs[hs[0][0]] - 2:
        out.append(_f("divergence", "bearish RSI divergence", "bearish", hs[1][0], b, level=hs[1][2],
                      note="higher high in price (%s>%s), lower high in RSI (%.0f<%.0f)" %
                      (P(hs[1][2]), P(hs[0][2]), rs[hs[1][0]], rs[hs[0][0]])))
    if len(ls) == 2 and ls[1][2] < ls[0][2] and rs[ls[1][0]] > rs[ls[0][0]] + 2:
        out.append(_f("divergence", "bullish RSI divergence", "bullish", ls[1][0], b, level=ls[1][2],
                      note="lower low in price (%s<%s), higher low in RSI (%.0f>%.0f)" %
                      (P(ls[1][2]), P(ls[0][2]), rs[ls[1][0]], rs[ls[0][0]])))
    return out


def breakouts(b, lv, a):
    out = []
    n = len(b)
    vavg = T.sma(b.v, 20)
    vol = b.has_volume()
    for i in range(max(1, n - 3), n):
        for L in lv:
            p = L["price"]
            if vol:
                volx = b.v[i] / vavg[i] if vavg[i] else 0
                st, conf = "tick volume %.1fx avg" % volx, ("high" if volx >= 1.5 else "low")
                weak = volx < 1.5
            else:
                st, conf, weak = "no volume data", "medium", False
            if b.c[i - 1] <= p < b.c[i] and b.c[i] - p >= 0.1 * a:
                out.append(_f("breakout", "broke above %s" % P(p), "bullish", i, b, st, p, conf=conf,
                              note="weak volume: could be a false breakout" if weak else "wait for a retest of the level"))
            if b.c[i - 1] >= p > b.c[i] and p - b.c[i] >= 0.1 * a:
                out.append(_f("breakout", "broke below %s" % P(p), "bearish", i, b, st, p, conf=conf,
                              note="weak volume: could be a false breakdown" if weak else "wait for a retest of the level"))
    return out


def gaps(b, look=10):
    out = []
    n = len(b)
    for i in range(max(1, n - look), n):
        if b.l[i] > b.h[i - 1]:
            filled = min(b.l[i:]) <= b.h[i - 1]
            out.append(_f("gap", "gap up", "bullish", i, b, "filled" if filled else "open", b.h[i - 1],
                          note="gap zone %s-%s" % (P(b.h[i - 1]), P(b.l[i]))))
        elif b.h[i] < b.l[i - 1]:
            filled = max(b.h[i:]) >= b.l[i - 1]
            out.append(_f("gap", "gap down", "bearish", i, b, "filled" if filled else "open", b.l[i - 1],
                          note="gap zone %s-%s" % (P(b.h[i]), P(b.l[i - 1]))))
    return out


def scan(b, zz_atr=2.0, candle_look=5):
    T.set_symbol(b.symbol)
    if len(b) < 60:
        T.die("need at least 60 bars, got %d" % len(b))
    a = T.atr(b, 14)[-1]
    piv = T.zigzag(b, zz_atr * a, include_last=True)
    lv = T.levels(b, a)
    found = candles(b, candle_look) + patterns(b, a, piv) + divergences(b) + breakouts(b, lv, a) + gaps(b)
    bull = sum(1 for f in found if f["bias"] == "bullish")
    bear = sum(1 for f in found if f["bias"] == "bearish")
    out = {"symbol": b.symbol, "tf": b.tf, "source": b.source, "bar": b.date(len(b) - 1),
           "price": T.r(b.c[-1]), "atr14": T.r(a), "structure": structure(piv),
           "swings": [{"bar": b.date(p[0]), "type": p[1], "price": T.r(p[2])} for p in piv[-8:]],
           "levels": lv, "findings": found, "bullish": bull, "bearish": bear}
    out["text"] = render(out)
    return out


def render(o):
    L = ["CHART SCANNER  %s %s  (%s, last bar %s)" % (o["symbol"], o["tf"], o["source"], o["bar"]),
         "price %s | ATR %s (%s pips) | structure: %s" % (P(o["price"]), P(o["atr14"]), T.pips(o["atr14"], o["symbol"]), o["structure"]),
         "levels: " + ", ".join("%s(x%d)" % (P(x["price"]), x["touches"]) for x in o["levels"]),
         "swings: " + " ".join("%s %s@%s" % (s["bar"], s["type"], P(s["price"])) for s in o["swings"]), ""]
    if not o["findings"]:
        L.append("no patterns on the recent bars")
    for f in o["findings"]:
        bits = ["[%s] %s %s" % (f["bias"][:4].upper(), f["bar"], f["name"])]
        if f["status"]:
            bits.append(f["status"])
        if f["level"] is not None:
            bits.append("level %s" % P(f["level"]))
        if f["target"] is not None:
            bits.append("target %s (%+.0f pips)" % (P(f["target"]), T.pips(f["target"] - o["price"], o["symbol"])))
        bits.append("conf %s" % f["confidence"])
        if f["note"]:
            bits.append(f["note"])
        L.append(" | ".join(bits))
    L.append("")
    L.append("tally: %d bullish, %d bearish signals" % (o["bullish"], o["bearish"]))
    return "\n".join(L)


def svg(b, o, path, last=150):
    """Plain SVG candlestick chart with levels and finding markers."""
    s = b.slice(-last)
    off = len(b) - len(s)
    W, H, pad = 1100, 520, 50
    lo, hi = min(s.l), max(s.h)
    lv = [x for x in o["levels"] if lo * 0.97 <= x["price"] <= hi * 1.03]
    lo = min([lo] + [x["price"] for x in lv])
    hi = max([hi] + [x["price"] for x in lv])
    y = lambda p: pad + (hi - p) / ((hi - lo) or 1) * (H - 2 * pad)  # noqa: E731
    cw = (W - 2 * pad) / len(s)
    x = lambda i: pad + (i + 0.5) * cw  # noqa: E731
    e = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" font-family="sans-serif" font-size="11">' % (W, H),
         '<rect width="100%" height="100%" fill="#fff"/>',
         '<text x="%d" y="20" font-size="14" font-weight="bold">%s %s  %s</text>' % (pad, o["symbol"], o["tf"], o["bar"])]
    for L in lv:
        e.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="#888" stroke-dasharray="4 3"/>'
                 % (pad, W - pad, y(L["price"]), y(L["price"])))
        e.append('<text x="%d" y="%.1f" fill="#555">%s</text>' % (W - pad + 3, y(L["price"]) + 4, P(L["price"])))
    e20 = T.ema(b.c, 20)[off:]
    pts = " ".join("%.1f,%.1f" % (x(i), y(v)) for i, v in enumerate(e20) if v is not None)
    e.append('<polyline points="%s" fill="none" stroke="#2b6cb0" stroke-width="1.2"/>' % pts)
    for i in range(len(s)):
        up = s.c[i] >= s.o[i]
        col = "#16a34a" if up else "#dc2626"
        e.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s"/>' % (x(i), x(i), y(s.h[i]), y(s.l[i]), col))
        top, bot = y(max(s.o[i], s.c[i])), y(min(s.o[i], s.c[i]))
        e.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                 % (x(i) - cw * 0.35, top, cw * 0.7, max(bot - top, 0.8), col))
    for f in o["findings"]:
        i = f["index"] - off
        if 0 <= i < len(s):
            bull = f["bias"] == "bullish"
            yy = y(s.l[i]) + 14 if bull else y(s.h[i]) - 6
            e.append('<text x="%.1f" y="%.1f" text-anchor="middle" fill="%s">%s %s</text>' % (
                x(i), yy, "#16a34a" if bull else ("#dc2626" if f["bias"] == "bearish" else "#555"),
                "▲" if bull else ("▼" if f["bias"] == "bearish" else "•"), f["name"]))
    e.append("</svg>")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(e))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    T.add_data_args(ap, "H1", 300)
    ap.add_argument("--swing-atr", type=float, default=2.0, help="zigzag swing size in ATRs (default 2)")
    ap.add_argument("--candles", type=int, default=5, help="how many recent bars to check for candle patterns")
    ap.add_argument("--svg", help="also draw the chart to this .svg file")
    a = ap.parse_args(argv)
    b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv)
    o = scan(b, a.swing_atr, a.candles)
    if a.svg:
        o["svg"] = svg(b, o, a.svg)
        o["text"] += "\nchart: " + o["svg"]
    if a.json:
        for f in o["findings"]:
            f.pop("index", None)
    T.emit(o, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
