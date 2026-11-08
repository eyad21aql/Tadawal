#!/usr/bin/env python3
"""Exit Planner: where the stop goes, where to take profit, and how to trail.

    python3 exit.py plan AAPL --entry 190                     # stop from structure/ATR, targets, scale-out
    python3 exit.py plan AAPL --entry 190 --stop 184 --order o1  # also set sl/tp on order o1
    python3 exit.py trail AAPL --entry 190 --stop 184 --since 2026-10-01
    python3 exit.py review                                    # every open position vs its plan

Long by default; --side sell for shorts. Plans are saved to trader/exits.json.
A stop is only ever moved in the trade's favour, never loosened.
"""
import argparse
import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402


def plan(b, entry, side="buy", stop=None, min_rr=1.5, chand_k=3.0, time_stop=10, scale=(33, 33, 34)):
    a = T.atr(b, 14)[-1]
    long = side == "buy"
    sgn = 1 if long else -1
    how = "given"
    if stop is None:
        piv = [p for p in T.pivots(b, 3, 3) if p[0] >= len(b) - 60]
        if long:
            sw = [p[2] for p in piv if p[1] == "L" and p[2] < entry]
            ref = max(sw) if sw else None
        else:
            sw = [p[2] for p in piv if p[1] == "H" and p[2] > entry]
            ref = min(sw) if sw else None
        if ref is not None:
            stop = ref - sgn * 0.5 * a
            dist = abs(entry - stop)
            if dist < a:
                stop, how = entry - sgn * a, "swing too close: 1 ATR"
            elif dist > 3 * a:
                stop, how = entry - sgn * 3 * a, "swing too far: capped at 3 ATR"
            else:
                how = "beyond last swing %s %.2f by 0.5 ATR" % ("low" if long else "high", ref)
        else:
            stop, how = entry - sgn * 2 * a, "2 ATR (no recent swing)"
    if (long and stop >= entry) or (not long and stop <= entry):
        T.die("stop must be %s entry" % ("below" if long else "above"))
    R = abs(entry - stop)
    lv = T.levels(b, a)
    struct = [x["price"] for x in lv if (x["price"] - entry) * sgn > 0.3 * a and abs(x["price"] - entry) <= 5 * R]
    struct.sort(key=lambda p: abs(p - entry))
    rm = [entry + sgn * k * R for k in (1, 2, 3)]
    tp_cands = [p - sgn * 0.1 * a for p in struct if abs(p - entry) >= min_rr * R]
    if tp_cands:
        tp, tp_why = tp_cands[0], "just in front of level %.2f" % (tp_cands[0] + sgn * 0.1 * a)
    else:
        tp, tp_why = entry + sgn * max(2.0, min_rr) * R, "%.1fR (no level in the way)" % max(2.0, min_rr)
    blockers = [p for p in struct if abs(p - entry) < min_rr * R]
    steps = [
        {"at": T.r(rm[0]), "r": 1, "action": "take %d%%, move stop to breakeven %.2f" % (scale[0], entry)},
        {"at": T.r(rm[1]), "r": 2, "action": "take %d%%, trail the rest" % scale[1]},
        {"at": "trail", "r": None, "action": "last %d%%: chandelier stop %.1f ATR from the %s since entry" % (
            scale[2], chand_k, "highest high" if long else "lowest low")},
    ]
    out = {"symbol": b.symbol, "tf": b.tf, "side": side, "entry": T.r(entry, 4), "stop": T.r(stop, 4),
           "stop_how": how, "atr14": T.r(a), "R": T.r(R, 4), "targets_r": [T.r(x) for x in rm],
           "structure_targets": [T.r(x) for x in struct[:4]], "tp_single": T.r(tp, 4), "tp_why": tp_why,
           "rr_single": T.r(abs(tp - entry) / R), "scale_out": steps, "chandelier_atr": chand_k,
           "time_stop_bars": time_stop,
           "warnings": ["level %.2f sits before %.1fR and may cap the move" % (p, min_rr) for p in blockers],
           "created": T.ny_now().isoformat(timespec="seconds")}
    L = ["EXIT PLAN  %s %s %s  entry %s" % (side.upper(), b.symbol, b.tf, out["entry"]),
         "stop %s  (%s)  1R = %s  ATR %s" % (out["stop"], how, out["R"], out["atr14"]),
         "single take-profit for the order: %s  (%s, R:R %s)" % (out["tp_single"], tp_why, out["rr_single"]),
         "R targets: 1R %s | 2R %s | 3R %s" % tuple(out["targets_r"]),
         "levels in the way: %s" % (", ".join(str(x) for x in out["structure_targets"]) or "none"),
         "scale-out plan:"]
    L += ["  %s -> %s" % (s["at"], s["action"]) for s in steps]
    L.append("time stop: if 1R is not reached within %d bars, reassess or exit" % time_stop)
    L += ["warning: " + w for w in out["warnings"]]
    out["text"] = "\n".join(L)
    return out


def trail(b, entry, stop, side="buy", since_index=0, chand_k=3.0, time_stop=10):
    long = side == "buy"
    sgn = 1 if long else -1
    a = T.atr(b, 14)[-1]
    R = abs(entry - stop)
    seg = b.slice(since_index)
    px = b.c[-1]
    extreme = max(seg.h) if long else min(seg.l)
    best_r = (extreme - entry) * sgn / R if R else 0
    cands = [("current stop", stop)]
    if best_r >= 1:
        cands.append(("breakeven (1R reached)", entry))
    if best_r >= 1.5:
        cands.append(("chandelier %.1f ATR" % chand_k, extreme - sgn * chand_k * a))
    if best_r >= 2:
        cands.append(("lock 1R (2R reached)", entry + sgn * R))
    best = max(cands, key=lambda c: c[1] * sgn)
    new = best[1]
    events = []
    if (px - stop) * sgn <= 0:
        events.append("price %.2f is through the stop %.2f: the position should be closed" % (px, stop))
    if len(seg) >= time_stop and best_r < 1:
        events.append("time stop: %d bars without reaching 1R (best %.2fR)" % (len(seg), best_r))
    for k in (1, 2, 3):
        if best_r >= k:
            events.append("%dR (%.2f) reached" % (k, entry + sgn * k * R))
    moved = (new - stop) * sgn > 1e-9
    out = {"symbol": b.symbol, "side": side, "entry": entry, "stop_old": stop, "stop_new": T.r(new, 4),
           "move": moved, "reason": best[0], "price": T.r(px), "open_r": T.r((px - entry) * sgn / R if R else 0),
           "best_r": T.r(best_r), "bars_in_trade": len(seg), "events": events}
    L = ["TRAIL  %s %s  entry %s  price %s  (%.2fR now, best %.2fR, %d bars)" % (
        side.upper(), b.symbol, T.r(entry, 4), out["price"], out["open_r"], out["best_r"], len(seg)),
         ("MOVE STOP %s -> %s  (%s)" % (T.r(stop, 4), out["stop_new"], best[0])) if moved
         else "keep stop at %s" % T.r(stop, 4)]
    L += ["event: " + e for e in events]
    out["text"] = "\n".join(L)
    return out


def index_since(b, since):
    if not since:
        return 0
    ts = int(dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc).timestamp())
    for i, t in enumerate(b.t):
        if t >= ts:
            return i
    return len(b) - 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("symbol")
    p.add_argument("--entry", type=float, help="default: current price")
    p.add_argument("--stop", type=float)
    p.add_argument("--side", choices=["buy", "sell"], default="buy")
    p.add_argument("--chandelier", type=float, default=3.0)
    p.add_argument("--time-stop", type=int, default=10)
    p.add_argument("--order", help="write sl/tp into this order id in trader/orders.json")
    T.add_data_args(p, "D1", 300)
    t = sub.add_parser("trail")
    t.add_argument("symbol")
    t.add_argument("--entry", type=float, required=True)
    t.add_argument("--stop", type=float, required=True)
    t.add_argument("--side", choices=["buy", "sell"], default="buy")
    t.add_argument("--since", help="entry date YYYY-MM-DD (bars from then on)")
    t.add_argument("--chandelier", type=float, default=3.0)
    t.add_argument("--time-stop", type=int, default=10)
    T.add_data_args(t, "D1", 300)
    r = sub.add_parser("review", help="trail every open position")
    T.add_data_args(r, "D1", 300)
    a = ap.parse_args(argv)
    cfg = T.load_config()
    exits = T.load_json(T.tpath("exits.json"), {}) or {}

    if a.cmd == "plan":
        b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv)
        entry = a.entry if a.entry is not None else b.c[-1]
        o = plan(b, entry, a.side, a.stop, float(cfg["min_rr"]), a.chandelier, a.time_stop)
        exits[a.symbol.upper()] = {k: v for k, v in o.items() if k != "text"}
        T.save_json(T.tpath("exits.json"), exits)
        if a.order:
            data = T.load_orders()
            hit = [x for x in data["orders"] if x.get("id") == a.order]
            if not hit:
                T.die("order %s not found" % a.order)
            hit[0]["sl"], hit[0]["tp"], hit[0]["status"] = o["stop"], o["tp_single"], "proposed"
            T.save_orders(data)
            o["text"] += "\norder %s updated: sl %s tp %s (run risk check again; the volume may need re-sizing)" % (
                a.order, o["stop"], o["tp_single"])
        T.emit(o, a.json)
        return 0
    if a.cmd == "trail":
        b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv)
        T.emit(trail(b, a.entry, a.stop, a.side, index_since(b, a.since), a.chandelier, a.time_stop), a.json)
        return 0
    acct = T.account_snapshot(cfg)
    outs, L = [], ["EXIT REVIEW (%d open positions)" % len(acct["positions"])]
    for pos in acct["positions"]:
        sym = pos["symbol"]
        pl = exits.get(sym.upper()) or {}
        stop = pos.get("sl") or pl.get("stop")
        if not stop:
            L.append("%s: NO STOP. Run: exit.py plan %s --entry %s" % (sym, sym, pos["entry"]))
            continue
        b = T.get_bars(sym, a.tf, a.count, a.source, a.csv)
        since = pos.get("opened", pl.get("created", ""))[:10] or None
        o = trail(b, pos["entry"], stop, pos["side"], index_since(b, since), pl.get("chandelier_atr", 3.0),
                  pl.get("time_stop_bars", 10))
        o["position"] = pos.get("id")
        outs.append(o)
        L.append("[%s] " % pos.get("id") + o["text"].replace("\n", "\n    "))
    T.emit({"positions": outs, "text": "\n".join(L)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
