#!/usr/bin/env python3
"""Trading Plan: write your rules once, then every trade is checked against them.

    python3 plan.py init --style day              # templates: scalp | day | swing | smc
    python3 plan.py show
    python3 plan.py set pairs EURUSD GBPUSD XAUUSD
    python3 plan.py set max_trades_per_day 2
    python3 plan.py setup add "london-breakout" "Asian range break at London open, retest, H1 close"
    python3 plan.py today                         # can I trade now? trades left, streak, news blackout
    python3 plan.py check o1                      # IN PLAN / OUT OF PLAN / NEEDS CHECKLIST
    python3 plan.py check o1 --checklist yes,yes,yes,yes,yes

The plan lives in trader/plan.json. Orders carry the setup name (risk.py size
... --setup trend-pullback). Verdicts are stored per exact order contents in
trader/plan_verdicts.json; when "enforce" is true the order executor refuses
an order that is not IN PLAN unless the user overrides it with a reason, and
the trade coach counts every override as a rule break.
"""
import argparse
import datetime as dt
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

CHECKLIST = [
    "Higher-timeframe trend or bias agrees with the trade",
    "Price is at a level or zone I marked before (not chasing)",
    "My entry trigger has CLOSED (candle / break of structure)",
    "No high-impact news for these currencies in the blackout window",
    "I am calm: not trying to win back a loss, not bored, not FOMO",
]

TEMPLATES = {
    "scalp": {
        "pairs": ["EURUSD", "GBPUSD", "USDJPY"], "sessions": ["London", "New York"],
        "timeframes": {"bias": "H1", "entry": "M5"}, "stop_pips": [5, 15], "min_rr": 1.5,
        "max_trades_per_day": 5, "max_losses_in_a_row": 3,
        "setups": {"session-open-breakout": "Break of the Asian range in the first hour of London or New York, entry on retest",
                   "ema-pullback": "M5 trend with H1, pullback to EMA20, bullish/bearish rejection candle"}},
    "day": {
        "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"], "sessions": ["London", "New York"],
        "timeframes": {"bias": "H4", "entry": "H1"}, "stop_pips": [15, 50], "min_rr": 2.0,
        "max_trades_per_day": 3, "max_losses_in_a_row": 2,
        "setups": {"trend-pullback": "H4 trend, H1 pullback to EMA20/50 or a marked level, confirmation candle closed",
                   "breakout-retest": "H1 close beyond a marked level, entry on the retest, stop behind the level",
                   "range-reversal": "Clear H4 range, rejection at the edge with RSI divergence, target the middle/other edge"}},
    "swing": {
        "pairs": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD"], "sessions": [],
        "timeframes": {"bias": "D1", "entry": "H4"}, "stop_pips": [40, 200], "min_rr": 2.0,
        "max_trades_per_day": 2, "max_losses_in_a_row": 3,
        "setups": {"d1-trend-pullback": "D1 trend, H4 pullback into a D1 level, H4 confirmation",
                   "weekly-level-reaction": "Rejection of a weekly level with a D1 reversal candle",
                   "fundamental-divergence": "Economic bias + COT agree, enter on an H4 pullback"}},
    "smc": {
        "pairs": ["EURUSD", "GBPUSD", "XAUUSD"], "sessions": ["London", "New York"],
        "timeframes": {"bias": "H4", "entry": "M15"}, "stop_pips": [10, 40], "min_rr": 3.0,
        "max_trades_per_day": 2, "max_losses_in_a_row": 2,
        "setups": {"sweep-choch-ob": "Liquidity sweep, CHoCH on M15, entry at the order block that caused it",
                   "fvg-in-discount": "H4 bullish structure, price in discount, entry at an unfilled M15 FVG (mirror for sells)",
                   "bos-ob-continuation": "BOS in the H4 direction, entry on the return to the fresh order block"}},
}
COMMON = {"max_weekly_loss_pct": 6.0, "news_blackout_minutes": [30, 15], "friday_cutoff_ny": "12:00",
          "sunday_open_wait_minutes": 60, "checklist": CHECKLIST, "enforce": True}
PASS_MAX_AGE_MIN = 60


def plan_path():
    return T.tpath("plan.json")


def load_plan(required=True):
    p = T.load_json(plan_path(), None)
    if p is None and required:
        T.die("no trading plan yet: run plan.py init --style scalp|day|swing|smc")
    return p


def forex_day_start(now=None):
    now = (now or T.ny_now()).astimezone(T.NY)
    start = now.replace(hour=17, minute=0, second=0, microsecond=0)
    return start if now >= start else start - dt.timedelta(days=1)


def _ts(s):
    try:
        d = dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=T.NY)


def day_stats(journal, orders, now=None):
    """Trades taken and the losing streak in the current forex day, realized P&L this week."""
    start = forex_day_start(now)
    week_start = start - dt.timedelta(days=(start.weekday() + 1) % 7)  # Sunday 17:00 New York
    taken, closed_today, week_pnl = 0, [], 0.0
    for t in journal:
        o = _ts(t.get("opened_at"))
        if o and o >= start:
            taken += 1
        c = _ts(t.get("closed_at"))
        if t.get("status") == "closed" and c:
            if c >= start:
                closed_today.append((c, t))
            if c >= week_start:
                week_pnl += float(t.get("pnl") or 0)
    taken += sum(1 for o in orders if o.get("status") == "sent_manual" and (_ts(o.get("placed_at")) or start) >= start)
    streak = 0
    for _, t in sorted(closed_today, key=lambda x: x[0], reverse=True):
        if float(t.get("pnl") or 0) < 0:
            streak += 1
        else:
            break
    return {"trades_today": taken, "losses_in_a_row": streak, "week_pnl": round(week_pnl, 2)}


def news_blocks(symbol, plan, now=None):
    cal = T.load_json(T.tpath("calendar.json"), None)
    if not cal:
        return None
    before, after = plan.get("news_blackout_minutes", [30, 15])
    ccys = set(T.split_pair(symbol))
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for e in cal.get("events", []):
        if e.get("impact") != "High" or e.get("currency") not in ccys:
            continue
        t = _ts(e.get("time"))
        if t is None:
            continue
        mins = (t - now).total_seconds() / 60
        if -after <= mins <= before:
            out.append("%s %s %s" % (e["currency"], e["title"], "in %d min" % mins if mins >= 0 else "%d min ago" % -mins))
    return out


def evaluate(order, plan, clock, stats, balance, news, checklist=None):
    """Return (verdict, reasons, notes). Pure given its inputs."""
    R, N = [], []
    sym = T.norm_symbol(order.get("symbol"))
    if plan.get("pairs") and sym not in plan["pairs"]:
        R.append("%s is not one of your plan pairs (%s)" % (sym, ", ".join(plan["pairs"])))
    setup = order.get("setup")
    if not setup:
        R.append("no setup named: which of your setups is this? (%s)" % ", ".join(plan.get("setups", {})))
    elif setup not in plan.get("setups", {}):
        R.append("setup %r is not in your plan (%s)" % (setup, ", ".join(plan.get("setups", {}))))
    if order.get("entry") and order.get("sl"):
        sp = T.pips(abs(float(order["entry"]) - float(order["sl"])), sym)
        lo, hi = plan.get("stop_pips", [0, 1e9])
        if not lo <= sp <= hi:
            R.append("stop is %s pips; your plan says %s-%s for this style" % (sp, lo, hi))
        if order.get("tp"):
            rr = abs(float(order["tp"]) - float(order["entry"])) / abs(float(order["entry"]) - float(order["sl"]))
            if rr < float(plan.get("min_rr", 0)) - 1e-9:
                R.append("reward:risk %.2f, your plan needs %.1f" % (rr, plan["min_rr"]))
        elif plan.get("min_rr"):
            R.append("no take-profit: your plan needs at least %.1fR" % plan["min_rr"])
    if stats["trades_today"] >= int(plan.get("max_trades_per_day", 99)):
        R.append("already %d trades today, your limit is %d" % (stats["trades_today"], plan["max_trades_per_day"]))
    if stats["losses_in_a_row"] >= int(plan.get("max_losses_in_a_row", 99)):
        R.append("%d losses in a row today: your plan says stop for the day" % stats["losses_in_a_row"])
    if balance and stats["week_pnl"] <= -balance * float(plan.get("max_weekly_loss_pct", 100)) / 100:
        R.append("weekly loss %.2f hit your %.1f%% limit: no new trades until next week" % (
            stats["week_pnl"], plan["max_weekly_loss_pct"]))
    market = order.get("type", "market") == "market"
    allowed = plan.get("sessions") or []
    if clock.get("open") and allowed and not set(allowed) & set(clock.get("sessions", [])):
        msg = "outside your sessions (%s); active now: %s" % (", ".join(allowed), ", ".join(clock.get("sessions") or ["none"]))
        (R if market else N).append(msg)
    t_ny = _ts(clock.get("now_iso")) if clock.get("now_iso") else T.ny_now()
    cut = plan.get("friday_cutoff_ny")
    if cut and t_ny.weekday() == 4 and t_ny.strftime("%H:%M") >= cut:
        R.append("Friday after %s New York: your plan avoids new trades before the weekend" % cut)
    wait = int(plan.get("sunday_open_wait_minutes") or 0)
    if wait and t_ny.weekday() == 6 and t_ny.hour * 60 + t_ny.minute < 17 * 60 + wait:
        R.append("first %d minutes after the Sunday open: spreads are wide, your plan waits" % wait)
    if news:
        R.append("news blackout: " + "; ".join(news))
    elif news is None:
        N.append("news not checked (run economic-analyst calendar)")
    items = plan.get("checklist") or []
    if R:
        return "OUT OF PLAN", R, N
    if items:
        if not checklist:
            return "NEEDS CHECKLIST", ["answer the checklist with the user"], N
        if len(checklist) != len(items):
            return "NEEDS CHECKLIST", ["%d answers for %d checklist items" % (len(checklist), len(items))], N
        no = [items[k] for k, ans in enumerate(checklist) if not ans]
        if no:
            return "OUT OF PLAN", ["checklist: " + x for x in no], N
    return "IN PLAN", [], N


def parse_answers(text):
    out = []
    for x in str(text).split(","):
        x = x.strip().lower()
        if x in ("y", "yes", "نعم", "اي", "ايوه", "1", "true", "ok", "✓"):
            out.append(True)
        elif x in ("n", "no", "لا", "0", "false", "x"):
            out.append(False)
        else:
            T.die("checklist answers must be yes/no separated by commas, got %r" % x)
    return out


def render_plan(p):
    L = ["TRADING PLAN  style %s  (enforce: %s)" % (p.get("style"), p.get("enforce")),
         "pairs: %s" % ", ".join(p.get("pairs") or ["any"]),
         "sessions: %s" % ", ".join(p.get("sessions") or ["any"]),
         "timeframes: bias %s, entry %s" % (p["timeframes"]["bias"], p["timeframes"]["entry"]),
         "stop: %s-%s pips | min reward:risk %s" % (p["stop_pips"][0], p["stop_pips"][1], p["min_rr"]),
         "max %s trades a day | stop after %s losses in a row | weekly loss limit %s%%" % (
             p["max_trades_per_day"], p["max_losses_in_a_row"], p["max_weekly_loss_pct"]),
         "news blackout: %s min before, %s min after high-impact news" % tuple(p["news_blackout_minutes"]),
         "no new trades Friday after %s New York; wait %s min after the Sunday open" % (
             p.get("friday_cutoff_ny") or "-", p.get("sunday_open_wait_minutes") or 0),
         "setups:"]
    L += ["  - %s: %s" % (k, v) for k, v in p.get("setups", {}).items()]
    L.append("checklist before every trade:")
    L += ["  %d. %s" % (i + 1, x) for i, x in enumerate(p.get("checklist", []))]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--style", choices=sorted(TEMPLATES), default="day")
    i.add_argument("--force", action="store_true")
    sub.add_parser("show")
    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("values", nargs="+")
    su = sub.add_parser("setup")
    su.add_argument("action", choices=["add", "remove"])
    su.add_argument("name")
    su.add_argument("description", nargs="?", default="")
    sub.add_parser("today")
    c = sub.add_parser("check")
    c.add_argument("order_ids", nargs="*")
    c.add_argument("--checklist", help="answers in order, e.g. yes,yes,yes,no,yes")
    a = ap.parse_args(argv)
    cfg = T.load_config()

    if a.cmd == "init":
        if os.path.exists(plan_path()) and not a.force:
            T.die("a plan already exists (plan.py show). Use --force to replace it.")
        p = dict(COMMON, **json.loads(json.dumps(TEMPLATES[a.style])), style=a.style,
                 created=T.ny_now().isoformat(timespec="seconds"))
        T.save_json(plan_path(), p)
        T.emit(dict(plan=p, text="plan created from the %s template; adjust it with the user:\n%s" % (
            a.style, render_plan(p))), a.json)
        return 0
    p = load_plan()
    if a.cmd == "show":
        T.emit(dict(plan=p, text=render_plan(p)), a.json)
        return 0
    if a.cmd == "set":
        k, vals = a.key, a.values
        if k in ("pairs",):
            p[k] = [T.norm_symbol(v) for v in vals]
        elif k == "sessions":
            names = {"london": "London", "newyork": "New York", "ny": "New York", "tokyo": "Tokyo", "sydney": "Sydney"}
            p[k] = [names[v.lower().replace(" ", "")] for v in vals if v.lower() != "any"]
        elif k in ("stop_pips", "news_blackout_minutes"):
            p[k] = [float(v) for v in vals][:2]
        elif k in ("max_trades_per_day", "max_losses_in_a_row", "sunday_open_wait_minutes"):
            p[k] = int(vals[0])
        elif k in ("min_rr", "max_weekly_loss_pct"):
            p[k] = float(vals[0])
        elif k == "enforce":
            p[k] = vals[0].lower() in ("true", "yes", "1", "on")
        elif k == "friday_cutoff_ny":
            p[k] = None if vals[0].lower() in ("none", "off") else vals[0]
        elif k in ("bias", "entry"):
            p["timeframes"][k] = vals[0].upper()
        elif k == "checklist":
            p[k] = [" ".join(vals)] if len(vals) == 1 else vals
        else:
            T.die("unknown key %s" % k)
        T.save_json(plan_path(), p)
        T.emit(dict(plan=p, text="updated %s\n%s" % (k, render_plan(p))), a.json)
        return 0
    if a.cmd == "setup":
        if a.action == "add":
            if not a.description:
                T.die("describe the setup: what must be true to take it")
            p.setdefault("setups", {})[a.name] = a.description
        else:
            p.get("setups", {}).pop(a.name, None)
        T.save_json(plan_path(), p)
        T.emit(dict(plan=p, text=render_plan(p)), a.json)
        return 0

    clock = T.market_clock(cfg=cfg)
    clock["now_iso"] = T.ny_now().isoformat()
    j = T.journal()["trades"]
    orders = T.load_orders()["orders"]
    stats = day_stats(j, orders)
    bal = T.account_snapshot(cfg)["balance"]
    if a.cmd == "today":
        L = ["TODAY vs your plan  (%s, your time %s)" % (clock["status"], clock["now_local"]),
             "trades today %d / %d | losses in a row %d / %d | this week %+.2f (limit -%.2f)" % (
                 stats["trades_today"], p["max_trades_per_day"], stats["losses_in_a_row"], p["max_losses_in_a_row"],
                 stats["week_pnl"], bal * p["max_weekly_loss_pct"] / 100)]
        stop = []
        if stats["trades_today"] >= p["max_trades_per_day"]:
            stop.append("daily trade limit reached")
        if stats["losses_in_a_row"] >= p["max_losses_in_a_row"]:
            stop.append("losing streak limit reached: walk away until tomorrow")
        if stats["week_pnl"] <= -bal * p["max_weekly_loss_pct"] / 100:
            stop.append("weekly loss limit reached")
        ses = set(p.get("sessions") or [])
        if clock["open"] and ses and not ses & set(clock.get("sessions", [])):
            stop.append("outside your sessions (%s)" % ", ".join(sorted(ses)))
        for sym in p.get("pairs", []):
            nb = news_blocks(sym, p)
            if nb:
                L.append("  %s: news blackout now (%s)" % (sym, "; ".join(nb)))
        L.append("=> " + ("STOP: " + "; ".join(stop) if stop else ("you may look for your setups" if clock["open"]
                                                                     else "market closed")))
        T.emit({"stats": stats, "stop": stop, "text": "\n".join(L)}, a.json)
        return 0

    data = T.load_orders()
    sel = [o for o in data["orders"] if (not a.order_ids or o.get("id") in a.order_ids)
           and o.get("status", "proposed") in ("proposed", "passed", "blocked")]
    if not sel:
        T.die("no matching proposed orders")
    answers = parse_answers(a.checklist) if a.checklist else None
    verdicts = T.load_json(T.tpath("plan_verdicts.json"), {}) or {}
    L, out = ["PLAN CHECK  (%s)" % clock["status"]], []
    for o in sel:
        v, R, N = evaluate(o, p, clock, stats, bal, news_blocks(o["symbol"], p), answers)
        h = T.order_hash(o)
        verdicts[h] = {"verdict": v, "order_id": o.get("id"), "reasons": R, "checked_at": T.ny_now().isoformat(timespec="seconds"),
                       "checklist": answers}
        L.append("%s  %s  %s %s %s setup=%s" % (v, o.get("id"), o.get("side"), o.get("volume"), o.get("symbol"), o.get("setup")))
        L += ["   x " + r for r in R] + ["   ! " + n for n in N]
        if v == "NEEDS CHECKLIST":
            L += ["   ? %d. %s" % (k + 1, q) for k, q in enumerate(p.get("checklist", []))]
        out.append({"id": o.get("id"), "verdict": v, "reasons": R, "notes": N})
    T.save_json(T.tpath("plan_verdicts.json"), verdicts)
    T.emit({"results": out, "text": "\n".join(L)}, a.json)
    return 0 if all(r["verdict"] == "IN PLAN" for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
