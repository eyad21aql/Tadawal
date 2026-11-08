#!/usr/bin/env python3
"""Price Alerts: rings once when a price level, a % move or the opening bell hits.

    python3 alerts.py add AAPL above 200 --note "breakout"
    python3 alerts.py add AAPL below 180            # e.g. a mental stop
    python3 alerts.py add TSLA cross 250            # either direction
    python3 alerts.py add NVDA pct 5                # +/-5% from the previous close
    python3 alerts.py add SPY bell                  # NYSE opening bell, once a day
    python3 alerts.py list | remove ID | rearm ID | clear
    python3 alerts.py check                         # fetch prices once, ring what hit
    python3 alerts.py watch --every 60              # keep checking until the close
    python3 alerts.py clock                         # NYSE open or closed

Alerts live in trader/alerts.json; the latest prices in trader/prices.json.
Each alert rings once and is then marked fired (use --repeat to re-arm automatically).
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

KINDS = ("above", "below", "cross", "pct", "bell")


def load():
    return T.load_json(T.tpath("alerts.json"), {"alerts": [], "next_id": 1})


def save(st):
    T.save_json(T.tpath("alerts.json"), st)


def evaluate(alerts, prices, clock, today):
    """Mutates alerts; returns the list of rings. prices: {SYM: {price, prev_close}}."""
    rings = []
    for al in alerts:
        if al.get("fired") and not al.get("repeat"):
            continue
        sym = al["symbol"]
        if al["kind"] == "bell":
            if clock["open"] and al.get("last_day") != today:
                al["last_day"] = today
                al["fired"] = clock["now_ny"]
                rings.append({"id": al["id"], "symbol": sym, "msg": "opening bell: NYSE is open (%s)" % clock["now_ny"]})
            continue
        q = prices.get(sym)
        if not q:
            continue
        px = q["price"]
        if al.get("armed") is False:
            if (al["kind"] == "above" and px < al["level"]) or (al["kind"] == "below" and px > al["level"]):
                al["armed"] = True  # price went back across the level: ring again next time
            continue
        hit, msg = False, ""
        if al["kind"] == "above" and px >= al["level"]:
            hit, msg = True, "%s at %.2f is above %.2f" % (sym, px, al["level"])
        elif al["kind"] == "below" and px <= al["level"]:
            hit, msg = True, "%s at %.2f is below %.2f" % (sym, px, al["level"])
        elif al["kind"] == "cross":
            last = al.get("last_price")
            if last is not None and (last - al["level"]) * (px - al["level"]) <= 0 and last != px:
                hit, msg = True, "%s crossed %.2f (%.2f -> %.2f)" % (sym, al["level"], last, px)
            al["last_price"] = px
        elif al["kind"] == "pct" and q.get("prev_close"):
            ch = (px / float(q["prev_close"]) - 1) * 100
            if abs(ch) >= al["level"] and al.get("last_day") != today:
                al["last_day"] = today
                hit, msg = True, "%s moved %+.2f%% from the previous close (%.2f -> %.2f)" % (
                    sym, ch, float(q["prev_close"]), px)
        if hit:
            al["fired"] = clock["now_ny"]
            al["fired_price"] = px
            if al.get("repeat") and al["kind"] in ("above", "below"):
                al["armed"] = False
            rings.append({"id": al["id"], "symbol": sym, "msg": msg + (" | " + al["note"] if al.get("note") else "")})
    return rings


def check(cfg, quiet=False):
    st = load()
    clock = T.market_clock()
    syms = sorted({a["symbol"] for a in st["alerts"] if a["kind"] != "bell" and (not a.get("fired") or a.get("repeat"))})
    prices, errors = {}, []
    for s in syms:
        try:
            prices[s] = T.get_price(s, cfg)
        except SystemExit:
            errors.append(s)
        time.sleep(0.2)
    allp = T.load_json(T.tpath("prices.json"), {}) or {}
    allp.update(prices)
    T.save_json(T.tpath("prices.json"), allp)
    rings = evaluate(st["alerts"], prices, clock, T.ny_now().date().isoformat())
    save(st)
    L = []
    for r in rings:
        L.append("\a*** ALERT #%s  %s" % (r["id"], r["msg"]))
    if not quiet or rings:
        L.append("%s | checked %d symbols%s | %d alert(s) rang" % (
            clock["status"], len(syms), (", failed: " + ", ".join(errors)) if errors else "", len(rings)))
    return {"rings": rings, "prices": prices, "errors": errors, "text": "\n".join(L)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add")
    ad.add_argument("symbol")
    ad.add_argument("kind", choices=KINDS)
    ad.add_argument("level", type=float, nargs="?")
    ad.add_argument("--note", default="")
    ad.add_argument("--repeat", action="store_true")
    sub.add_parser("list")
    rm = sub.add_parser("remove")
    rm.add_argument("ids", nargs="+", type=int)
    ra = sub.add_parser("rearm")
    ra.add_argument("ids", nargs="+", type=int)
    sub.add_parser("clear", help="delete alerts that already fired")
    sub.add_parser("check")
    w = sub.add_parser("watch")
    w.add_argument("--every", type=int, default=60, help="seconds between checks (min 15)")
    w.add_argument("--max-minutes", type=int, default=0, help="stop after N minutes (0 = until the close)")
    sub.add_parser("clock")
    sub.add_parser("price").add_argument("symbols", nargs="+")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    st = load()

    if a.cmd == "add":
        if a.kind != "bell" and a.level is None:
            T.die("%s needs a level" % a.kind)
        al = {"id": st["next_id"], "symbol": a.symbol.upper(), "kind": a.kind, "level": a.level, "note": a.note,
              "repeat": a.repeat, "created": T.ny_now().isoformat(timespec="seconds"), "fired": None}
        st["alerts"].append(al)
        st["next_id"] += 1
        save(st)
        T.emit(dict(alert=al, text="alert #%d: %s %s %s%s" % (al["id"], al["symbol"], a.kind,
                                                               "" if a.level is None else a.level,
                                                               "%" if a.kind == "pct" else "")), a.json)
    elif a.cmd == "list":
        L = ["#%-3s %-6s %-6s %-8s %s%s" % (x["id"], x["symbol"], x["kind"], "" if x["level"] is None else x["level"],
                                           ("fired " + x["fired"]) if x.get("fired") else "armed",
                                           "  | " + x["note"] if x.get("note") else "") for x in st["alerts"]]
        T.emit({"alerts": st["alerts"], "text": "\n".join(L) or "no alerts"}, a.json)
    elif a.cmd in ("remove", "rearm"):
        if a.cmd == "remove":
            st["alerts"] = [x for x in st["alerts"] if x["id"] not in a.ids]
        else:
            for x in st["alerts"]:
                if x["id"] in a.ids:
                    x["fired"], x["last_day"], x["armed"] = None, None, True
        save(st)
        T.emit({"text": "%s: %s" % (a.cmd, a.ids)}, a.json)
    elif a.cmd == "clear":
        n = len(st["alerts"])
        st["alerts"] = [x for x in st["alerts"] if not x.get("fired") or x.get("repeat")]
        save(st)
        T.emit({"text": "removed %d fired alerts" % (n - len(st["alerts"]))}, a.json)
    elif a.cmd == "check":
        T.emit(check(cfg), a.json)
    elif a.cmd == "watch":
        every = max(15, a.every)
        t0 = time.time()
        print("watching every %ds (Ctrl+C to stop)" % every, flush=True)
        try:
            while True:
                r = check(cfg, quiet=True)
                if r["text"]:
                    print(r["text"], flush=True)
                clock = T.market_clock()
                if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
                    break
                if not clock["open"] and not a.max_minutes and clock.get("minutes_to_open", 0) > 60:
                    print("%s; stopping (next open %s)" % (clock["status"], clock.get("next_open")))
                    break
                time.sleep(every)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "price":
        out = {s.upper(): T.get_price(s, cfg) for s in a.symbols}
        allp = T.load_json(T.tpath("prices.json"), {}) or {}
        allp.update(out)
        T.save_json(T.tpath("prices.json"), allp)
        T.emit({"prices": out, "text": "\n".join("%s %.2f (prev close %s, %s)" % (
            k, v["price"], v.get("prev_close"), v["source"]) for k, v in out.items())}, a.json)
    else:
        c = T.market_clock()
        txt = c["status"] + (", closes %s" % c["closes_at"] if c["open"] else ", next open %s" % c["next_open"])
        T.emit(dict(c, text=txt), a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
