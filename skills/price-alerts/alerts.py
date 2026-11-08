#!/usr/bin/env python3
"""Price Alerts: rings once when a price level, a % move or a session open hits.

    python3 alerts.py add EURUSD above 1.0900 --note "breakout"
    python3 alerts.py add EURUSD below 1.0800            # e.g. a mental stop
    python3 alerts.py add GBPJPY cross 193.00            # either direction
    python3 alerts.py add XAUUSD pct 1                   # +/-1% from the previous daily close
    python3 alerts.py add EURUSD session london          # London open (tokyo, sydney, newyork, overlap)
    python3 alerts.py list | remove ID | rearm ID | clear
    python3 alerts.py check                              # fetch prices once, ring what hit
    python3 alerts.py watch --every 60                   # keep checking while forex is open
    python3 alerts.py clock                              # forex open/closed, sessions

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

KINDS = ("above", "below", "cross", "pct", "session")
SESSION_NAMES = {"london": "London", "newyork": "New York", "new-york": "New York", "ny": "New York",
                 "tokyo": "Tokyo", "sydney": "Sydney", "overlap": "overlap"}


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
        if al["kind"] == "session":
            ses = clock.get("sessions", [])
            want = al.get("session")
            on = ("London" in ses and "New York" in ses) if want == "overlap" else want in ses
            if clock["open"] and on and al.get("last_day") != today:
                al["last_day"] = today
                al["fired"] = clock["now_ny"]
                rings.append({"id": al["id"], "symbol": sym, "msg": "%s session is open (%s your time)%s" % (
                    "London/New York overlap" if want == "overlap" else want, clock.get("now_local", ""),
                    " | " + al["note"] if al.get("note") else "")})
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
        F = lambda x: T.px(x, sym)  # noqa: E731
        if al["kind"] == "above" and px >= al["level"]:
            hit, msg = True, "%s at %s is above %s" % (sym, F(px), F(al["level"]))
        elif al["kind"] == "below" and px <= al["level"]:
            hit, msg = True, "%s at %s is below %s" % (sym, F(px), F(al["level"]))
        elif al["kind"] == "cross":
            last = al.get("last_price")
            if last is not None and (last - al["level"]) * (px - al["level"]) <= 0 and last != px:
                hit, msg = True, "%s crossed %s (%s -> %s)" % (sym, F(al["level"]), F(last), F(px))
            al["last_price"] = px
        elif al["kind"] == "pct" and q.get("prev_close"):
            ch = (px / float(q["prev_close"]) - 1) * 100
            if abs(ch) >= al["level"] and al.get("last_day") != today:
                al["last_day"] = today
                hit, msg = True, "%s moved %+.2f%% (%+.0f pips) from the previous close (%s -> %s)" % (
                    sym, ch, T.pips(px - float(q["prev_close"]), sym), F(float(q["prev_close"])), F(px))
        if hit:
            al["fired"] = clock["now_ny"]
            al["fired_price"] = px
            if al.get("repeat") and al["kind"] in ("above", "below"):
                al["armed"] = False
            rings.append({"id": al["id"], "symbol": sym, "msg": msg + (" | " + al["note"] if al.get("note") else "")})
    return rings


def overnight_rings(cfg, st, now=None, positions=None):
    """No-overnight accounts: ring 30 and 10 minutes before the flat time, and inside the flat window."""
    ovn = T.overnight_status(cfg, now)
    if not ovn["active"]:
        return []
    if positions is None:
        try:
            positions = T.account_snapshot(cfg)["positions"]
        except SystemExit:
            positions = []
    if not positions:
        return []
    day = T.last_rollover(now).date().isoformat()
    left = ovn.get("minutes_to_flat", 9999)
    marks = st.setdefault("overnight_marks", {})
    out = []
    for mark, cond in (("30", 10 < left <= 30), ("10", 0 < left <= 10), ("now", ovn["must_close"])):
        key = "%s-%s" % (day, mark)
        if cond and key not in marks:
            marks[key] = True
            what = "CLOSE ALL TRADES NOW" if mark == "now" else "%d minutes left to close all trades" % left
            out.append({"id": "flat", "symbol": "ALL", "msg": "%s (no overnight: rollover %s your time, %d open)" % (
                what, ovn["rollover_local"], len(positions))})
    return out


def check(cfg, quiet=False):
    st = load()
    clock = T.market_clock(cfg=cfg)
    syms = sorted({a["symbol"] for a in st["alerts"] if a["kind"] != "session" and (not a.get("fired") or a.get("repeat"))})
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
    rings += overnight_rings(cfg, st)
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
    ad.add_argument("level", nargs="?", help="price, percent, or a session name")
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
        if a.level is None:
            T.die("%s needs a %s" % (a.kind, "session name" if a.kind == "session" else "level"))
        sym = T.norm_symbol(a.symbol)
        al = {"id": st["next_id"], "symbol": sym, "kind": a.kind, "level": None, "note": a.note,
              "repeat": a.repeat, "created": T.ny_now().isoformat(timespec="seconds"), "fired": None}
        if a.kind == "session":
            key = a.level.lower().replace(" ", "")
            if key not in SESSION_NAMES:
                T.die("session must be one of: london, newyork, tokyo, sydney, overlap")
            al["session"] = SESSION_NAMES[key]
        else:
            al["level"] = float(a.level)
        st["alerts"].append(al)
        st["next_id"] += 1
        save(st)
        T.emit(dict(alert=al, text="alert #%d: %s %s %s%s" % (al["id"], al["symbol"], a.kind,
                                                               al.get("session") or T.px(al["level"], sym) if a.kind != "pct" else al["level"],
                                                               "%" if a.kind == "pct" else "")), a.json)
    elif a.cmd == "list":
        L = ["#%-3s %-7s %-7s %-9s %s%s" % (x["id"], x["symbol"], x["kind"], x.get("session") or ("" if x["level"] is None else x["level"]),
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
                clock = T.market_clock(cfg=cfg)
                if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
                    break
                if not clock["open"] and not a.max_minutes and clock.get("minutes_to_open", 0) > 60:
                    print("%s; stopping (next open %s)" % (clock["status"], clock.get("next_open")))
                    break
                time.sleep(every)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "price":
        out = {T.norm_symbol(s): T.get_price(s, cfg) for s in a.symbols}
        allp = T.load_json(T.tpath("prices.json"), {}) or {}
        allp.update(out)
        T.save_json(T.tpath("prices.json"), allp)
        T.emit({"prices": out, "text": "\n".join("%s %s (prev close %s%s, %s)" % (
            k, T.px(v["price"], k), T.px(v.get("prev_close"), k),
            ", spread %s pips" % v["spread_pips"] if v.get("spread_pips") is not None else "", v["source"])
            for k, v in out.items())}, a.json)
    else:
        c = T.market_clock(cfg=cfg)
        txt = "%s | your time %s | %s" % (c["status"], c["now_local"],
                                         ("closes %s" % c["closes_at"]) if c["open"] else ("opens %s" % c["next_open"]))
        txt = "\n".join([txt] + c.get("warnings", []))
        T.emit(dict(c, text=txt), a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
