#!/usr/bin/env python3
"""Trade Coach: reviews your closed trades and tells you what to keep and what to fix.

    python3 coach.py review                         # all closed trades
    python3 coach.py review --days 7                # this week's review (also saved as markdown)
    python3 coach.py tag p3 --setup trend-pullback --emotion fomo --mistake "entered before the candle closed"
    python3 coach.py tag p3 --note "moved my stop"
    python3 coach.py emotions                       # the words to use when tagging

Reads trader/journal.json (filled by the order executor and budget manager),
trader/plan.json (your limits) and trader/calendar_history.json (news times).
Looks at results by setup, session, pair, direction, weekday and emotion, and
checks for the habits that drain accounts: revenge trades, bigger size after
a loss, overtrading days, cutting winners early, losses bigger than the plan,
trades without a stop, holding losers longer than winners, trading into
high-impact news and breaking your own plan.
"""
import argparse
import collections
import datetime as dt
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

EMOTIONS = ["calm", "confident", "fomo", "revenge", "fear", "greed", "bored", "tired", "impatient", "hopeful"]
MIN_GROUP = 3


def _ts(s):
    try:
        d = dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=T.NY)


def enrich(t, cfg):
    o, c = _ts(t.get("opened_at")), _ts(t.get("closed_at"))
    x = dict(t)
    x["_open"], x["_close"] = o, c
    x["symbol"] = T.norm_symbol(t.get("symbol"))
    pnl = float(t.get("pnl") or 0)
    x["outcome"] = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
    if o:
        ses = T.active_sessions(o)
        x["session"] = "London/NY overlap" if "London" in ses and "New York" in ses else (" / ".join(ses) or "off-session")
        loc = o.astimezone(T.user_tz(cfg))
        x["weekday"] = loc.strftime("%a")
        x["hour"] = loc.strftime("%H:00")
    if o and c:
        x["minutes"] = max(0, (c - o).total_seconds() / 60)
    if x.get("pips") is None and t.get("exit") is not None and t.get("entry") is not None:
        x["pips"] = T.pips((float(t["exit"]) - float(t["entry"])) * (1 if t.get("side") == "buy" else -1), x["symbol"])
    return x


def group_stats(ts):
    wins = [t for t in ts if t["outcome"] == "win"]
    losses = [t for t in ts if t["outcome"] == "loss"]
    gw = sum(float(t["pnl"]) for t in wins)
    gl = -sum(float(t["pnl"]) for t in losses)
    rs = [t["r"] for t in ts if t.get("r") is not None]
    return {"n": len(ts), "net": round(gw - gl, 2), "win_rate": round(len(wins) / len(ts) * 100) if ts else 0,
            "pf": round(gw / gl, 2) if gl else None, "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "pips": round(sum(t.get("pips") or 0 for t in ts), 1)}


def breakdown(trades, key):
    g = collections.defaultdict(list)
    for t in trades:
        g[t.get(key) or "untagged"].append(t)
    return {k: group_stats(v) for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))}


def detect(trades, plan, cal_events):
    """Return a list of findings: {kind, title, count, net, detail, severity}."""
    F = []
    ts = sorted(trades, key=lambda t: t.get("_open") or dt.datetime.min.replace(tzinfo=dt.timezone.utc))

    def add(kind, title, items, sev, detail=""):
        if items:
            F.append({"kind": kind, "title": title, "count": len(items), "net": round(sum(float(t.get("pnl") or 0) for t in items), 2),
                      "ids": [t.get("id") for t in items], "severity": sev, "detail": detail})

    revenge, upsized = [], []
    for k in range(1, len(ts)):
        prev, cur = ts[k - 1], ts[k]
        if prev["outcome"] == "loss" and prev.get("_close") and cur.get("_open"):
            gap = (cur["_open"] - prev["_close"]).total_seconds() / 60
            if 0 <= gap <= 60:
                revenge.append(cur)
            if float(cur.get("volume") or 0) > 1.25 * float(prev.get("volume") or 0) and gap <= 24 * 60:
                upsized.append(cur)
    add("revenge", "trades opened within an hour after a loss (revenge trading?)", revenge, 3)
    add("upsize", "bigger lot right after a loss", upsized, 3)

    per_day = collections.defaultdict(list)
    for t in ts:
        if t.get("_open"):
            per_day[t["_open"].astimezone(T.NY).date()].append(t)
    cap = int((plan or {}).get("max_trades_per_day") or 0)
    if not cap and per_day:
        cap = max(3, int(statistics.median(len(v) for v in per_day.values()) * 2))
    over = [t for d, v in per_day.items() if len(v) > cap for t in v[cap:]]
    add("overtrading", "trades beyond %d a day (overtrading)" % cap, over, 2)

    wins = [t for t in ts if t["outcome"] == "win" and t.get("r") is not None]
    losses = [t for t in ts if t["outcome"] == "loss" and t.get("r") is not None]
    if len(wins) >= MIN_GROUP:
        small = [t for t in wins if t["r"] < 1.0]
        if len(small) / len(wins) >= 0.5:
            avg_l = statistics.mean([t["r"] for t in losses]) if losses else -1
            add("cut_winners", "winners closed below 1R (%d of %d): cutting winners early while losers average %.2fR" % (
                len(small), len(wins), avg_l), small, 2)
    big = [t for t in losses if t["r"] < -1.2]
    add("big_losses", "losses bigger than the planned stop (< -1.2R): stop moved, removed, or gapped", big, 3)
    nostop = [t for t in ts if not t.get("sl")]
    add("no_stop", "trades without a stop-loss", nostop, 3)
    lw = [t["minutes"] for t in ts if t["outcome"] == "win" and t.get("minutes") is not None]
    ll = [t["minutes"] for t in ts if t["outcome"] == "loss" and t.get("minutes") is not None]
    if len(lw) >= MIN_GROUP and len(ll) >= MIN_GROUP and statistics.median(ll) > 1.5 * statistics.median(lw):
        F.append({"kind": "hold_losers", "title": "losers held %.0f min vs winners %.0f min (median): hoping instead of exiting" % (
            statistics.median(ll), statistics.median(lw)), "count": len(ll), "net": None, "ids": [], "severity": 2, "detail": ""})
    breaks = [t for t in ts if t.get("rule_break")]
    add("rule_breaks", "trades taken outside your plan (overridden)", breaks, 3)
    newsy = []
    for t in ts:
        if not t.get("_open"):
            continue
        ccys = set(T.split_pair(t["symbol"]))
        for e in cal_events:
            if e.get("impact") != "High" or e.get("currency") not in ccys:
                continue
            et = _ts(e.get("time"))
            if et and abs((t["_open"] - et).total_seconds()) <= 15 * 60:
                newsy.append(t)
                break
    add("news", "trades opened within 15 min of high-impact news", newsy, 2)
    untagged = [t for t in ts if not t.get("setup")]
    if ts and len(untagged) / len(ts) > 0.3:
        F.append({"kind": "untagged", "title": "%d of %d trades have no setup name: you cannot learn which setup works" % (
            len(untagged), len(ts)), "count": len(untagged), "net": None, "ids": [], "severity": 1, "detail": ""})
    return F


def strengths(trades):
    out = []
    for key, label in (("setup", "setup"), ("session", "session"), ("symbol", "pair"), ("side", "direction"),
                       ("weekday", "day")):
        for k, s in breakdown(trades, key).items():
            if k != "untagged" and s["n"] >= MIN_GROUP and s["net"] > 0 and (s["pf"] is None or s["pf"] >= 1.5):
                out.append({"what": "%s %s" % (label, k), "stats": s})
    out.sort(key=lambda x: -x["stats"]["net"])
    return out[:3]


def weaknesses(trades):
    out = []
    for key, label in (("setup", "setup"), ("session", "session"), ("symbol", "pair"), ("weekday", "day"),
                       ("emotion", "emotion")):
        for k, s in breakdown(trades, key).items():
            if k != "untagged" and s["n"] >= MIN_GROUP and s["net"] < 0:
                out.append({"what": "%s %s" % (label, k), "stats": s})
    out.sort(key=lambda x: x["stats"]["net"])
    return out[:3]


def fmt_stats(s):
    return "%d trades, net %+.2f, win %d%%, PF %s, avg %sR, %+.1f pips" % (
        s["n"], s["net"], s["win_rate"], s["pf"] if s["pf"] is not None else ("no losses" if s["net"] > 0 else "-"), s["avg_r"] if s["avg_r"] is not None else "-", s["pips"])


def review(trades, plan, cal_events, title):
    total = group_stats(trades)
    found = sorted(detect(trades, plan, cal_events), key=lambda f: (-f["severity"], f["net"] if f["net"] is not None else 0))
    keep = strengths(trades)
    weak = weaknesses(trades)
    L = ["TRADE COACH  %s" % title, "overall: " + fmt_stats(total), ""]
    L.append("KEEP DOING:")
    L += ["  + %s: %s" % (k["what"], fmt_stats(k["stats"])) for k in keep] or ["  (need at least %d trades in a group to judge)" % MIN_GROUP]
    L.append("FIX:")
    fixes = [f for f in found if f["severity"] >= 2][:3]
    for f in fixes:
        L.append("  - %s: %d trade(s)%s" % (f["title"], f["count"], ", net %+.2f" % f["net"] if f["net"] is not None else ""))
    if not fixes:
        L.append("  no damaging habit found: keep following the plan")
    if weak:
        L.append("WHERE YOU LOSE:")
        L += ["  - %s: %s" % (w["what"], fmt_stats(w["stats"])) for w in weak]
    other = [f for f in found if f not in fixes]
    if other:
        L.append("ALSO NOTICED:")
        L += ["  . %s (%d)" % (f["title"], f["count"]) for f in other]
    for key, label in (("setup", "by setup"), ("session", "by session"), ("symbol", "by pair")):
        bd = breakdown(trades, key)
        if len(bd) > 1 or "untagged" not in bd:
            L.append("")
            L.append(label + ":")
            L += ["  %-22s %s" % (k, fmt_stats(s)) for k, s in bd.items()]
    if total["n"] < 20:
        L.append("")
        L.append("note: %d closed trades. Patterns firm up after 20-30; treat these as early signs." % total["n"])
    return {"overall": total, "findings": found, "keep": keep, "weak": weak, "text": "\n".join(L)}


def save_markdown(text, days):
    d = T.ny_now().date().isoformat()
    p = T.tpath("reviews", "review-%s%s.md" % (d, "-%dd" % days if days else ""))
    with open(p, "w", encoding="utf-8") as f:
        f.write("# Trade review %s\n\n```\n%s\n```\n" % (d, text))
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review")
    r.add_argument("--days", type=int, help="only trades closed in the last N days")
    r.add_argument("--pair")
    r.add_argument("--no-save", action="store_true")
    t = sub.add_parser("tag")
    t.add_argument("trade_id")
    t.add_argument("--setup")
    t.add_argument("--emotion", choices=EMOTIONS)
    t.add_argument("--mistake")
    t.add_argument("--note")
    sub.add_parser("emotions")
    a = ap.parse_args(argv)
    cfg = T.load_config()

    if a.cmd == "emotions":
        T.emit({"emotions": EMOTIONS, "text": "tag with one of: " + ", ".join(EMOTIONS) +
                "\nAsk after each trade: how did you feel when you clicked?"}, a.json)
        return 0
    j = T.journal()
    if a.cmd == "tag":
        hit = [x for x in j["trades"] if str(x.get("id")) == a.trade_id]
        if not hit:
            T.die("trade %s not in the journal (budget.py journal lists them)" % a.trade_id)
        x = hit[0]
        if a.setup:
            x["setup"] = a.setup
        if a.emotion:
            x["emotion"] = a.emotion
        if a.mistake:
            x.setdefault("mistakes", []).append(a.mistake)
        if a.note:
            x["note"] = ((x.get("note") or "") + " | " + a.note).strip(" |")
        T.save_journal(j)
        T.emit({"trade": x, "text": "tagged %s: setup=%s emotion=%s mistakes=%s" % (
            a.trade_id, x.get("setup"), x.get("emotion"), x.get("mistakes"))}, a.json)
        return 0

    trades = [enrich(x, cfg) for x in j["trades"] if x.get("status") == "closed" and x.get("pnl") is not None]
    title = "all closed trades"
    if a.days:
        since = T.ny_now() - dt.timedelta(days=a.days)
        trades = [x for x in trades if x.get("_close") and x["_close"] >= since]
        title = "last %d days" % a.days
    if a.pair:
        trades = [x for x in trades if x["symbol"] == T.norm_symbol(a.pair)]
        title += " on " + T.norm_symbol(a.pair)
    if not trades:
        T.emit({"text": "no closed trades to review yet (%s)" % title}, a.json)
        return 0
    plan = T.load_json(T.tpath("plan.json"), None)
    cal = list((T.load_json(T.tpath("calendar_history.json"), {"events": {}}) or {}).get("events", {}).values())
    out = review(trades, plan, cal, title)
    if not a.no_save:
        out["saved"] = save_markdown(out["text"], a.days)
        out["text"] += "\n\nsaved: " + out["saved"]
    T.emit(out, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
