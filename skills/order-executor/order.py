#!/usr/bin/env python3
"""Order Executor: places an order ONLY after it passed the risk check and the
user typed yes for that exact ticket. Three modes (trader/config.json "mode"):

  paper   simulated fills on a local ledger (default, no broker)
  manual  you place the order yourself in the MetaTrader 5 phone/desktop app; this
          prints the exact steps and records what you did (works with MT5 mobile)
  mt5     the order is sent to the MetaTrader 5 desktop terminal (Windows)

    python3 order.py tickets                                   # what is ready, with ticket codes
    python3 order.py place o1 --ticket 3fa2c1d09b7e --confirm "نعم"
    python3 order.py filled o1 --price 1.08512                 # manual: you placed it, at this price
    python3 order.py positions                                 # open positions (paper: fills, stops, targets)
    python3 order.py modify 7 --sl 1.0835 --confirm "yes"      # stop may only tighten
    python3 order.py close 7 --confirm "yes" [--volume 0.1] [--price 1.0890]
    python3 order.py cancel o2                                 # drop a proposed/pending order
    python3 order.py account                                   # which account and mode is active

Safety gates, enforced here and not only in the instructions:
  * mode "mt5" refuses REAL accounts unless "allow_live": true in trader/config.json,
    refuses when Algo Trading is off, and when the spread is above max_spread_pips.
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

YES = re.compile(r"^\s*(yes|y|confirm|confirmed|go|place it|do it|ok|okay|done|نعم|ايوه|إيوه|ايه|اي|أي|أكيد|اكيد|موافق|"
                 r"نفذ|نفّذ|تمام|يلا|اوكي|أوكي)\b", re.IGNORECASE)
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


def now_iso():
    return T.ny_now().isoformat(timespec="seconds")


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


def plan_gate(o, cfg, override):
    """None if the trading plan allows the order (or there is no enforced plan), else the reason."""
    plan = T.load_json(T.tpath("plan.json"), None)
    if not plan or not plan.get("enforce", True):
        return None
    v = (T.load_json(T.tpath("plan_verdicts.json"), {}) or {}).get(T.order_hash(o))
    why = None
    if not v:
        why = "not checked against your trading plan (run: plan.py check %s)" % o.get("id")
    elif v.get("verdict") != "IN PLAN":
        why = "%s: %s" % (v["verdict"], "; ".join(v.get("reasons", [])))
    else:
        try:
            age = (T.ny_now() - dt.datetime.fromisoformat(v["checked_at"])).total_seconds() / 60
        except Exception:
            age = 1e9
        if age > PASS_MAX_AGE_MIN:
            why = "the plan check is %.0f minutes old; run plan.py check again" % age
    if why and override:
        return None
    return why


def ticket_text(o):
    s = T.norm_symbol(o["symbol"])
    typ = o.get("type", "market")
    return "%s %s lots %s %s%s  SL %s  TP %s" % (
        o["side"].upper(), o["volume"], s, typ, "" if typ == "market" else " @ %s" % T.px(o.get("entry"), s),
        T.px(o.get("sl"), s), T.px(o.get("tp"), s) if o.get("tp") else "none")


# ------------------------------------------------------------------ ledger (paper + manual)

def ledger_open(o, cfg, price, mode, note=""):
    st = T.paper_state(cfg)
    spec = T.symbol_spec(o["symbol"], cfg)
    pid = st["next_id"]
    st["next_id"] += 1
    pos = {"id": pid, "symbol": T.norm_symbol(o["symbol"]), "side": o["side"], "volume": o["volume"], "entry": price,
           "sl": o.get("sl"), "tp": o.get("tp"), "price": price, "mpp": T.money_per_point(spec), "opened": now_iso(),
           "order_id": o.get("id"), "mode": mode, "setup": o.get("setup"), "rule_break": o.get("rule_break")}
    st["positions"].append(pos)
    T.save_paper(st)
    j = T.journal()
    j["trades"].append({"id": "%s%d" % (mode[0], pid), "position": pid, "order_id": o.get("id"), "mode": mode,
                        "symbol": pos["symbol"], "side": o["side"], "volume": o["volume"], "entry": price,
                        "sl": o.get("sl"), "tp": o.get("tp"), "opened_at": pos["opened"], "status": "open", "note": note,
                        "setup": o.get("setup"), "rule_break": o.get("rule_break")})
    T.save_journal(j)
    return pos


def ledger_close(st, pos, price, why, volume=None):
    vol = min(volume or pos["volume"], pos["volume"])
    sgn = 1 if pos["side"] == "buy" else -1
    pnl = round(sgn * (price - pos["entry"]) * vol * pos.get("mpp", 1), 2)
    st["balance"] = round(st["balance"] + pnl, 2)
    pos["volume"] = round(pos["volume"] - vol, 8)
    if pos["volume"] <= 1e-9:
        st["positions"] = [p for p in st["positions"] if p["id"] != pos["id"]]
    j = T.journal()
    for t in j["trades"]:
        if t.get("position") == pos["id"] and t.get("mode") in ("paper", "manual") and t["status"] == "open":
            risk = abs(t["entry"] - t["sl"]) * vol * pos.get("mpp", 1) if t.get("sl") else None
            rec = dict(exit=price, pnl=pnl, closed_at=now_iso(), status="closed", why=why,
                       pips=T.pips(sgn * (price - t["entry"]), t["symbol"]), r=round(pnl / risk, 2) if risk else None)
            if vol < t["volume"] - 1e-9:
                j["trades"].append(dict(t, volume=vol, id=t["id"] + "-%d" % len(j["trades"]), **rec))
                t["volume"] = round(t["volume"] - vol, 8)
            else:
                t.update(rec)
            break
    T.save_journal(j)
    return pnl


def ledger_sync(cfg):
    """Paper: fill resting orders, apply stops/targets at the current price.
    Manual: only flag positions whose stop or target was probably hit."""
    st = T.paper_state(cfg)
    manual = cfg["mode"] == "manual"
    events, prices = [], {}
    for s in {p["symbol"] for p in st["positions"]} | {o["symbol"] for o in st.get("pending", [])}:
        try:
            prices[s] = T.get_price(s, cfg)["price"]
        except SystemExit:
            pass
    keep = []
    for o in st.get("pending", []):
        p = prices.get(T.norm_symbol(o["symbol"]))
        buy = o["side"] == "buy"
        trig = p is not None and ((o["type"] == "limit" and ((buy and p <= o["entry"]) or (not buy and p >= o["entry"])))
                                  or (o["type"] == "stop" and ((buy and p >= o["entry"]) or (not buy and p <= o["entry"]))))
        if trig:
            T.save_paper(dict(st, pending=keep + [x for x in st["pending"] if x is not o and x not in keep]))
            fill = o["entry"] if o["type"] == "limit" else p
            pos = ledger_open(o, cfg, fill, "paper")
            st = T.paper_state(cfg)
            events.append("paper %s order %s filled at %s (position %s)" % (o["type"], o.get("id"), T.px(fill, o["symbol"]), pos["id"]))
        else:
            keep.append(o)
    st["pending"] = keep
    for pos in list(st["positions"]):
        p = prices.get(pos["symbol"])
        if p is None:
            continue
        pos["price"] = p
        long = pos["side"] == "buy"
        hit = None
        if pos.get("sl") and ((long and p <= pos["sl"]) or (not long and p >= pos["sl"])):
            hit = ("stop", pos["sl"])
        elif pos.get("tp") and ((long and p >= pos["tp"]) or (not long and p <= pos["tp"])):
            hit = ("target", pos["tp"])
        if hit and manual:
            events.append("position %s %s: price %s passed the %s %s. Check the MT5 app; if it closed, record it: "
                          "order.py close %s --price %s --confirm yes" % (pos["id"], pos["symbol"], T.px(p, pos["symbol"]),
                                                                           hit[0], T.px(hit[1], pos["symbol"]), pos["id"],
                                                                           T.px(hit[1], pos["symbol"])))
        elif hit:
            pnl = ledger_close(st, pos, hit[1], hit[0])
            events.append("paper position %s closed at %s %s (%+.2f)" % (pos["id"], hit[0], T.px(hit[1], pos["symbol"]), pnl))
    T.save_paper(st)
    return events


def manual_steps(o, cfg):
    s = T.broker_symbol(o["symbol"], cfg)
    typ = o.get("type", "market")
    kind = {"market": "Market Execution / تنفيذ فوري",
            ("buy", "limit"): "Buy Limit", ("sell", "limit"): "Sell Limit",
            ("buy", "stop"): "Buy Stop", ("sell", "stop"): "Sell Stop"}
    k = kind["market"] if typ == "market" else kind[(o["side"], typ)]
    P = lambda x: T.px(x, o["symbol"])  # noqa: E731
    L = ["Place it in the MetaTrader 5 app:",
         "  1. Quotes (الأسعار) -> tap %s -> Trade (تداول)" % s,
         "  2. Order type: %s" % k]
    if typ != "market":
        L.append("  3. Price: %s" % P(o["entry"]))
    L += ["  %d. Volume: %s" % (4 if typ != "market" else 3, o["volume"]),
          "  %d. Stop Loss: %s    Take Profit: %s" % (5 if typ != "market" else 4, P(o.get("sl")), P(o.get("tp")) if o.get("tp") else "-"),
          "  %d. Press %s" % (6 if typ != "market" else 5,
                              ("Buy by Market / شراء" if o["side"] == "buy" else "Sell by Market / بيع") if typ == "market" else "Place / وضع"),
          "Then tell Claude the fill price (or send a screenshot of the trade) so it is recorded:",
          "  order.py filled %s --price <fill price>" % o.get("id")]
    return "\n".join(L)


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
        T.die("symbol %s not available at your broker (symbol_suffix / symbol_map in trader/config.json)" % sym)
    si = mt5.symbol_info(sym)
    tk = mt5.symbol_info_tick(sym)
    spread = T.pips(tk.ask - tk.bid, o["symbol"])
    if spread > float(cfg.get("max_spread_pips", 3)):
        T.die("spread is %s pips, above max_spread_pips %s: wait for normal liquidity" % (spread, cfg.get("max_spread_pips")))
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
        return {"status": "rejected", "msg": str(info), "request": req}
    st = "filled" if typ == "market" else "pending"
    if st == "filled":
        j = T.journal()
        j["trades"].append({"id": "m%s" % info["deal"], "position": info["order"], "order_id": o.get("id"), "mode": "mt5",
                            "account": ai.login, "symbol": T.norm_symbol(o["symbol"]), "side": o["side"],
                            "volume": info["volume"], "entry": info["price"], "sl": o.get("sl"), "tp": o.get("tp"),
                            "opened_at": now_iso(), "status": "open", "setup": o.get("setup"),
                            "rule_break": o.get("rule_break")})
        T.save_journal(j)
    return {"status": st, "msg": "MT5 %s: retcode %s %s (spread %s pips)" % (st, info["retcode"], info["comment"], spread),
            "result": info, "request": req,
            "account": "%s (%s)" % (ai.login, {0: "demo", 1: "contest", 2: "REAL"}.get(ai.trade_mode))}


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
        if o.get("status") in ("placed", "sent_manual", "cancelled"):
            continue
        v, why = verdict_for(o)
        h = T.order_hash(o)
        if v:
            L.append("READY  %s  ticket %s  %s" % (o["id"], h, ticket_text(o)))
            L += ["       ! " + w for w in v.get("warnings", [])]
            pg = plan_gate(o, cfg, None)
            if pg:
                L.append("       PLAN: " + pg)
        else:
            L.append("NOT READY  %s  %s  -> %s" % (o["id"], ticket_text(o), why))
        out.append({"id": o["id"], "ticket": h if v else None, "ready": bool(v), "why": why, "order": o})
    if len(L) == 1:
        L.append("no orders. Size one with: risk.py size PAIR --entry X --stop Y --target Z --write")
    else:
        L.append("To place: the user replies yes for one ticket; then order.py place ID --ticket CODE --confirm \"<their words>\"")
    T.emit({"tickets": out, "text": "\n".join(L)}, a.json)
    return 0


def cmd_place(a, cfg):
    data = T.load_orders()
    o = find_order(data, a.order_id)
    if o.get("status") in ("placed", "sent_manual"):
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
    pg = plan_gate(o, cfg, a.override_plan)
    if pg:
        T.die("outside your trading plan: %s. If the user still wants it, they must say why; then add "
              "--override-plan \"<their reason>\" (it is recorded as a rule break)." % pg)
    if a.override_plan:
        o["rule_break"] = a.override_plan
    clock = T.market_clock(cfg=cfg)
    mode = cfg["mode"]
    typ = o.get("type", "market")
    if mode == "paper":
        if typ == "market" and not clock["open"]:
            T.die("%s. Use a limit/stop order or place it after %s." % (clock["status"], clock.get("next_open")))
        if typ == "market":
            pos = ledger_open(o, cfg, T.get_price(o["symbol"], cfg)["price"], "paper")
            res = {"status": "filled", "position": pos["id"], "msg": "paper fill at %s (position %s)" % (
                T.px(pos["entry"], o["symbol"]), pos["id"])}
        else:
            st = T.paper_state(cfg)
            st.setdefault("pending", []).append(dict(o, placed=now_iso()))
            T.save_paper(st)
            res = {"status": "pending", "msg": "paper %s order resting at %s" % (typ, T.px(o["entry"], o["symbol"]))}
        new_status = "placed"
    elif mode == "manual":
        res = {"status": "sent_manual", "msg": manual_steps(o, cfg)}
        if not clock["open"] and typ == "market":
            res["msg"] = "note: %s; a market order can only be placed after %s\n" % (clock["status"], clock.get("next_open")) + res["msg"]
        new_status = "sent_manual"
    elif mode == "mt5":
        res = mt5_place(o, cfg)
        new_status = "placed"
    else:
        T.die("unknown mode %r (paper, manual or mt5)" % mode)
    ok = res["status"] in ("filled", "pending", "sent_manual")
    if ok:
        o["status"], o["placed_at"] = new_status, now_iso()
        T.save_orders(data)
    log_exec({"at": now_iso(), "mode": mode, "order": o, "ticket": h, "user_confirmation": a.confirm,
              "plan_override": a.override_plan, "result": res})
    head = {"sent_manual": "APPROVED - PLACE IT YOURSELF", "filled": "PLACED", "pending": "PLACED"}.get(res["status"], "NOT PLACED")
    T.emit(dict(res, text="%s  %s  ticket %s\n%s" % (head, ticket_text(o), h, res["msg"])), a.json)
    return 0 if ok else 1


def cmd_filled(a, cfg):
    """Manual mode: the user placed the order in the app; record the fill."""
    data = T.load_orders()
    o = find_order(data, a.order_id)
    if o.get("status") != "sent_manual":
        T.die("order %s is not waiting for a manual fill (status %s)" % (o["id"], o.get("status")))
    o2 = dict(o, volume=a.volume or o["volume"], sl=a.sl if a.sl is not None else o.get("sl"),
              tp=a.tp if a.tp is not None else o.get("tp"))
    pos = ledger_open(o2, cfg, a.price, "manual", note="recorded from the MT5 app")
    o["status"], o["filled_at"] = "placed", now_iso()
    T.save_orders(data)
    log_exec({"at": now_iso(), "mode": "manual", "filled": o["id"], "price": a.price, "position": pos["id"]})
    diff = T.pips(abs(a.price - o["entry"]), o["symbol"]) if o.get("entry") else None
    T.emit({"position": pos, "text": "recorded position %s: %s %s lots %s at %s%s" % (
        pos["id"], o2["side"], o2["volume"], pos["symbol"], T.px(a.price, pos["symbol"]),
        " (%s pips from the planned entry)" % diff if diff else "")}, a.json)
    return 0


def cmd_positions(a, cfg):
    events = ledger_sync(cfg) if cfg["mode"] in ("paper", "manual") else []
    acct = T.account_snapshot(cfg)
    L = ["POSITIONS  %s  balance %.2f equity %.2f %s" % (
        acct["mode"] + (" " + acct["trade_mode"] if acct["mode"] == "mt5" else ""), acct["balance"], acct["equity"],
        acct["currency"])]
    L += ["event: " + e for e in events]
    for p in acct["positions"]:
        s = p["symbol"]
        cur = p.get("price") or p["entry"]
        L.append("  #%s %s %s %s @%s  now %s (%+.0f pips)  SL %s TP %s  P&L %+.2f" % (
            p["id"], p["side"], p["volume"], s, T.px(p["entry"], s), T.px(cur, s),
            T.pips((cur - p["entry"]) * (1 if p["side"] == "buy" else -1), s), T.px(p.get("sl"), s), T.px(p.get("tp"), s),
            p.get("profit", 0)))
    for o in acct.get("pending", []):
        L.append("  pending %s %s %s %s @%s" % (o.get("id"), o["side"], o["volume"], o["symbol"], T.px(o["entry"], o["symbol"])))
    waiting = [o for o in T.load_orders()["orders"] if o.get("status") == "sent_manual"]
    for o in waiting:
        L.append("  waiting for you to place in the app: %s  %s" % (o["id"], ticket_text(o)))
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
    s = p["symbol"]
    long = p["side"] == "buy"
    if a.sl is not None and p.get("sl") and ((long and a.sl < p["sl"]) or (not long and a.sl > p["sl"])):
        T.die("refusing to loosen the stop (%s -> %s). Stops only move in the trade's favour." % (
            T.px(p["sl"], s), T.px(a.sl, s)))
    cur = p.get("price")
    if a.sl is not None and cur and ((long and a.sl >= cur) or (not long and a.sl <= cur)):
        T.die("stop %s is through the current price %s; close the position instead" % (T.px(a.sl, s), T.px(cur, s)))
    if cfg["mode"] in ("paper", "manual"):
        st = T.paper_state(cfg)
        for q in st["positions"]:
            if str(q["id"]) == str(a.position):
                q["sl"] = a.sl if a.sl is not None else q.get("sl")
                q["tp"] = a.tp if a.tp is not None else q.get("tp")
        T.save_paper(st)
        msg = "position %s: SL %s TP %s" % (a.position, T.px(a.sl if a.sl is not None else p.get("sl"), s),
                                           T.px(a.tp if a.tp is not None else p.get("tp"), s))
        if cfg["mode"] == "manual":
            msg += "\nIn the MT5 app: Trade (تداول) -> long-press the %s position -> Modify (تعديل) -> set the same SL/TP -> Modify" % s
        ok = True
    else:
        mt5, _ = mt5_guard(cfg)
        req = {"action": mt5.TRADE_ACTION_SLTP, "position": int(a.position), "symbol": p.get("broker_symbol", s),
               "sl": float(a.sl if a.sl is not None else (p.get("sl") or 0.0)),
               "tp": float(a.tp if a.tp is not None else (p.get("tp") or 0.0))}
        ok, info = mt5_send(mt5, req)
        msg = str(info)
    log_exec({"at": now_iso(), "mode": cfg["mode"], "modify": a.position, "sl": a.sl, "tp": a.tp,
              "user_confirmation": a.confirm, "ok": ok, "result": msg})
    T.emit({"ok": ok, "text": ("MODIFIED " if ok else "FAILED ") + msg}, a.json)
    return 0 if ok else 1


def cmd_close(a, cfg):
    if not affirmative(a.confirm):
        T.die("--confirm is not a clear yes")
    acct = T.account_snapshot(cfg)
    p = _find_position(acct, a.position)
    s = p["symbol"]
    if cfg["mode"] in ("paper", "manual"):
        if cfg["mode"] == "manual" and a.price is None:
            T.die("manual mode: close the trade in the MT5 app first, then give its close price with --price")
        st = T.paper_state(cfg)
        pos = next(q for q in st["positions"] if str(q["id"]) == str(a.position))
        price = a.price if a.price is not None else T.get_price(s, cfg)["price"]
        pnl = ledger_close(st, pos, price, "manual close" if cfg["mode"] == "manual" else "closed", a.volume)
        T.save_paper(st)
        ok, msg = True, "%s close of %s at %s, P&L %+.2f %s" % (cfg["mode"], s, T.px(price, s), pnl, cfg["currency"])
    else:
        mt5, _ = mt5_guard(cfg)
        bs = p.get("broker_symbol", s)
        si = mt5.symbol_info(bs)
        tk = mt5.symbol_info_tick(bs)
        long = p["side"] == "buy"
        req = {"action": mt5.TRADE_ACTION_DEAL, "position": int(a.position), "symbol": bs,
               "volume": float(a.volume or p["volume"]), "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY,
               "price": tk.bid if long else tk.ask, "deviation": 20, "magic": int(cfg.get("magic", 2126)),
               "comment": "tadawal close", "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling(mt5, si)}
        ok, info = mt5_send(mt5, req)
        msg = str(info)
    log_exec({"at": now_iso(), "mode": cfg["mode"], "close": a.position, "volume": a.volume, "price": a.price,
              "user_confirmation": a.confirm, "ok": ok, "result": msg})
    T.emit({"ok": ok, "text": ("CLOSED " if ok else "FAILED ") + msg}, a.json)
    return 0 if ok else 1


def cmd_cancel(a, cfg):
    data = T.load_orders()
    o = find_order(data, a.order_id)
    msg = "order %s cancelled" % o["id"]
    if o.get("status") == "sent_manual":
        msg += " (if you already placed it in the app, delete it there too)"
    elif o.get("status") == "placed":
        if cfg["mode"] in ("paper", "manual"):
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
    log_exec({"at": now_iso(), "mode": cfg["mode"], "cancel": o["id"]})
    T.emit({"text": msg}, a.json)
    return 0


def cmd_account(a, cfg):
    acct = T.account_snapshot(cfg)
    where = {"mt5": "MT5 account %s on %s (%s)" % (acct.get("account"), acct.get("server"), acct.get("trade_mode")),
             "manual": "manual: you place orders in the MT5 app, this keeps the record",
             "paper": "local paper account (simulation)"}.get(acct["mode"], acct["mode"])
    txt = "mode %s | %s | balance %.2f equity %.2f %s | live trading %s" % (
        cfg["mode"], where, acct["balance"], acct["equity"], acct["currency"],
        "ALLOWED" if cfg.get("allow_live") else "blocked (MT5 real accounts refused)")
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
    p.add_argument("--confirm", required=True, help="the user's own words, e.g. 'yes' / 'نعم'")
    p.add_argument("--override-plan", help="the user's reason for trading outside their plan (logged)")
    f = sub.add_parser("filled", help="manual mode: record the fill from the app")
    f.add_argument("order_id")
    f.add_argument("--price", type=float, required=True)
    f.add_argument("--volume", type=float)
    f.add_argument("--sl", type=float)
    f.add_argument("--tp", type=float)
    sub.add_parser("positions")
    m = sub.add_parser("modify")
    m.add_argument("position")
    m.add_argument("--sl", type=float)
    m.add_argument("--tp", type=float)
    m.add_argument("--confirm", required=True)
    c = sub.add_parser("close")
    c.add_argument("position")
    c.add_argument("--volume", type=float)
    c.add_argument("--price", type=float, help="manual mode: the close price from the app")
    c.add_argument("--confirm", required=True)
    x = sub.add_parser("cancel")
    x.add_argument("order_id")
    sub.add_parser("account")
    a = ap.parse_args(argv)
    cfg = T.load_config()
    return {"tickets": cmd_tickets, "place": cmd_place, "filled": cmd_filled, "positions": cmd_positions,
            "modify": cmd_modify, "close": cmd_close, "cancel": cmd_cancel, "account": cmd_account}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
