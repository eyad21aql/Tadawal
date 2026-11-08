#!/usr/bin/env python3
"""Time Machine: replay a strategy on past bars and see what would have happened.

    python3 backtest.py AAPL --strategy ema_cross --fast 20 --slow 50
    python3 backtest.py SPY --strategy rsi_revert --tf D1 --start 2018-01-01
    python3 backtest.py NVDA --strategy breakout --lookback 55 --sl-atr 2 --trail-atr 3
    python3 backtest.py AAPL --rules my_rules.json
    python3 backtest.py AAPL --strategy ema_cross --grid fast=10,20,30 slow=50,100,200

Signals are read on a bar's close and filled at the NEXT bar's open (no
look-ahead). If the stop and the target are both inside one bar, the stop is
assumed to hit first. Slippage and commission are charged on every fill.
The last 30% of the data is reported separately as out-of-sample (--split).

Rules file format (any of entry_long, exit_long, entry_short, exit_short;
all conditions in a list must be true):
  {"entry_long": [["rsi14", "crosses_above", 30], ["close", ">", "sma200"]],
   "exit_long":  [["rsi14", ">", 60]]}
Series: open high low close volume, smaN emaN rsiN atrN, macd macd_signal
macd_hist, bb_upperN bb_lowerN bb_midN, highestN lowestN (previous N bars),
vol_smaN, or a number. Operators: > < >= <= crosses_above crosses_below.
"""
import argparse
import datetime as dt
import itertools
import json
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

BARS_PER_YEAR = {"M1": 252 * 390, "M5": 252 * 78, "M15": 252 * 26, "M30": 252 * 13, "H1": 252 * 7,
                 "H4": 252 * 2, "D1": 252, "W1": 52}

STRATEGIES = {
    "ema_cross": lambda p: {
        "entry_long": [["ema%d" % p["fast"], "crosses_above", "ema%d" % p["slow"]]],
        "exit_long": [["ema%d" % p["fast"], "crosses_below", "ema%d" % p["slow"]]],
        "entry_short": [["ema%d" % p["fast"], "crosses_below", "ema%d" % p["slow"]]],
        "exit_short": [["ema%d" % p["fast"], "crosses_above", "ema%d" % p["slow"]]]},
    "rsi_revert": lambda p: {
        "entry_long": [["rsi14", "crosses_above", p["rsi_low"]], ["close", ">", "sma200"]],
        "exit_long": [["rsi14", ">", p["rsi_high"]]],
        "entry_short": [["rsi14", "crosses_below", p["rsi_high"]], ["close", "<", "sma200"]],
        "exit_short": [["rsi14", "<", p["rsi_low"]]]},
    "breakout": lambda p: {
        "entry_long": [["close", ">", "highest%d" % p["lookback"]]],
        "exit_long": [["close", "<", "lowest%d" % max(2, p["lookback"] // 2)]],
        "entry_short": [["close", "<", "lowest%d" % p["lookback"]]],
        "exit_short": [["close", ">", "highest%d" % max(2, p["lookback"] // 2)]]},
    "macd_trend": lambda p: {
        "entry_long": [["macd", "crosses_above", "macd_signal"], ["close", ">", "ema200"]],
        "exit_long": [["macd", "crosses_below", "macd_signal"]],
        "entry_short": [["macd", "crosses_below", "macd_signal"], ["close", "<", "ema200"]],
        "exit_short": [["macd", "crosses_above", "macd_signal"]]},
    "pullback": lambda p: {
        "entry_long": [["close", ">", "ema200"], ["rsi14", "crosses_above", p["rsi_low"] + 10]],
        "exit_long": [["rsi14", ">", p["rsi_high"]]],
        "entry_short": [["close", "<", "ema200"], ["rsi14", "crosses_below", p["rsi_high"] - 10]],
        "exit_short": [["rsi14", "<", p["rsi_low"]]]},
}


RELEVANT = {"ema_cross": ("fast", "slow"), "rsi_revert": ("rsi_low", "rsi_high"), "breakout": ("lookback",),
            "macd_trend": (), "pullback": ("rsi_low", "rsi_high")}


class Series:
    def __init__(self, b):
        self.b, self.cache = b, {}

    def get(self, name):
        if isinstance(name, (int, float)):
            return None
        name = str(name).lower()
        if name in self.cache:
            return self.cache[name]
        b = self.b
        base = {"open": b.o, "high": b.h, "low": b.l, "close": b.c, "volume": b.v}
        if name in base:
            s = base[name]
        elif name in ("macd", "macd_signal", "macd_hist"):
            m = T.macd(b.c)
            self.cache.update({"macd": m[0], "macd_signal": m[1], "macd_hist": m[2]})
            return self.cache[name]
        else:
            m = re.fullmatch(r"(sma|ema|rsi|atr|bb_upper|bb_lower|bb_mid|highest|lowest|vol_sma)(\d+)", name)
            if not m:
                T.die("unknown series %r" % name)
            f, n = m.group(1), int(m.group(2))
            if f == "sma":
                s = T.sma(b.c, n)
            elif f == "ema":
                s = T.ema(b.c, n)
            elif f == "rsi":
                s = T.rsi(b.c, n)
            elif f == "atr":
                s = T.atr(b, n)
            elif f == "highest":
                s = T.highest(b.h, n)
            elif f == "lowest":
                s = T.lowest(b.l, n)
            elif f == "vol_sma":
                s = T.sma(b.v, n)
            else:
                mid, up, lo = T.bollinger(b.c, n)
                s = {"bb_upper": up, "bb_lower": lo, "bb_mid": mid}[f]
        self.cache[name] = s
        return s

    def val(self, x, i):
        if isinstance(x, (int, float)):
            return float(x)
        try:
            return float(x)
        except (TypeError, ValueError):
            pass
        if i < 0:
            return None
        return self.get(x)[i]


def cond_true(S, c, i):
    lhs, op, rhs = c
    a, b = S.val(lhs, i), S.val(rhs, i)
    if a is None or b is None:
        return False
    if op in ("crosses_above", "crosses_below"):
        a0, b0 = S.val(lhs, i - 1), S.val(rhs, i - 1)
        if a0 is None or b0 is None:
            return False
        return (a0 <= b0 and a > b) if op == "crosses_above" else (a0 >= b0 and a < b)
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def all_true(S, conds, i):
    return bool(conds) and all(cond_true(S, c, i) for c in conds)


def run(b, rules, capital=10000.0, risk_pct=1.0, sl_atr=2.0, tp_r=None, trail_atr=None, max_bars=None,
        slippage_bps=2.0, commission=0.0, allow_short=False, fractional=False, start_ts=None):
    S = Series(b)
    atr = T.atr(b, 14)
    n = len(b)
    slip = slippage_bps / 10000.0
    cash = capital
    pos = None
    pending = None
    trades, curve = [], []
    first = 1
    if start_ts:
        first = max(1, next((i for i, t in enumerate(b.t) if t >= start_ts), n))

    def fill(px, side, entering):
        worse = 1 + slip if (side == "buy") == entering else 1 - slip
        return px * worse

    def close(i, px, why):
        nonlocal cash, pos
        side = pos["side"]
        exit_px = fill(px, side, False)
        sgn = 1 if side == "buy" else -1
        pnl = sgn * (exit_px - pos["entry"]) * pos["qty"] - commission * pos["qty"] * 2
        cash += pnl
        R = pos["risk"]
        trades.append({"side": side, "entry_date": b.date(pos["i"]), "entry": round(pos["entry"], 4),
                       "exit_date": b.date(i), "exit": round(exit_px, 4), "qty": pos["qty"],
                       "pnl": round(pnl, 2), "r": round(pnl / R, 2) if R else None,
                       "bars": i - pos["i"], "why": why})
        pos = None

    for i in range(first, n):
        if pending in ("buy", "sell") and pos is None:
            side = pending
            pending = None
            a = atr[i - 1]
            entry = fill(b.o[i], side, True)
            stop = None
            if sl_atr and a:
                stop = entry - sl_atr * a if side == "buy" else entry + sl_atr * a
            equity = cash
            if stop is not None:
                per = abs(entry - stop)
                qty = equity * risk_pct / 100.0 / per
            else:
                qty = equity / entry
            qty = min(qty, equity / entry)
            qty = qty if fractional else math.floor(qty)
            if qty > 0:
                risk = abs(entry - stop) * qty if stop is not None else entry * qty * 0.1
                tp = None
                if tp_r and stop is not None:
                    tp = entry + tp_r * abs(entry - stop) * (1 if side == "buy" else -1)
                pos = {"side": side, "entry": entry, "qty": qty, "stop": stop, "tp": tp, "i": i, "risk": risk,
                       "best": entry}
        elif pending == "exit" and pos:
            pending = None
            close(i, b.o[i], "exit signal")
        if pos:
            long = pos["side"] == "buy"
            st, tp = pos["stop"], pos["tp"]
            if st is not None and ((long and b.o[i] <= st) or (not long and b.o[i] >= st)):
                close(i, b.o[i], "stop (gap)")
            elif st is not None and ((long and b.l[i] <= st) or (not long and b.h[i] >= st)):
                close(i, st, "stop" if st == pos.get("stop0", st) else "trailing stop")
            elif tp is not None and ((long and b.h[i] >= tp) or (not long and b.l[i] <= tp)):
                close(i, tp, "target")
            elif max_bars and i - pos["i"] >= max_bars:
                close(i, b.c[i], "time exit")
        if pos and trail_atr and atr[i]:
            long = pos["side"] == "buy"
            pos.setdefault("stop0", pos["stop"])
            if long:
                pos["best"] = max(pos["best"], b.h[i])
                ns = pos["best"] - trail_atr * atr[i]
                if pos["stop"] is None or ns > pos["stop"]:
                    pos["stop"] = ns
            else:
                pos["best"] = min(pos["best"], b.l[i])
                ns = pos["best"] + trail_atr * atr[i]
                if pos["stop"] is None or ns < pos["stop"]:
                    pos["stop"] = ns
        if i < n - 1:
            if pos:
                key = "exit_long" if pos["side"] == "buy" else "exit_short"
                if all_true(S, rules.get(key), i):
                    pending = "exit"
            else:
                if all_true(S, rules.get("entry_long"), i):
                    pending = "buy"
                elif allow_short and all_true(S, rules.get("entry_short"), i):
                    pending = "sell"
        mark = cash
        if pos:
            sgn = 1 if pos["side"] == "buy" else -1
            mark += sgn * (b.c[i] - pos["entry"]) * pos["qty"]
        curve.append((b.t[i], mark))
    if pos:
        close(n - 1, b.c[-1], "still open at end")
        curve[-1] = (b.t[-1], cash)
    return trades, curve


def metrics(trades, curve, tf, capital):
    if not curve:
        return {}
    eq = [c[1] for c in curve]
    start, end = capital, eq[-1]
    years = max((curve[-1][0] - curve[0][0]) / (365.25 * 86400), 1e-9)
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    rets = [(eq[k] / eq[k - 1] - 1) for k in range(1, len(eq)) if eq[k - 1] > 0]
    mu = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / max(1, len(rets) - 1)) if len(rets) > 1 else 0
    sharpe = mu / sd * math.sqrt(BARS_PER_YEAR.get(tf, 252)) if sd else 0
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    rs = [t["r"] for t in trades if t["r"] is not None]
    in_mkt = sum(t["bars"] for t in trades)
    return {
        "trades": len(trades), "return_pct": round((end / start - 1) * 100, 2),
        "cagr_pct": round(((end / start) ** (1 / years) - 1) * 100, 2) if end > 0 else -100.0,
        "max_drawdown_pct": round(mdd * 100, 2), "sharpe": round(sharpe, 2),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "profit_factor": round(gw / gl, 2) if gl else (float("inf") if gw else 0.0),
        "avg_win": round(gw / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gl / len(losses), 2) if losses else 0.0,
        "expectancy_r": round(sum(rs) / len(rs), 2) if rs else 0.0,
        "avg_bars_held": round(in_mkt / len(trades), 1) if trades else 0.0,
        "exposure_pct": round(in_mkt / max(1, len(curve)) * 100, 1),
        "best": max((t["pnl"] for t in trades), default=0.0), "worst": min((t["pnl"] for t in trades), default=0.0),
        "final_equity": round(end, 2), "years": round(years, 2)}


def buy_hold(b, first, capital):
    p0 = b.o[first] if first < len(b) else b.c[-1]
    eq = [capital * c / p0 for c in b.c[first:]]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    return {"return_pct": round((eq[-1] / capital - 1) * 100, 2), "max_drawdown_pct": round(mdd * 100, 2)}


def split_metrics(trades, curve, tf, split):
    k = int(len(curve) * split)
    if k < 10 or len(curve) - k < 10:
        return None
    cut = curve[k][0]
    ins = [t for t in trades if t["entry_date"] < _fmt(cut, tf)]
    oos = [t for t in trades if t["entry_date"] >= _fmt(cut, tf)]
    return {"split_date": _fmt(cut, tf),
            "in_sample": metrics(ins, curve[:k], tf, curve[0][1]),
            "out_of_sample": metrics(oos, curve[k:], tf, curve[k][1])}


def _fmt(ts, tf):
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    if T.NY:
        d = d.astimezone(T.NY)
    return d.strftime("%Y-%m-%d") if tf in ("D1", "W1") else d.strftime("%Y-%m-%d %H:%M")


def backtest(b, rules, name, opts):
    start_ts = None
    if opts.get("start"):
        start_ts = int(dt.datetime.fromisoformat(opts["start"]).replace(tzinfo=dt.timezone.utc).timestamp())
    if opts.get("end"):
        end_ts = int(dt.datetime.fromisoformat(opts["end"]).replace(tzinfo=dt.timezone.utc).timestamp())
        b = b.slice(0, next((i for i, t in enumerate(b.t) if t > end_ts), len(b)))
    kw = {k: opts[k] for k in ("capital", "risk_pct", "sl_atr", "tp_r", "trail_atr", "max_bars", "slippage_bps",
                               "commission", "allow_short", "fractional") if k in opts}
    trades, curve = run(b, rules, start_ts=start_ts, **kw)
    first = len(b) - len(curve)
    m = metrics(trades, curve, b.tf, opts.get("capital", 10000.0))
    out = {"symbol": b.symbol, "tf": b.tf, "source": b.source, "strategy": name, "rules": rules,
           "settings": kw, "from": b.date(first) if curve else None, "to": b.date(len(b) - 1),
           "metrics": m, "buy_hold": buy_hold(b, first, opts.get("capital", 10000.0)),
           "walk_forward": split_metrics(trades, curve, b.tf, opts.get("split", 0.7)), "trades": trades}
    return out


def render(o, show_trades=10):
    m, bh = o["metrics"], o["buy_hold"]
    s = o["settings"]
    L = ["TIME MACHINE  %s %s  %s  (%s .. %s, %s)" % (o["symbol"], o["tf"], o["strategy"], o["from"], o["to"], o["source"]),
         "capital %s, risk %s%%/trade, stop %s ATR, target %s, trail %s, slippage %s bps, commission %s/share" % (
             s.get("capital"), s.get("risk_pct"), s.get("sl_atr") or "none", "%sR" % s["tp_r"] if s.get("tp_r") else "none",
             "%s ATR" % s["trail_atr"] if s.get("trail_atr") else "none", s.get("slippage_bps"), s.get("commission")),
         "",
         "strategy   return %6s%%  CAGR %6s%%  max DD %6s%%  Sharpe %s" % (
             m.get("return_pct"), m.get("cagr_pct"), m.get("max_drawdown_pct"), m.get("sharpe")),
         "buy&hold   return %6s%%                 max DD %6s%%" % (bh["return_pct"], bh["max_drawdown_pct"]),
         "trades %s | win rate %s%% | profit factor %s | expectancy %sR | avg win %s / avg loss %s | held %s bars | in market %s%%" % (
             m.get("trades"), m.get("win_rate_pct"), m.get("profit_factor"), m.get("expectancy_r"), m.get("avg_win"),
             m.get("avg_loss"), m.get("avg_bars_held"), m.get("exposure_pct"))]
    wf = o.get("walk_forward")
    if wf:
        i, x = wf["in_sample"], wf["out_of_sample"]
        L.append("walk-forward split %s: in-sample %s%% (PF %s, %s trades) | out-of-sample %s%% (PF %s, %s trades)" % (
            wf["split_date"], i.get("return_pct"), i.get("profit_factor"), i.get("trades"),
            x.get("return_pct"), x.get("profit_factor"), x.get("trades")))
    warn = []
    if m.get("trades", 0) < 30:
        warn.append("only %d trades: too few to trust the statistics" % m.get("trades", 0))
    if wf and wf["in_sample"].get("return_pct", 0) > 0 and wf["out_of_sample"].get("return_pct", 0) <= 0:
        warn.append("works in-sample but not out-of-sample: likely curve-fitted")
    if m.get("return_pct", 0) < bh["return_pct"] and m.get("max_drawdown_pct", 0) <= bh["max_drawdown_pct"]:
        warn.append("buy & hold beat it on return and drawdown")
    L += ["warning: " + w for w in warn]
    if show_trades and o["trades"]:
        L.append("")
        L.append("last %d trades:" % min(show_trades, len(o["trades"])))
        for t in o["trades"][-show_trades:]:
            L.append("  %s %s %s @%s -> %s @%s  %+.2f (%sR) %s" % (
                t["side"], t["qty"], t["entry_date"], t["entry"], t["exit_date"], t["exit"], t["pnl"], t["r"], t["why"]))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    T.add_data_args(ap, "D1", 5000)
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), default="ema_cross")
    ap.add_argument("--rules", help="JSON rules file (overrides --strategy)")
    ap.add_argument("--fast", type=int, default=20)
    ap.add_argument("--slow", type=int, default=50)
    ap.add_argument("--rsi-low", type=float, default=30)
    ap.add_argument("--rsi-high", type=float, default=70)
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--sl-atr", type=float, default=2.0, help="initial stop in ATRs (0 = none)")
    ap.add_argument("--tp-r", type=float, help="take profit at this many R")
    ap.add_argument("--trail-atr", type=float, help="ATR trailing stop")
    ap.add_argument("--max-bars", type=int, help="exit after this many bars")
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--commission", type=float, default=0.0, help="per share per side")
    ap.add_argument("--allow-short", action="store_true")
    ap.add_argument("--fractional", action="store_true")
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--grid", nargs="*", help="e.g. fast=10,20 slow=50,100: test every combination")
    ap.add_argument("--trades", type=int, default=10, help="how many trades to list")
    a = ap.parse_args(argv)
    b = T.get_bars(a.symbol, a.tf, a.count, a.source, a.csv)
    opts = {"capital": a.capital, "risk_pct": a.risk_pct, "sl_atr": a.sl_atr or None, "tp_r": a.tp_r,
            "trail_atr": a.trail_atr, "max_bars": a.max_bars, "slippage_bps": a.slippage_bps,
            "commission": a.commission, "allow_short": a.allow_short, "fractional": a.fractional,
            "start": a.start, "end": a.end, "split": a.split}
    params = {"fast": a.fast, "slow": a.slow, "rsi_low": a.rsi_low, "rsi_high": a.rsi_high, "lookback": a.lookback}
    if a.grid:
        if a.rules:
            T.die("--grid works with --strategy parameters, not --rules")
        axes = {}
        for g in a.grid:
            k, v = g.split("=")
            axes[k.replace("-", "_")] = [float(x) if "." in x else int(x) for x in v.split(",")]
        rows = []
        for combo in itertools.product(*axes.values()):
            p = dict(params, **dict(zip(axes, combo)))
            o = backtest(b, STRATEGIES[a.strategy](p), a.strategy, opts)
            wf = o["walk_forward"] or {}
            rows.append({"params": dict(zip(axes, combo)), **{k: o["metrics"].get(k) for k in (
                "return_pct", "max_drawdown_pct", "profit_factor", "trades", "sharpe")},
                "oos_return_pct": (wf.get("out_of_sample") or {}).get("return_pct")})
        rows.sort(key=lambda r: -(r["sharpe"] or 0))
        L = ["GRID  %s %s %s  (%d combinations, sorted by Sharpe)" % (b.symbol, b.tf, a.strategy, len(rows))]
        for r in rows:
            L.append("  %-28s ret %7s%%  DD %7s%%  PF %5s  trades %4s  Sharpe %5s  out-of-sample %s%%" % (
                json.dumps(r["params"]), r["return_pct"], r["max_drawdown_pct"], r["profit_factor"], r["trades"],
                r["sharpe"], r["oos_return_pct"]))
        L.append("warning: the best row in-sample is usually worse live; prefer a broad plateau of good rows")
        T.emit({"grid": rows, "text": "\n".join(L)}, a.json)
        return 0
    if a.rules:
        rules = T.load_json(a.rules)
        name = os.path.basename(a.rules)
    else:
        rules = STRATEGIES[a.strategy](params)
        name = "%s %s" % (a.strategy, json.dumps({k: params[k] for k in RELEVANT[a.strategy]}))
    o = backtest(b, rules, name, opts)
    stamp = T.ny_now().strftime("%Y%m%d-%H%M%S")
    path = T.tpath("backtests", "%s_%s_%s.json" % (b.symbol, a.strategy if not a.rules else "rules", stamp))
    T.save_json(path, o)
    o["text"] = render(o, a.trades) + "\nsaved: " + path
    T.emit(o, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
