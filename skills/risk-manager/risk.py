#!/usr/bin/env python3
"""Risk Manager: lot size from your stop, and a PASS/BLOCK gate for every order.

    python3 risk.py size EURUSD --entry 1.0850 --stop 1.0820 --target 1.0910
    python3 risk.py size EURUSD --side buy --stop-pips 30 --rr 2           # entry = current price
    python3 risk.py size GBPJPY --entry 195.40 --stop 196.10 --target 194.00 --write   # -> trader/orders.json
    python3 risk.py pipvalue EURUSD GBPJPY XAUUSD                           # value of 1 pip per lot
    python3 risk.py check                                                   # PASS / BLOCK each order
    python3 risk.py status                                                  # open risk, today's P&L, exposure
    python3 risk.py rules                                                   # the limits in force

Limits live in trader/config.json (risk_per_trade_pct, max_total_risk_pct,
max_open_positions, max_daily_loss_pct, max_leverage, max_same_currency,
min_rr, blocked_symbols, allow_short). The order executor refuses any order
whose current contents did not PASS here.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

RULE_KEYS = ("mode", "currency", "account_balance", "risk_per_trade_pct", "max_total_risk_pct", "max_open_positions",
             "max_daily_loss_pct", "max_leverage", "max_same_currency", "min_rr", "blocked_symbols", "allow_short")


def size(symbol, entry, stop, target=None, risk_pct=None, balance=None, cfg=None, spec=None):
    cfg = cfg or T.load_config()
    sym = T.norm_symbol(symbol)
    T.set_symbol(sym)
    if entry == stop:
        T.die("entry and stop cannot be equal")
    side = "buy" if stop < entry else "sell"
    spec = spec or T.symbol_spec(sym, cfg)
    if balance is None:
        balance = T.account_snapshot(cfg)["balance"]
    risk_pct = float(risk_pct if risk_pct is not None else cfg["risk_per_trade_pct"])
    risk_cash = balance * risk_pct / 100.0
    mpp = T.money_per_point(spec)
    per_lot = abs(entry - stop) * mpp
    vol = T.round_volume(risk_cash / per_lot, spec)
    notes = []
    max_not = balance * float(cfg["max_leverage"])
    if T.notional(spec, vol, entry) > max_not:
        vol = T.round_volume(max_not / T.notional(spec, 1.0, entry), spec)
        notes.append("reduced to respect max_leverage %sx (%.0f %s notional)" % (cfg["max_leverage"], max_not, cfg["currency"]))
    if vol < spec["volume_min"]:
        notes.append("the stop is too wide for this account: even %s lots risk %.2f (%.2f%%)" % (
            spec["volume_min"], spec["volume_min"] * per_lot, spec["volume_min"] * per_lot / balance * 100))
        vol = 0.0
    stop_pips = T.pips(abs(entry - stop), sym)
    out = {"symbol": sym, "broker_symbol": spec.get("broker_symbol", sym), "side": side, "entry": entry, "stop": stop,
           "target": target, "stop_pips": stop_pips, "pip_value_per_lot": round(T.pip_value(spec), 4),
           "balance": round(balance, 2), "risk_pct": risk_pct, "risk_cash_allowed": round(risk_cash, 2),
           "volume": vol, "risk_cash": round(vol * per_lot, 2), "risk_pct_actual": round(vol * per_lot / balance * 100, 3),
           "notional": round(T.notional(spec, vol, entry), 2), "leverage_used": round(T.notional(spec, vol, entry) / balance, 2),
           "margin_estimate": round(T.notional(spec, vol, entry) / float(cfg.get("leverage") or 100), 2),
           "notes": notes, "spec": spec}
    if target is not None:
        rr = abs(target - entry) / abs(entry - stop)
        out.update(rr=round(rr, 2), target_pips=T.pips(abs(target - entry), sym),
                   reward_cash=round(vol * abs(target - entry) * mpp, 2))
        if (side == "buy") != (target > entry):
            notes.append("target is on the wrong side of the entry")
        elif rr < float(cfg["min_rr"]):
            notes.append("reward:risk %.2f is below your minimum %.2f" % (rr, cfg["min_rr"]))
    P = lambda x: T.px(x, sym)  # noqa: E731
    L = ["POSITION SIZE  %s %s" % (side.upper(), sym),
         "entry %s  stop %s (%s pips)%s" % (P(entry), P(stop), stop_pips,
                                             "  target %s (%s pips)" % (P(target), out["target_pips"]) if target is not None else ""),
         "balance %.2f x %.2f%% = %.2f %s allowed risk | 1 pip = %.2f %s per lot" % (
             balance, risk_pct, risk_cash, cfg["currency"], out["pip_value_per_lot"], cfg["currency"]),
         "=> %s lots  (risk %.2f = %.2f%%, notional %.0f, leverage %sx, margin ~%.2f)" % (
             vol, out["risk_cash"], out["risk_pct_actual"], out["notional"], out["leverage_used"], out["margin_estimate"])]
    if "rr" in out:
        L.append("reward:risk %.2f  (reward %.2f %s)" % (out["rr"], out["reward_cash"], cfg["currency"]))
    L += ["note: " + n for n in notes]
    out["text"] = "\n".join(L)
    return out


def check_orders(orders, cfg=None, acct=None, today_pnl=None, price_of=None, spec_of=None):
    """Return [(order, verdict, reasons, warnings)]. Pure function given its inputs."""
    cfg = cfg or T.load_config()
    acct = acct or T.account_snapshot(cfg)
    bal = acct["balance"]
    today_pnl = T.realized_today(cfg) if today_pnl is None else today_pnl
    spec_of = spec_of or (lambda s: T.symbol_spec(s, cfg))
    blocked = {T.norm_symbol(s) for s in cfg.get("blocked_symbols", [])}
    positions = list(acct["positions"])
    open_n = len(positions)
    open_risk, open_not = 0.0, 0.0
    for p in positions:
        sp = spec_of(p["symbol"])
        open_not += T.notional(sp, p["volume"], p.get("price") or p["entry"])
        if p.get("sl"):
            open_risk += max(0.0, (p["entry"] - p["sl"]) if p["side"] == "buy" else (p["sl"] - p["entry"])) \
                * p["volume"] * T.money_per_point(sp)
        else:
            open_risk = float("inf")
    held = {(T.norm_symbol(p["symbol"]), p["side"]) for p in positions}
    seen, results = set(), []
    for o in orders:
        R, W = [], []
        sym = T.norm_symbol(o.get("symbol"))
        side = o.get("side")
        vol = float(o.get("volume") or 0)
        entry, sl, tp = o.get("entry"), o.get("sl"), o.get("tp")
        if not sym:
            R.append("no symbol")
        if sym in blocked:
            R.append("%s is on your blocked list" % sym)
        if side not in ("buy", "sell"):
            R.append("side must be buy or sell")
        if side == "sell" and not cfg.get("allow_short", True):
            R.append("selling is off (allow_short=false)")
        if o.get("type", "market") not in ("market", "limit", "stop"):
            R.append("order type must be market, limit or stop")
        if entry is None and price_of and sym:
            entry = price_of(sym)
        if entry is None:
            R.append("no entry price")
        if not sl:
            R.append("no stop-loss: every order needs one")
        spec = spec_of(sym) if sym else None
        if spec and vol:
            if vol < spec["volume_min"] - 1e-9 or vol > spec["volume_max"] + 1e-9:
                R.append("volume %s outside %s..%s lots" % (vol, spec["volume_min"], spec["volume_max"]))
            step = spec["volume_step"] or 0.01
            if abs(vol / step - round(vol / step)) > 1e-6:
                R.append("volume %s is not a multiple of the lot step %s" % (vol, step))
        elif not vol:
            R.append("volume must be > 0")
        if entry is not None and sl and side in ("buy", "sell") and spec and vol:
            entry, sl = float(entry), float(sl)
            if (side == "buy" and sl >= entry) or (side == "sell" and sl <= entry):
                R.append("stop %s is on the wrong side of entry %s" % (T.px(sl, sym), T.px(entry, sym)))
            else:
                mpp = T.money_per_point(spec)
                risk = abs(entry - sl) * vol * mpp
                lim = bal * float(cfg["risk_per_trade_pct"]) / 100
                if risk > lim * 1.001:
                    R.append("risks %.2f (%.2f%%), over your %.2f%% per trade (%.2f)" % (
                        risk, risk / bal * 100, cfg["risk_per_trade_pct"], lim))
                tot_lim = bal * float(cfg["max_total_risk_pct"]) / 100
                if open_risk + risk > tot_lim * 1.001:
                    R.append("total open risk would be %s, over max_total_risk_pct %.1f%% (%.2f)" % (
                        "unlimited (a position has no stop)" if open_risk == float("inf") else "%.2f" % (open_risk + risk),
                        cfg["max_total_risk_pct"], tot_lim))
                nt = T.notional(spec, vol, entry)
                if (open_not + nt) / bal > float(cfg["max_leverage"]) * 1.001:
                    R.append("leverage would be %.1fx, over max_leverage %sx" % ((open_not + nt) / bal, cfg["max_leverage"]))
                if T.pips(abs(entry - sl), sym) < 5 and T.split_pair(sym)[0] not in ("XAU", "XAG"):
                    W.append("stop is only %s pips: normal spread and noise can hit it" % T.pips(abs(entry - sl), sym))
                if tp:
                    tp = float(tp)
                    if (side == "buy" and tp <= entry) or (side == "sell" and tp >= entry):
                        R.append("target %s is on the wrong side of entry" % T.px(tp, sym))
                    else:
                        rr = abs(tp - entry) / abs(entry - sl)
                        if rr < float(cfg["min_rr"]) - 1e-9:
                            R.append("reward:risk %.2f below your minimum %.2f" % (rr, cfg["min_rr"]))
                else:
                    W.append("no take-profit set (exit-planner can add one)")
                if not R:
                    open_risk += risk
                    open_not += nt
        if open_n + 1 > int(cfg["max_open_positions"]):
            R.append("would make %d open positions, max is %d" % (open_n + 1, cfg["max_open_positions"]))
        if today_pnl <= -bal * float(cfg["max_daily_loss_pct"]) / 100:
            R.append("daily loss limit hit: %.2f today (max %.1f%%). No new trades until the next trading day "
                     "(17:00 New York)." % (today_pnl, cfg["max_daily_loss_pct"]))
        if (sym, side) in held:
            W.append("you already hold a %s position in %s" % (side, sym))
        if (sym, side) in seen:
            W.append("duplicate of another order in this batch")
        if sym and side in ("buy", "sell"):
            ex = T.exposure(positions + [{"symbol": sym, "side": side}])
            for ccy in T.split_pair(sym):
                if ccy and abs(ex.get(ccy, 0)) > int(cfg.get("max_same_currency", 2)):
                    W.append("%d trades would all be %s %s: correlated risk (they win or lose together)" % (
                        abs(ex[ccy]), "long" if ex[ccy] > 0 else "short", ccy))
        seen.add((sym, side))
        verdict = "BLOCK" if R else "PASS"
        if not R:
            open_n += 1
            positions.append({"symbol": sym, "side": side})
        results.append((o, verdict, R, W))
    return results


def cmd_check(a, cfg):
    data = T.load_orders()
    orders = [o for o in data["orders"] if o.get("status", "proposed") in ("proposed", "blocked", "passed")]
    if a.id:
        orders = [o for o in orders if o.get("id") in a.id]
    if not orders:
        T.emit({"text": "no proposed orders in trader/orders.json"}, a.json)
        return 0
    clock = T.market_clock(cfg=cfg)
    res = check_orders(orders, cfg, price_of=lambda s: T.get_price(s, cfg)["price"])
    verdicts = T.load_json(T.tpath("risk_verdicts.json"), {}) or {}
    now = T.ny_now().isoformat(timespec="seconds")
    L, out = ["RISK CHECK  (%s, %s)" % (cfg["mode"], clock["status"])], []
    for o, v, R, W in res:
        h = T.order_hash(o)
        o["status"] = "passed" if v == "PASS" else "blocked"
        verdicts[h] = {"verdict": v, "order_id": o.get("id"), "reasons": R, "warnings": W, "checked_at": now}
        sym = T.norm_symbol(o.get("symbol"))
        L.append("%s  %s  %s %s lots %s @%s sl %s tp %s" % (
            v, o.get("id"), o.get("side"), o.get("volume"), sym, T.px(o["entry"], sym) if o.get("entry") else "mkt",
            T.px(o.get("sl"), sym), T.px(o.get("tp"), sym)))
        L += ["   x " + x for x in R] + ["   ! " + x for x in W]
        out.append({"id": o.get("id"), "hash": h, "verdict": v, "reasons": R, "warnings": W})
    if not clock["open"]:
        L.append("note: forex is closed; orders wait until %s" % clock.get("next_open"))
    T.save_json(T.tpath("risk_verdicts.json"), verdicts)
    T.save_orders(data)
    T.emit({"results": out, "text": "\n".join(L)}, a.json)
    return 0 if all(r["verdict"] == "PASS" for r in out) else 1


def cmd_status(a, cfg):
    acct = T.account_snapshot(cfg)
    bal = acct["balance"]
    L = ["ACCOUNT  %s%s  balance %.2f  equity %.2f %s" % (
        acct["mode"], " (%s)" % acct["trade_mode"] if acct["mode"] == "mt5" else "", bal, acct["equity"], acct["currency"])]
    tot, nt = 0.0, 0.0
    for p in acct["positions"]:
        sp = T.symbol_spec(p["symbol"], cfg)
        nt += T.notional(sp, p["volume"], p.get("price") or p["entry"])
        if p.get("sl"):
            dist = max(0.0, (p["entry"] - p["sl"]) if p["side"] == "buy" else (p["sl"] - p["entry"]))
            rk = dist * p["volume"] * T.money_per_point(sp)
            tot += rk
            rtxt = "%.2f (%.2f%%, %s pips)" % (rk, rk / bal * 100, T.pips(dist, p["symbol"]))
        else:
            rtxt = "UNLIMITED: no stop"
            tot = float("inf")
        L.append("  %s %s %s @%s sl %s tp %s | P&L %.2f | risk %s" % (
            p["side"], p["volume"], p["symbol"], T.px(p["entry"], p["symbol"]), T.px(p.get("sl"), p["symbol"]),
            T.px(p.get("tp"), p["symbol"]), p.get("profit", 0), rtxt))
    today = T.realized_today(cfg)
    lim = bal * float(cfg["max_daily_loss_pct"]) / 100
    ex = {k: v for k, v in T.exposure(acct["positions"]).items() if v}
    L.append("open positions %d / %d | leverage %.1fx of max %sx" % (
        len(acct["positions"]), cfg["max_open_positions"], nt / bal if bal else 0, cfg["max_leverage"]))
    L.append("open risk %s of max %.2f (%.1f%%)" % ("UNLIMITED" if tot == float("inf") else "%.2f" % tot,
                                                   bal * float(cfg["max_total_risk_pct"]) / 100, cfg["max_total_risk_pct"]))
    if ex:
        L.append("currency exposure: " + ", ".join("%s %s%d" % (k, "+" if v > 0 else "", v) for k, v in sorted(ex.items())))
    L.append("realized today %.2f; daily loss limit %.2f%s" % (today, -lim, "  ** HIT: stop trading today **" if today <= -lim else ""))
    T.emit({"account": acct, "open_risk": tot, "realized_today": today, "exposure": ex, "text": "\n".join(L)}, a.json)
    return 0


def next_order_id(data):
    nums = [int(str(o.get("id", ""))[1:]) for o in data["orders"] if str(o.get("id", "")).startswith("o")
            and str(o.get("id"))[1:].isdigit()]
    return "o%d" % (max(nums or [0]) + 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("size", help="lot size from entry and stop")
    s.add_argument("symbol")
    s.add_argument("--entry", type=float, help="entry price (default: current price)")
    s.add_argument("--stop", type=float)
    s.add_argument("--stop-pips", type=float, help="stop distance in pips instead of --stop")
    s.add_argument("--target", type=float)
    s.add_argument("--target-pips", type=float)
    s.add_argument("--rr", type=float, help="target at this reward:risk")
    s.add_argument("--side", choices=["buy", "sell"], help="needed with --stop-pips")
    s.add_argument("--risk-pct", type=float)
    s.add_argument("--balance", type=float)
    s.add_argument("--type", choices=["market", "limit", "stop"], default=None)
    s.add_argument("--write", action="store_true", help="append the order to trader/orders.json")
    s.add_argument("--note", default="")
    s.add_argument("--json", action="store_true")
    pv = sub.add_parser("pipvalue", help="value of one pip per lot")
    pv.add_argument("symbols", nargs="+")
    pv.add_argument("--json", action="store_true")
    c = sub.add_parser("check", help="PASS/BLOCK every proposed order")
    c.add_argument("--id", nargs="*")
    c.add_argument("--json", action="store_true")
    st = sub.add_parser("status", help="open positions, open risk, exposure, today's P&L")
    st.add_argument("--json", action="store_true")
    ru = sub.add_parser("rules", help="show the limits in force")
    ru.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    if a.cmd == "size":
        sym = T.norm_symbol(a.symbol)
        entry = a.entry if a.entry is not None else T.get_price(sym, cfg)["price"]
        stop = a.stop
        if stop is None:
            if a.stop_pips is None or not a.side:
                T.die("give --stop, or --stop-pips with --side buy|sell")
            stop = entry - (1 if a.side == "buy" else -1) * a.stop_pips * T.pip_size(sym)
        sgn = 1 if stop < entry else -1
        target = a.target
        if target is None and a.target_pips:
            target = entry + sgn * a.target_pips * T.pip_size(sym)
        if target is None and a.rr:
            target = entry + sgn * a.rr * abs(entry - stop)
        d = T.digits_for(sym)
        stop, target = round(stop, d), (round(target, d) if target is not None else None)
        out = size(sym, entry, stop, target, a.risk_pct, a.balance, cfg=cfg)
        if a.write and out["volume"] > 0:
            data = T.load_orders()
            otype = a.type or ("market" if a.entry is None else "limit")
            order = {"id": next_order_id(data), "symbol": sym, "side": out["side"], "type": otype,
                     "volume": out["volume"], "entry": round(entry, d), "sl": stop, "tp": target,
                     "status": "proposed", "note": a.note, "created": T.ny_now().isoformat(timespec="seconds")}
            data["orders"].append(order)
            T.save_orders(data)
            out["order"] = order
            out["text"] += "\nwritten to trader/orders.json as %s (%s). Next: risk.py check" % (order["id"], otype)
        T.emit(out, a.json)
        return 0
    if a.cmd == "pipvalue":
        rows = []
        for s_ in a.symbols:
            sp = T.symbol_spec(s_, cfg)
            rows.append({"symbol": sp["symbol"], "pip": T.pip_size(sp["symbol"]), "pip_value_per_lot": round(T.pip_value(sp), 4)})
        T.emit({"pip_values": rows, "text": "\n".join("%-8s 1 pip = %s  ->  %.2f %s per standard lot, %.2f per mini (0.1), %.2f per micro (0.01)" % (
            r_["symbol"], r_["pip"], r_["pip_value_per_lot"], cfg["currency"], r_["pip_value_per_lot"] / 10,
            r_["pip_value_per_lot"] / 100) for r_ in rows)}, a.json)
        return 0
    if a.cmd == "check":
        return cmd_check(a, cfg)
    if a.cmd == "status":
        return cmd_status(a, cfg)
    rules = {k: cfg.get(k) for k in RULE_KEYS}
    T.emit(dict(rules=rules, text="\n".join("%-20s %s" % (k, v) for k, v in rules.items())), a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
