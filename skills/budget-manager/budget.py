#!/usr/bin/env python3
"""Budget Manager: how your capital is split, and how your trading is really doing.

    python3 budget.py status                          # cash, invested, per-position share, limits left
    python3 budget.py plan --capital 10000 --positions 5 --reserve 20
    python3 budget.py report                          # win rate, profit factor, expectancy, drawdown
    python3 budget.py report --from 2026-10-01 --by symbol
    python3 budget.py journal                         # list trades (open and closed)
    python3 budget.py journal-add AAPL buy 10 190 --exit 198 --opened 2026-10-01 --closed 2026-10-08 --sl 185
    python3 budget.py sync --days 90                  # import closed trades from MT5 history
    python3 budget.py deposit 500 | withdraw 200      # paper account cash

The journal is trader/journal.json. Paper fills from the order executor are
recorded automatically; MT5 trades come in with `sync`.
"""
import argparse
import collections
import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402


def stats(trades):
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    closed.sort(key=lambda t: str(t.get("closed_at")))
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    eq, peak, mdd, streak, worst_streak = 0.0, 0.0, 0.0, 0, 0
    for t in closed:
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        streak = streak + 1 if t["pnl"] <= 0 else 0
        worst_streak = max(worst_streak, streak)
    rs = [t["r"] for t in closed if t.get("r") is not None]
    aw = gw / len(wins) if wins else 0.0
    al = -gl / len(losses) if losses else 0.0
    return {"trades": len(closed), "net_pnl": round(gw - gl, 2), "wins": len(wins), "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "payoff": round(aw / -al, 2) if al else None,
            "profit_factor": round(gw / gl, 2) if gl else (None if not gw else float("inf")),
            "expectancy": round((gw - gl) / len(closed), 2) if closed else 0.0,
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "max_drawdown": round(mdd, 2), "max_losing_streak": worst_streak,
            "best": max((t["pnl"] for t in closed), default=0.0), "worst": min((t["pnl"] for t in closed), default=0.0)}


def group(trades, key):
    g = collections.OrderedDict()
    for t in sorted(trades, key=lambda t: str(t.get("closed_at"))):
        if t.get("status") != "closed":
            continue
        k = t["symbol"] if key == "symbol" else str(t.get("closed_at", ""))[:7]
        g.setdefault(k, []).append(t)
    return {k: stats(v) for k, v in g.items()}


def cmd_status(a, cfg):
    acct = T.account_snapshot(cfg)
    bal, eq = acct["balance"], acct["equity"]
    invested = 0.0
    rows = []
    for p in acct["positions"]:
        spec = T.symbol_spec(p["symbol"], cfg)
        val = p["volume"] * spec["contract_size"] * (p.get("price") or p["entry"])
        invested += val
        rows.append((p, val))
    L = ["BUDGET  %s  balance %.2f  equity %.2f %s" % (acct["mode"], bal, eq, acct["currency"])]
    for p, val in rows:
        L.append("  %-6s %s %s  value %.2f = %.1f%% of equity%s" % (
            p["symbol"], p["side"], p["volume"], val, val / eq * 100 if eq else 0,
            "  ** over max_position_pct **" if eq and val / eq * 100 > cfg["max_position_pct"] else ""))
    cash = eq - invested if acct["mode"] == "paper" else acct.get("free_margin", eq - invested)
    L.append("invested %.2f (%.1f%%) | %s %.2f" % (invested, invested / eq * 100 if eq else 0,
                                                  "cash" if acct["mode"] == "paper" else "free margin", cash))
    today = T.realized_today(cfg)
    j = T.journal()["trades"]
    week0 = (T.ny_now().date() - dt.timedelta(days=T.ny_now().weekday())).isoformat()
    month0 = T.ny_now().date().replace(day=1).isoformat()
    wk = sum(t.get("pnl") or 0 for t in j if t.get("status") == "closed" and str(t.get("closed_at", ""))[:10] >= week0)
    mo = sum(t.get("pnl") or 0 for t in j if t.get("status") == "closed" and str(t.get("closed_at", ""))[:10] >= month0)
    lim = bal * cfg["max_daily_loss_pct"] / 100
    L.append("realized: today %+.2f | this week %+.2f | this month %+.2f" % (today, wk, mo))
    L.append("daily loss room left: %.2f of %.2f" % (max(0.0, lim + min(today, 0)), lim))
    L.append("slots: %d of %d positions used | risk per new trade %.2f (%.1f%%)" % (
        len(acct["positions"]), cfg["max_open_positions"], bal * cfg["risk_per_trade_pct"] / 100, cfg["risk_per_trade_pct"]))
    T.emit({"account": acct, "invested": invested, "realized_today": today, "week": wk, "month": mo,
            "text": "\n".join(L)}, a.json)
    return 0


def cmd_plan(a, cfg):
    cap = a.capital or T.account_snapshot(cfg)["balance"]
    usable = cap * (1 - a.reserve / 100)
    per = usable / a.positions
    cap_pct = min(per / cap * 100, cfg["max_position_pct"])
    per = cap * cap_pct / 100
    risk = cap * cfg["risk_per_trade_pct"] / 100
    L = ["BUDGET PLAN  capital %.2f" % cap,
         "cash reserve %.0f%% = %.2f (never invested; covers drawdowns and opportunities)" % (a.reserve, cap * a.reserve / 100),
         "up to %d positions x %.2f each (%.1f%% of capital; cap %s%%)" % (a.positions, per, cap_pct, cfg["max_position_pct"]),
         "risk per trade %.2f (%.1f%%): the most one stop-out may cost" % (risk, cfg["risk_per_trade_pct"]),
         "worst case if every position stops out: %.2f (%.1f%%)" % (risk * a.positions, risk * a.positions / cap * 100),
         "daily loss limit %.2f (%.1f%%): stop trading for the day when hit" % (cap * cfg["max_daily_loss_pct"] / 100,
                                                                               cfg["max_daily_loss_pct"])]
    if a.prices:
        L.append("max size at your per-position budget:")
        for s in a.prices:
            sym, px = s.split("=")
            L.append("  %s @ %s -> %d shares" % (sym.upper(), px, int(per // float(px))))
    if risk * a.positions / cap * 100 > cfg.get("max_total_risk_pct", 6):
        L.append("warning: all positions at full risk exceed max_total_risk_pct %s%%" % cfg.get("max_total_risk_pct", 6))
    T.emit({"capital": cap, "per_position": per, "risk_per_trade": risk, "text": "\n".join(L)}, a.json)
    return 0


def in_range(t, a):
    d = str(t.get("closed_at") or t.get("opened_at") or "")[:10]
    return (not a.date_from or d >= a.date_from) and (not a.date_to or d <= a.date_to)


def cmd_report(a, cfg):
    trades = [t for t in T.journal()["trades"] if in_range(t, a)]
    s = stats(trades)
    L = ["PERFORMANCE  %s .. %s" % (a.date_from or "start", a.date_to or "now"),
         "closed trades %d | net %+.2f | win rate %s%% (%d W / %d L)" % (s["trades"], s["net_pnl"], s["win_rate_pct"], s["wins"], s["losses"]),
         "avg win %.2f | avg loss %.2f | payoff %s | profit factor %s" % (s["avg_win"], s["avg_loss"], s["payoff"], s["profit_factor"]),
         "expectancy %+.2f per trade | avg R %s | max drawdown %.2f | longest losing streak %d" % (
             s["expectancy"], s["avg_r"], s["max_drawdown"], s["max_losing_streak"]),
         "best %+.2f | worst %+.2f" % (s["best"], s["worst"])]
    if s["trades"] and s["profit_factor"] is not None and s["profit_factor"] != float("inf") and s["profit_factor"] < 1:
        L.append("note: profit factor below 1 means the method loses money so far: review before adding size")
    if s["trades"] < 20:
        L.append("note: fewer than 20 closed trades: the numbers are not stable yet")
    by = group(trades, a.by) if a.by else {}
    if by:
        L.append("")
        L.append("by %s:" % a.by)
        for k, v in by.items():
            L.append("  %-8s trades %3d  net %+9.2f  win %5s%%  PF %s" % (k, v["trades"], v["net_pnl"], v["win_rate_pct"], v["profit_factor"]))
    open_t = [t for t in trades if t.get("status") == "open"]
    if open_t:
        L.append("open trades: %d (%s)" % (len(open_t), ", ".join(t["symbol"] for t in open_t)))
    T.emit({"stats": s, "by": by, "text": "\n".join(L)}, a.json)
    return 0


def cmd_journal(a, cfg):
    j = T.journal()["trades"]
    L = ["%-10s %-6s %-4s %8s %10s %10s %10s %s" % ("id", "symbol", "side", "volume", "entry", "exit", "pnl", "status")]
    for t in j[-a.last:]:
        L.append("%-10s %-6s %-4s %8s %10s %10s %10s %s %s" % (
            t.get("id"), t["symbol"], t["side"], t["volume"], t["entry"], t.get("exit", ""), t.get("pnl", ""),
            t.get("status"), str(t.get("closed_at") or t.get("opened_at") or "")[:10]))
    T.emit({"trades": j[-a.last:], "text": "\n".join(L)}, a.json)
    return 0


def cmd_journal_add(a, cfg):
    j = T.journal()
    t = {"id": "j%d" % (len(j["trades"]) + 1), "mode": "manual", "symbol": a.symbol.upper(), "side": a.side,
         "volume": a.volume, "entry": a.entry, "sl": a.sl, "tp": a.tp, "opened_at": a.opened or T.ny_now().date().isoformat(),
         "status": "open", "note": a.note}
    if a.exit is not None:
        sgn = 1 if a.side == "buy" else -1
        t["exit"] = a.exit
        t["pnl"] = round(sgn * (a.exit - a.entry) * a.volume - (a.fees or 0), 2)
        t["closed_at"] = a.closed or T.ny_now().date().isoformat()
        t["status"] = "closed"
        if a.sl:
            t["r"] = round(t["pnl"] / (abs(a.entry - a.sl) * a.volume), 2)
    j["trades"].append(t)
    T.save_journal(j)
    T.emit({"trade": t, "text": "added %s" % t["id"]}, a.json)
    return 0


def cmd_sync(a, cfg):
    mt5 = T.mt5_connect(cfg, required=True)
    since = dt.datetime.utcnow() - dt.timedelta(days=a.days)
    deals = mt5.history_deals_get(since, dt.datetime.utcnow() + dt.timedelta(days=1)) or []
    by_pos = collections.defaultdict(list)
    for d in deals:
        if d.position_id and d.type in (0, 1):
            by_pos[d.position_id].append(d)
    j = T.journal()
    known = {t.get("position") for t in j["trades"] if t.get("mode") == "mt5" and t.get("status") == "closed"}
    added = 0
    for pid, ds in by_pos.items():
        ins = [d for d in ds if d.entry == 0]
        outs = [d for d in ds if d.entry in (1, 3)]
        if not ins or not outs or pid in known:
            continue
        e = ins[0]
        vol = sum(d.volume for d in outs)
        exit_px = sum(d.price * d.volume for d in outs) / vol
        pnl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in ds)
        rec = None
        for t in j["trades"]:
            if t.get("mode") == "mt5" and t.get("status") == "open" and t.get("position") == pid:
                rec = t
        if rec is None:
            rec = {"id": "m%d" % pid, "position": pid, "mode": "mt5", "symbol": e.symbol, "side": "buy" if e.type == 0 else "sell",
                   "volume": e.volume, "entry": e.price, "opened_at": dt.datetime.utcfromtimestamp(e.time).isoformat()}
            j["trades"].append(rec)
        risk = abs(rec["entry"] - rec["sl"]) * vol * T.money_per_point(T.symbol_spec(rec["symbol"], cfg)) if rec.get("sl") else None
        rec.update(exit=round(exit_px, 5), pnl=round(pnl, 2), status="closed",
                   closed_at=dt.datetime.utcfromtimestamp(outs[-1].time).isoformat(), r=round(pnl / risk, 2) if risk else None)
        added += 1
    T.save_journal(j)
    T.emit({"added": added, "text": "synced %d closed MT5 trades from the last %d days" % (added, a.days)}, a.json)
    return 0


def cmd_cash(a, cfg, sign):
    if cfg["mode"] != "paper":
        T.die("deposits/withdrawals only apply to the paper account; MT5 balances come from your broker")
    st = T.paper_state(cfg)
    st["balance"] = round(st["balance"] + sign * a.amount, 2)
    st.setdefault("cash_moves", []).append({"at": T.ny_now().isoformat(timespec="seconds"), "amount": sign * a.amount})
    T.save_paper(st)
    T.emit({"balance": st["balance"], "text": "paper balance now %.2f" % st["balance"]}, a.json)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("plan")
    p.add_argument("--capital", type=float)
    p.add_argument("--positions", type=int, default=5)
    p.add_argument("--reserve", type=float, default=20.0, help="%% kept in cash")
    p.add_argument("--prices", nargs="*", help="SYMBOL=PRICE pairs to show max shares")
    r = sub.add_parser("report")
    r.add_argument("--from", dest="date_from")
    r.add_argument("--to", dest="date_to")
    r.add_argument("--by", choices=["symbol", "month"])
    jl = sub.add_parser("journal")
    jl.add_argument("--last", type=int, default=30)
    ja = sub.add_parser("journal-add")
    ja.add_argument("symbol")
    ja.add_argument("side", choices=["buy", "sell"])
    ja.add_argument("volume", type=float)
    ja.add_argument("entry", type=float)
    ja.add_argument("--exit", type=float)
    ja.add_argument("--sl", type=float)
    ja.add_argument("--tp", type=float)
    ja.add_argument("--fees", type=float)
    ja.add_argument("--opened")
    ja.add_argument("--closed")
    ja.add_argument("--note", default="")
    s = sub.add_parser("sync")
    s.add_argument("--days", type=int, default=90)
    for name in ("deposit", "withdraw"):
        sub.add_parser(name).add_argument("amount", type=float)
    a = ap.parse_args(argv)
    cfg = T.load_config()
    cfg.setdefault("max_total_risk_pct", 6.0)
    if a.cmd in ("deposit", "withdraw"):
        return cmd_cash(a, cfg, 1 if a.cmd == "deposit" else -1)
    return {"status": cmd_status, "plan": cmd_plan, "report": cmd_report, "journal": cmd_journal,
            "journal-add": cmd_journal_add, "sync": cmd_sync}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
