#!/usr/bin/env python3
"""Risk Manager: position size from your stop, and a PASS/BLOCK gate for every order.

    python3 risk.py size AAPL --entry 190 --stop 185 --target 200          # how many shares/lots
    python3 risk.py size AAPL --entry 190 --stop 185 --target 200 --write  # -> trader/orders.json
    python3 risk.py check                                                  # PASS / BLOCK each order
    python3 risk.py status                                                 # open risk, today's P&L
    python3 risk.py rules                                                  # show the limits in force

Limits live in trader/config.json (risk_per_trade_pct, max_open_positions,
max_daily_loss_pct, max_position_pct, max_total_risk_pct, min_rr,
blocked_symbols, allow_short). The order executor refuses any order whose
current contents did not PASS here.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

RULE_KEYS = ("mode", "account_balance", "risk_per_trade_pct", "max_open_positions", "max_daily_loss_pct",
             "max_position_pct", "max_total_risk_pct", "min_rr", "blocked_symbols", "allow_short")


def size(symbol, entry, stop, target=None, risk_pct=None, balance=None, side=None, cfg=None, spec=None):
    cfg = cfg or T.load_config()
    if entry == stop:
        T.die("entry and stop cannot be equal")
    side = side or ("buy" if stop < entry else "sell")
    if (side == "buy") != (stop < entry):
        T.die("for a %s the stop must be %s the entry" % (side, "below" if side == "buy" else "above"))
    spec = spec or T.symbol_spec(symbol, cfg)
    if balance is None:
        balance = T.account_snapshot(cfg)["balance"]
    risk_pct = float(risk_pct if risk_pct is not None else cfg["risk_per_trade_pct"])
    risk_cash = balance * risk_pct / 100.0
    mpp = T.money_per_point(spec)
    per_lot = abs(entry - stop) * mpp
    raw = risk_cash / per_lot
    vol = T.round_volume(raw, spec)
    notes = []
    cap = balance * float(cfg["max_position_pct"]) / 100.0
    notional = lambda v: v * spec["contract_size"] * entry  # noqa: E731
    if notional(vol) > cap:
        vol = T.round_volume(cap / (spec["contract_size"] * entry), spec)
        notes.append("reduced to respect max_position_pct %.0f%% (%.2f)" % (cfg["max_position_pct"], cap))
    if vol < spec["volume_min"]:
        notes.append("the stop is too wide for this account: even the minimum volume %s risks %.2f (%.2f%%)" % (
            spec["volume_min"], spec["volume_min"] * per_lot, spec["volume_min"] * per_lot / balance * 100))
        vol = 0.0
    out = {"symbol": symbol.upper(), "broker_symbol": spec["symbol"], "side": side, "entry": entry, "stop": stop,
           "target": target, "balance": round(balance, 2), "risk_pct": risk_pct, "risk_cash_allowed": round(risk_cash, 2),
           "volume": vol, "volume_unit": "shares" if spec["source"].startswith("paper") else "lots",
           "risk_cash": round(vol * per_lot, 2), "risk_pct_actual": round(vol * per_lot / balance * 100, 3),
           "notional": round(notional(vol), 2), "notes": notes, "spec": spec}
    if target is not None:
        rr = abs(target - entry) / abs(entry - stop)
        out["rr"] = round(rr, 2)
        out["reward_cash"] = round(vol * abs(target - entry) * mpp, 2)
        if (side == "buy") != (target > entry):
            notes.append("target is on the wrong side of the entry")
        elif rr < float(cfg["min_rr"]):
            notes.append("reward:risk %.2f is below your minimum %.2f" % (rr, cfg["min_rr"]))
    L = ["POSITION SIZE  %s %s" % (side.upper(), out["symbol"]),
         "entry %s  stop %s%s" % (entry, stop, "  target %s" % target if target is not None else ""),
         "balance %.2f x %.2f%% = %.2f allowed risk" % (balance, risk_pct, risk_cash),
         "=> volume %s %s  (risk %.2f = %.2f%%, notional %.2f)" % (
             vol, out["volume_unit"], out["risk_cash"], out["risk_pct_actual"], out["notional"])]
    if "rr" in out:
        L.append("reward:risk %.2f  (reward %.2f)" % (out["rr"], out["reward_cash"]))
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
    blocked = {s.upper() for s in cfg.get("blocked_symbols", [])}
    open_n = len(acct["positions"])
    open_risk = 0.0
    for p in acct["positions"]:
        if p.get("sl"):
            sp = spec_of(p["symbol"])
            open_risk += max(0.0, (p["entry"] - p["sl"]) if p["side"] == "buy" else (p["sl"] - p["entry"])) \
                * p["volume"] * T.money_per_point(sp)
        else:
            open_risk += float("inf")
    held = {(p["symbol"].upper(), p["side"]) for p in acct["positions"]}
    seen = set()
    results = []
    for o in orders:
        R, W = [], []
        sym = str(o.get("symbol", "")).upper()
        side = o.get("side")
        vol = float(o.get("volume") or 0)
        entry, sl, tp = o.get("entry"), o.get("sl"), o.get("tp")
        if not sym:
            R.append("no symbol")
        if sym in blocked:
            R.append("%s is on your blocked list" % sym)
        if side not in ("buy", "sell"):
            R.append("side must be buy or sell")
        if side == "sell" and not cfg.get("allow_short"):
            R.append("short selling is off (allow_short=false)")
        if o.get("type", "market") not in ("market", "limit", "stop"):
            R.append("order type must be market, limit or stop")
        if entry is None and price_of:
            entry = price_of(sym)
        if entry is None:
            R.append("no entry price")
        if not sl:
            R.append("no stop-loss: every order needs one")
        spec = spec_of(sym) if sym else None
        if spec and vol:
            if vol < spec["volume_min"] - 1e-9 or vol > spec["volume_max"] + 1e-9:
                R.append("volume %s outside %s..%s" % (vol, spec["volume_min"], spec["volume_max"]))
            step = spec["volume_step"] or 1
            if abs(vol / step - round(vol / step)) > 1e-6:
                R.append("volume %s is not a multiple of the step %s" % (vol, step))
        elif not vol:
            R.append("volume must be > 0")
        if entry is not None and sl and side in ("buy", "sell") and spec and vol:
            entry, sl = float(entry), float(sl)
            if (side == "buy" and sl >= entry) or (side == "sell" and sl <= entry):
                R.append("stop %.4f is on the wrong side of entry %.4f" % (sl, entry))
            else:
                mpp = T.money_per_point(spec)
                risk = abs(entry - sl) * vol * mpp
                lim = bal * float(cfg["risk_per_trade_pct"]) / 100
                if risk > lim * 1.001:
                    R.append("risks %.2f (%.2f%%), over your %.2f%% per trade (%.2f)" % (
                        risk, risk / bal * 100, cfg["risk_per_trade_pct"], lim))
                tot_lim = bal * float(cfg.get("max_total_risk_pct", 6)) / 100
                if open_risk + risk > tot_lim * 1.001:
                    R.append("total open risk would be %s, over max_total_risk_pct %.1f%% (%.2f)" % (
                        "unlimited (a position has no stop)" if open_risk == float("inf") else "%.2f" % (open_risk + risk),
                        cfg.get("max_total_risk_pct", 6), tot_lim))
                notion = vol * spec["contract_size"] * entry
                cap = bal * float(cfg["max_position_pct"]) / 100
                if notion > cap * 1.001:
                    R.append("position %.2f is over max_position_pct %.0f%% (%.2f)" % (notion, cfg["max_position_pct"], cap))
                if tp:
                    tp = float(tp)
                    if (side == "buy" and tp <= entry) or (side == "sell" and tp >= entry):
                        R.append("target %.4f is on the wrong side of entry" % tp)
                    else:
                        rr = abs(tp - entry) / abs(entry - sl)
                        if rr < float(cfg["min_rr"]) - 1e-9:
                            R.append("reward:risk %.2f below your minimum %.2f" % (rr, cfg["min_rr"]))
                else:
                    W.append("no take-profit set (exit-planner can add one)")
                if not R:
                    open_risk += risk
        if open_n + 1 > int(cfg["max_open_positions"]):
            R.append("would make %d open positions, max is %d" % (open_n + 1, cfg["max_open_positions"]))
        if today_pnl <= -bal * float(cfg["max_daily_loss_pct"]) / 100:
            R.append("daily loss limit hit: %.2f today (max %.1f%%). No new trades until tomorrow." % (
                today_pnl, cfg["max_daily_loss_pct"]))
        if (sym, side) in held:
            W.append("you already hold a %s position in %s" % (side, sym))
        if (sym, side) in seen:
            W.append("duplicate of another order in this batch")
        seen.add((sym, side))
        verdict = "BLOCK" if R else "PASS"
        if not R:
            open_n += 1
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
    clock = T.market_clock()
    res = check_orders(orders, cfg, price_of=lambda s: T.get_price(s, cfg)["price"])
    verdicts = T.load_json(T.tpath("risk_verdicts.json"), {}) or {}
    now = T.ny_now().isoformat(timespec="seconds")
    L, out = ["RISK CHECK  (%s, %s)" % (cfg["mode"], clock["status"])], []
    for o, v, R, W in res:
        h = T.order_hash(o)
        o["status"] = "passed" if v == "PASS" else "blocked"
        verdicts[h] = {"verdict": v, "order_id": o.get("id"), "reasons": R, "warnings": W, "checked_at": now}
        L.append("%s  %s  %s %s %s @%s sl %s tp %s" % (v, o.get("id"), o.get("side"), o.get("volume"),
                                                       o.get("symbol"), o.get("entry", "mkt"), o.get("sl"), o.get("tp")))
        L += ["   x " + x for x in R] + ["   ! " + x for x in W]
        out.append({"id": o.get("id"), "hash": h, "verdict": v, "reasons": R, "warnings": W})
    if not clock["open"]:
        L.append("note: market closed; market orders will wait for (or be rejected until) %s" % clock.get("next_open"))
    T.save_json(T.tpath("risk_verdicts.json"), verdicts)
    T.save_orders(data)
    T.emit({"results": out, "text": "\n".join(L)}, a.json)
    return 0 if all(r["verdict"] == "PASS" for r in out) else 1


def cmd_status(a, cfg):
    acct = T.account_snapshot(cfg)
    bal = acct["balance"]
    L = ["ACCOUNT  %s%s  balance %.2f  equity %.2f %s" % (
        acct["mode"], " (%s)" % acct["trade_mode"] if acct["mode"] == "mt5" else "", bal, acct["equity"], acct["currency"])]
    tot = 0.0
    for p in acct["positions"]:
        sp = T.symbol_spec(p["symbol"], cfg)
        if p.get("sl"):
            rk = max(0.0, (p["entry"] - p["sl"]) if p["side"] == "buy" else (p["sl"] - p["entry"])) * p["volume"] * T.money_per_point(sp)
            tot += rk
            rtxt = "%.2f (%.2f%%)" % (rk, rk / bal * 100)
        else:
            rtxt = "UNLIMITED: no stop"
            tot = float("inf")
        L.append("  %s %s %s @%s sl %s tp %s | P&L %.2f | risk %s" % (
            p["side"], p["volume"], p["symbol"], p["entry"], p.get("sl"), p.get("tp"), p.get("profit", 0), rtxt))
    today = T.realized_today(cfg)
    lim = bal * float(cfg["max_daily_loss_pct"]) / 100
    L.append("open positions %d / %d" % (len(acct["positions"]), cfg["max_open_positions"]))
    L.append("open risk %s of max %.2f (%.1f%%)" % ("UNLIMITED" if tot == float("inf") else "%.2f" % tot,
                                                   bal * float(cfg.get("max_total_risk_pct", 6)) / 100,
                                                   cfg.get("max_total_risk_pct", 6)))
    L.append("realized today %.2f; daily loss limit %.2f%s" % (today, -lim, "  ** HIT: stop trading today **" if today <= -lim else ""))
    T.emit({"account": acct, "open_risk": tot, "realized_today": today, "text": "\n".join(L)}, a.json)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("size", help="position size from entry and stop")
    s.add_argument("symbol")
    s.add_argument("--entry", type=float, help="entry price (default: current price)")
    s.add_argument("--stop", type=float, required=True)
    s.add_argument("--target", type=float)
    s.add_argument("--risk-pct", type=float)
    s.add_argument("--balance", type=float)
    s.add_argument("--type", choices=["market", "limit", "stop"], default=None)
    s.add_argument("--write", action="store_true", help="append the order to trader/orders.json")
    s.add_argument("--note", default="")
    s.add_argument("--json", action="store_true")
    c = sub.add_parser("check", help="PASS/BLOCK every proposed order")
    c.add_argument("--id", nargs="*")
    c.add_argument("--json", action="store_true")
    st = sub.add_parser("status", help="open positions, open risk, today's P&L")
    st.add_argument("--json", action="store_true")
    ru = sub.add_parser("rules", help="show the limits in force")
    ru.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    cfg.setdefault("max_total_risk_pct", 6.0)
    if a.cmd == "size":
        entry = a.entry if a.entry is not None else T.get_price(a.symbol, cfg)["price"]
        out = size(a.symbol, entry, a.stop, a.target, a.risk_pct, a.balance, cfg=cfg)
        if a.write and out["volume"] > 0:
            data = T.load_orders()
            n = 1 + max([int(str(o.get("id", "o0"))[1:] or 0) for o in data["orders"] if str(o.get("id", "")).startswith("o")] or [0])
            otype = a.type or ("market" if a.entry is None else "limit")
            order = {"id": "o%d" % n, "symbol": a.symbol.upper(), "side": out["side"], "type": otype,
                     "volume": out["volume"], "entry": round(entry, 4), "sl": a.stop, "tp": a.target,
                     "status": "proposed", "note": a.note, "created": T.ny_now().isoformat(timespec="seconds")}
            data["orders"].append(order)
            T.save_orders(data)
            out["order"] = order
            out["text"] += "\nwritten to trader/orders.json as %s (%s). Next: risk.py check" % (order["id"], otype)
        T.emit(out, a.json)
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
