#!/usr/bin/env python3
"""Order Executor: sends an order to MetaTrader 5 (or the local paper account)
ONLY after it passed the risk check and the user typed yes for that exact ticket.

    python3 order.py tickets                                  # what is ready, with ticket codes
    python3 order.py place o1 --ticket 3fa2c1d09b7e --confirm "yes"
    python3 order.py positions                                # open positions (paper: also fills/stops)
    python3 order.py modify 123456 --sl 188 --confirm "yes"   # stop may only tighten
    python3 order.py close 123456 --confirm "yes" [--volume 5]
    python3 order.py cancel o2                                # drop a proposed/pending order
    python3 order.py account                                  # which account and mode is active

Safety gates, all enforced here and not only in the instructions:
  * mode "paper" (default) never touches a broker.
  * mode "mt5" refuses REAL accounts unless "allow_live": true in trader/config.json.
  * the order's current contents must have a PASS from risk.py check (any edit voids it),
    and that PASS must be less than 60 minutes old.
  * --ticket must match the code shown by `tickets` for those exact contents.
  * --confirm must be the user's own affirmative words; each ticket can be placed once.
"""
import argparse
import datetime as dt
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

YES = re.compile(r"^\s*(yes|y|confirm|confirmed|go|place it|do it|ok|okay|نعم|ايوه|إيوه|ايه|اي|أي|أكيد|اكيد|موافق|نفذ|نفّذ|تمام|يلا)\b",
                 re.IGNORECASE)
NO = re.compile(r"\b(no|not|don'?t|cancel|stop|wait|hold)\b"
                r"|(?:^|\s)(?:لا|لأ|مو|ما|مب|انتظر|اصبر|الغ\w*|ألغ\w*|إلغ\w*|وقف|لحظة)(?=$|\s|[.,!?؟،])", re.IGNORECASE)
PASS_MAX_AGE_MIN = 60


def affirmative(text):
    return bool(text) and bool(YES.search(text)) and not NO.search(text)


def log_exec(entry):
    p = T.tpath("executions.json")
    log = T.load_json(p, {"executions": []})
    log["executions"].append(entry)
    T.save_json(p, log)


def verdict_for(order):
    v = (T.load_json(T.tpath("risk_verdicts.json"), {}) or {}).get(T.order_hash(order))
    if not v or v.get("verdict") != "PASS":
        return None, "no PASS from the risk manager for this exact order (run: risk.py check)"
    try:
        age = (T.ny_now() - dt.datetime.fromisoformat(v["checked_at"])).total_seconds() / 60
    except Exception:
        age = 1e9
    if age > PASS_MAX_AGE_MIN:
        return None, "the risk PASS is %.0f minutes old; run risk.py check again" % age
    return v, None


def ticket_text(o, cfg):
    return "%s %s %s %s%s  SL %s  TP %s" % (
        o["side"].upper(), o["volume"], o["symbol"], o.get("type", "market"),
        "" if o.get("type", "market") == "market" else " @ %s" % o.get("entry"), o.get("sl"), o.get("tp") or "none")


# ------------------------------------------------------------------ paper

def paper_fill(o, cfg, price):
    st = T.paper_state(cfg)
    spec = T.symbol_spec(o["symbol"], cfg)
    typ = o.get("type", "market")
    now = T.ny_now().isoformat(timespec="seconds")
    if typ != "market":
        st.setdefault("pending", []).append(dict(o, placed=now))
        T.save_paper(st)
        return {"status": "pending", "msg": "paper %s order resting at %s" % (typ, o["entry"])}
    pid = st["next_id"]
    st["next_id"] += 1
    pos = {"id": pid, "symbol": o["symbol"], "side": o["side"], "volume": o["volume"], "entry": price,
           "sl": o.get("sl"), "tp": o.get("tp"), "price": price, "mpp": T.money_per_point(spec), "opened": now,
           "order_id": o.get("id")}
    st["positions"].append(pos)
    T.save_paper(st)
    j = T.journal()
    j["trades"].append({"id": "p%d" % pid, "position": pid, "order_id": o.get("id"), "mode": "paper",
                        "symbol": o["symbol"], "side": o["side"], "volume": o["volume"], "entry": price,
                        "sl": o.get("sl"), "tp": o.get("tp"), "opened_at": now, "status": "open"})
    T.save_journal(j)
    return {"status": "filled", "position": pid, "price": price, "msg": "paper fill at %.4f" % price}


def paper_close(st, pos, price, why, volume=None):
    vol = min(volume or pos["volume"], pos["volume"])
    sgn = 1 if pos["side"] == "buy" else -1
    pnl = round(sgn * (price - pos["entry"]) * vol * pos.get("mpp", 1), 2)
    st["balance"] = round(st["balance"] + pnl, 2)
    pos["volume"] = round(pos["volume"] - vol, 8)
    if pos["volume"] <= 1e-9:
        st["positions"] = [p for p in st["positions"] if p["id"] != pos["id"]]
    now = T.ny_now().isoformat(timespec="seconds")
    j = T.journal()
    for t in j["trades"]:
        if t.get("position") == pos["id"] and t.get("mode") == "paper" and t["status"] == "open":
            risk = abs(t["entry"] - t["sl"]) * vol * pos.get("mpp", 1) if t.get("sl") else None
            if vol < t["volume"]:
                part = dict(t, volume=vol, exit=price, pnl=pnl, closed_at=now, status="closed", why=why,
                            id=t["id"] + "-%d" % len(j["trades"]), r=round(pnl / risk, 2) if risk else None)
                j["trades"].append(part)
                t["volume"] = round(t["volume"] - vol, 8)
            else:
                t.update(exit=price, pnl=pnl, closed_at=now, status="closed", why=why,
                         r=round(pnl / risk, 2) if risk else None)
            break
    T.save_journal(j)
    return pnl


def paper_sync(cfg):
    """Fill resting paper orders and apply stops/targets at the current price."""
    st = T.paper_state(cfg)
    events = []
    syms = {p["symbol"] for p in st["positions"]} | {o["symbol"] for o in st.get("pending", [])}
    prices = {}
    for s in syms:
        try:
            prices[s] = T.get_price(s, cfg)["price"]
        except SystemExit:
            pass
    keep = []
    for o in st.get("pending", []):
        px = prices.get(o["symbol"])
        trig = px is not None and (
            (o["type"] == "limit" and ((o["side"] == "buy" and px <= o["entry"]) or (o["side"] == "sell" and px >= o["entry"]))) or
            (o["type"] == "stop" and ((o["side"] == "buy" and px >= o["entry"]) or (o["side"] == "sell" and px <= o["entry"]))))
        if trig:
            T.save_paper(st)
            fillpx = o["entry"] if o["type"] == "limit" else px
            r = paper_fill(dict(o, type="market"), cfg, fillpx)
            st = T.paper_state(cfg)
            events.append("paper %s order %s filled at %.4f (position %s)" % (o["type"], o.get("id"), fillpx, r["position"]))
        else:
            keep.append(o)
    st["pending"] = keep
    for pos in list(st["positions"]):
        px = prices.get(pos["symbol"])
        if px is None:
            continue
        pos["price"] = px
        long = pos["side"] == "buy"
        if pos.get("sl") and ((long and px <= pos["sl"]) or (not long and px >= pos["sl"])):
            pnl = paper_close(st, pos, pos["sl"], "stop")
            events.append("paper position %s stopped at %s (%+.2f)" % (pos["id"], pos["sl"], pnl))
        elif pos.get("tp") and ((long and px >= pos["tp"]) or (not long and px <= pos["tp"])):
            pnl = paper_close(st, pos, pos["tp"], "target")
            events.append("paper position %s hit target %s (%+.2f)" % (pos["id"], pos["tp"], pnl))
    T.save_paper(st)
    return events


# ------------------------------------------------------------------ MT5

def mt5_guard(cfg):
    mt5 = T.mt5_connect(cfg, required=True)
    ai = mt5.account_info()
    if ai is None:
        T.die("MT5 is not logged in to an account")
    if ai.trade_mode == 2 and not cfg.get("allow_live"):
        T.die("account %s on %s is a REAL account. This pack refuses real accounts unless you set "
              '"allow_live": true in trader/config.json yourself. Practise on a demo account first.' % (ai.login, ai.server))
    ti = mt5.terminal_info()
    if ti is not None and not ti.trade_allowed:
        T.die("Algo Trading is switched off in the MT5 terminal (toolbar button 'Algo Trading').")
    return mt5, ai


def filling(mt5, si):
    fm = getattr(si, "filling_mode", 0)
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def mt5_send(mt5, req):
    chk = mt5.order_check(req)
    if chk is None or chk.retcode not in (0, mt5.TRADE_RETCODE_DONE):
        return False, "order_check failed: %s %s" % (getattr(chk, "retcode", None), getattr(chk, "comment", mt5.last_error()))
    res = mt5.order_send(req)
    if res is None:
        return False, "order_send returned nothing: %s" % (mt5.last_error(),)
    ok = res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
    return ok, {"retcode": res.retcode, "comment": res.comment, "order": res.order, "deal": res.deal,
                "price": res.price, "volume": res.volume}


def mt5_place(o, cfg):
    mt5, ai = mt5_guard(cfg)
    sym = T.broker_symbol(o["symbol"], cfg)
    if not mt5.symbol_select(sym, True):
        T.die("symbol %s not available at your broker" % sym)
    si = mt5.symbol_info(sym)
    tk = mt5.symbol_info_tick(sym)
    d = si.digits
    typ = o.get("type", "market")
    buy = o["side"] == "buy"
    req = {"symbol": sym, "volume": float(o["volume"]), "deviation": 20, "magic": int(cfg.get("magic", 2126)),
           "comment": "tadawal %s" % o.get("id", ""), "type_time": mt5.ORDER_TIME_GTC}
    if o.get("sl"):
        req["sl"] = round(float(o["sl"]), d)
    if o.get("tp"):
        req["tp"] = round(float(o["tp"]), d)
    if typ == "market":
        req.update(action=mt5.TRADE_ACTION_DEAL, type=mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                   price=tk.ask if buy else tk.bid, type_filling=filling(mt5, si))
    else:
        kind = {("buy", "limit"): "ORDER_TYPE_BUY_LIMIT", ("sell", "limit"): "ORDER_TYPE_SELL_LIMIT",
                ("buy", "stop"): "ORDER_TYPE_BUY_STOP", ("sell", "stop"): "ORDER_TYPE_SELL_STOP"}[(o["side"], typ)]
        req.update(action=mt5.TRADE_ACTION_PENDING, type=getattr(mt5, kind), price=round(float(o["entry"]), d),
                   type_filling=mt5.ORDER_FILLING_RETURN)
    ok, info = mt5_send(mt5, req)
    if not ok:
        return {"status": "rejected", "msg": str(info), "request": _plain(req)}
    st = "filled" if typ == "market" else "pending"
    if st == "filled":
        j = T.journal()
        j["trades"].append({"id": "m%s" % info["deal"], "position": info["order"], "order_id": o.get("id"), "mode": "mt5",
                            "account": ai.login, "symbol": o["symbol"], "side": o["side"], "volume": info["volume"],
                            "entry": info["price"], "sl": o.get("sl"), "tp": o.get("tp"),
                            "opened_at": T.ny_now().isoformat(timespec="seconds"), "status": "open"})
        T.save_journal(j)
    return {"status": st, "msg": "MT5 %s: retcode %s %s" % (st, info["retcode"], info["comment"]), "result": info,
            "request": _plain(req), "account": "%s (%s)" % (ai.login, {0: "demo", 1: "contest", 2: "REAL"}.get(ai.trade_mode))}


def _plain(req):
    return {k: (float(v) if isinstance(v, float) else v) for k, v in req.items()}


# ------------------------------------------------------------------ commands

def find_order(data, oid):
    for o in data["orders"]:
        if o.get("id") == oid:
            return o
    T.die("order %s not found in trader/orders.json" % oid)


def cmd_tickets(a, cfg):
    data = T.load_orders()
    L = ["TICKETS  (mode %s%s)" % (cfg["mode"], ", LIVE ALLOWED" if cfg.get("allow_live") else "")]
    out = []
    for o in data["orders"]:
        if o.get("status") in ("placed", "cancelled"):
            continue
        v, why = verdict_for(o)
        h = T.order_hash(o)
        if v:
            L.append("READY  %s  ticket %s  %s" % (o["id"], h, ticket_text(o, cfg)))
            for w in v.get("warnings", []):
                L.append("       ! " + w)
        else:
            L.append("NOT READY  %s  %s  -> %s" % (o["id"], ticket_text(o, cfg), why))
        out.append({"id": o["id"], "ticket": h if v else None, "ready": bool(v), "why": why, "order": o})
    if len(L) == 1:
        L.append("no orders. Size one with: risk.py size SYMBOL --entry X --stop Y --target Z --write")
    else:
        L.append("To place: the user must reply yes for a specific ticket; then order.py place ID --ticket CODE --confirm \"<their words>\"")
    T.emit({"tickets": out, "text": "\n".join(L)}, a.json)
    return 0


def cmd_place(a, cfg):
    data = T.load_orders()
    o = find_order(data, a.order_id)
    if o.get("status") == "placed":
        T.die("order %s was already placed (%s)" % (o["id"], o.get("placed_at")))
    h = T.order_hash(o)
    if a.ticket != h:
        T.die("ticket %s does not match order %s as it is now (%s). The order changed: show the tickets again "
              "and get a new yes." % (a.ticket, o["id"], h))
    v, why = verdict_for(o)
    if not v:
        T.die(why)
    if not affirmative(a.confirm):
        T.die("--confirm %r is not a clear yes. Placing nothing." % a.confirm)
    clock = T.market_clock()
    mode = cfg["mode"]
    if mode == "paper":
        if o.get("type", "market") == "market" and not clock["open"]:
            T.die("market is closed (%s). Use a limit order or place it after %s." % (clock["status"], clock.get("next_open")))
        price = T.get_price(o["symbol"], cfg)["price"] if o.get("type", "market") == "market" else o["entry"]
        res = paper_fill(o, cfg, price)
    elif mode == "mt5":
        res = mt5_place(o, cfg)
    else:
        T.die("unknown mode %r (paper or mt5)" % mode)
    now = T.ny_now().isoformat(timespec="seconds")
    ok = res["status"] in ("filled", "pending")
    if ok:
        o["status"], o["placed_at"] = "placed", now
        T.save_orders(data)
    log_exec({"at": now, "mode": mode, "order": o, "ticket": h, "user_confirmation": a.confirm, "result": res})
    T.emit(dict(res, text="%s  %s  ticket %s\n%s" % ("PLACED" if ok else "NOT PLACED", ticket_text(o, cfg), h, res["msg"])),
           a.json)
    return 0 if ok else 1


def cmd_positions(a, cfg):
    events = paper_sync(cfg) if cfg["mode"] == "paper" else []
    acct = T.account_snapshot(cfg)
    L = ["POSITIONS  %s  balance %.2f equity %.2f %s" % (acct["mode"] + (" " + acct["trade_mode"] if acct["mode"] == "mt5" else ""),
                                                        acct["balance"], acct["equity"], acct["currency"])]
    L += ["event: " + e for e in events]
    for p in acct["positions"]:
        L.append("  #%s %s %s %s @%s  now %s  SL %s TP %s  P&L %+.2f" % (
            p["id"], p["side"], p["volume"], p["symbol"], p["entry"], p.get("price"), p.get("sl"), p.get("tp"), p.get("profit", 0)))
    if cfg["mode"] == "paper":
        for o in T.paper_state(cfg).get("pending", []):
            L.append("  pending %s %s %s %s @%s" % (o.get("id"), o["side"], o["volume"], o["symbol"], o["entry"]))
    if not acct["positions"]:
        L.append("  no open positions")
    T.emit({"account": acct, "events": events, "text": "\n".join(L)}, a.json)
    return 0


def _find_position(acct, pid):
    for p in acct["positions"]:
        if str(p["id"]) == str(pid):
            return p
    T.die("position %s not found" % pid)


def cmd_modify(a, cfg):
    if not affirmative(a.confirm):
        T.die("--confirm is not a clear yes")
    acct = T.account_snapshot(cfg)
    p = _find_position(acct, a.position)
    long = p["side"] == "buy"
    if a.sl is not None and p.get("sl"):
        if (long and a.sl < p["sl"]) or (not long and a.sl > p["sl"]):
            T.die("refusing to loosen the stop (%s -> %s). Stops only move in the trade's favour." % (p["sl"], a.sl))
    if a.sl is not None and p.get("price") and ((long and a.sl >= p["price"]) or (not long and a.sl <= p["price"])):
        T.die("stop %s is through the current price %s; close the position instead" % (a.sl, p["price"]))
    if cfg["mode"] == "paper":
        st = T.paper_state(cfg)
        for q in st["positions"]:
            if str(q["id"]) == str(a.position):
                if a.sl is not None:
                    q["sl"] = a.sl
                if a.tp is not None:
                    q["tp"] = a.tp
        T.save_paper(st)
        msg = "paper position %s: SL %s TP %s" % (a.position, a.sl if a.sl is not None else p.get("sl"),
                                                   a.tp if a.tp is not None else p.get("tp"))
        ok = True
    else:
        mt5, _ = mt5_guard(cfg)
        req = {"action": mt5.TRADE_ACTION_SLTP, "position": int(a.position), "symbol": p["symbol"],
               "sl": float(a.sl if a.sl is not None else (p.get("sl") or 0.0)),
               "tp": float(a.tp if a.tp is not None else (p.get("tp") or 0.0))}
        ok, info = mt5_send(mt5, req)
        msg = str(info)
    log_exec({"at": T.ny_now().isoformat(timespec="seconds"), "mode": cfg["mode"], "modify": a.position, "sl": a.sl,
              "tp": a.tp, "user_confirmation": a.confirm, "ok": ok, "result": msg})
    T.emit({"ok": ok, "text": ("MODIFIED " if ok else "FAILED ") + msg}, a.json)
    return 0 if ok else 1


def cmd_close(a, cfg):
    if not affirmative(a.confirm):
        T.die("--confirm is not a clear yes")
    acct = T.account_snapshot(cfg)
    p = _find_position(acct, a.position)
    if cfg["mode"] == "paper":
        st = T.paper_state(cfg)
        pos = next(q for q in st["positions"] if str(q["id"]) == str(a.position))
        px = T.get_price(p["symbol"], cfg)["price"]
        pnl = paper_close(st, pos, px, "manual close", a.volume)
        T.save_paper(st)
        ok, msg = True, "paper close at %.4f, P&L %+.2f" % (px, pnl)
    else:
        mt5, _ = mt5_guard(cfg)
        si = mt5.symbol_info(p["symbol"])
        tk = mt5.symbol_info_tick(p["symbol"])
        long = p["side"] == "buy"
        req = {"action": mt5.TRADE_ACTION_DEAL, "position": int(a.position), "symbol": p["symbol"],
               "volume": float(a.volume or p["volume"]), "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY,
               "price": tk.bid if long else tk.ask, "deviation": 20, "magic": int(cfg.get("magic", 2126)),
               "comment": "tadawal close", "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling(mt5, si)}
        ok, info = mt5_send(mt5, req)
        msg = str(info)
    log_exec({"at": T.ny_now().isoformat(timespec="seconds"), "mode": cfg["mode"], "close": a.position,
              "volume": a.volume, "user_confirmation": a.confirm, "ok": ok, "result": msg})
    T.emit({"ok": ok, "text": ("CLOSED " if ok else "FAILED ") + msg}, a.json)
    return 0 if ok else 1


def cmd_cancel(a, cfg):
    data = T.load_orders()
    o = find_order(data, a.order_id)
    msg = "order %s cancelled" % o["id"]
    if o.get("status") == "placed":
        if cfg["mode"] == "paper":
            st = T.paper_state(cfg)
            before = len(st.get("pending", []))
            st["pending"] = [x for x in st.get("pending", []) if x.get("id") != o["id"]]
            T.save_paper(st)
            if before == len(st["pending"]):
                T.die("order %s already filled; close the position instead" % o["id"])
        else:
            mt5, _ = mt5_guard(cfg)
            hit = [x for x in (mt5.orders_get() or []) if x.comment.strip() == ("tadawal %s" % o["id"]).strip()]
            if not hit:
                T.die("no pending MT5 order for %s (already filled? close the position instead)" % o["id"])
            ok, info = mt5_send(mt5, {"action": mt5.TRADE_ACTION_REMOVE, "order": int(hit[0].ticket)})
            if not ok:
                T.die("MT5 refused the cancel: %s" % info)
    o["status"] = "cancelled"
    T.save_orders(data)
    log_exec({"at": T.ny_now().isoformat(timespec="seconds"), "mode": cfg["mode"], "cancel": o["id"]})
    T.emit({"text": msg}, a.json)
    return 0


def cmd_account(a, cfg):
    acct = T.account_snapshot(cfg)
    txt = "mode %s | %s | balance %.2f equity %.2f %s | live trading %s" % (
        cfg["mode"], ("account %s on %s (%s)" % (acct["account"], acct["server"], acct["trade_mode"])) if acct["mode"] == "mt5"
        else "local paper account", acct["balance"], acct["equity"], acct["currency"],
        "ALLOWED" if cfg.get("allow_live") else "blocked")
    T.emit(dict(acct, text=txt), a.json)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tickets")
    p = sub.add_parser("place")
    p.add_argument("order_id")
    p.add_argument("--ticket", required=True)
    p.add_argument("--confirm", required=True, help="the user's own words, e.g. 'yes'")
    sub.add_parser("positions")
    m = sub.add_parser("modify")
    m.add_argument("position")
    m.add_argument("--sl", type=float)
    m.add_argument("--tp", type=float)
    m.add_argument("--confirm", required=True)
    c = sub.add_parser("close")
    c.add_argument("position")
    c.add_argument("--volume", type=float)
    c.add_argument("--confirm", required=True)
    x = sub.add_parser("cancel")
    x.add_argument("order_id")
    sub.add_parser("account")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    return {"tickets": cmd_tickets, "place": cmd_place, "positions": cmd_positions, "modify": cmd_modify,
            "close": cmd_close, "cancel": cmd_cancel, "account": cmd_account}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
