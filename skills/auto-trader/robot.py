#!/usr/bin/env python3
"""Auto Trader: a rules-based trading robot for MetaTrader 5, supervised by Claude.

The robot trades by itself, inside hard limits:
  * enters only when the strategy's rules are true on a CLOSED candle, in the
    allowed sessions, outside news blackouts, in the direction Claude allowed
  * sizes every trade from its stop (risk_pct of the balance) and passes the
    same risk check as manual orders
  * moves the stop to breakeven at +1R (optional ATR trail)
  * NEVER holds a trade through the 17:00 New York rollover (no swap): no new
    trades after last_entry_ny, everything closed by flat_by_ny
  * stops for the day after max_losses_per_day or max_trades_per_day, and for
    the week after max_weekly_loss_pct
  * refuses MT5 REAL accounts unless config allow_live AND robot "live" are both true
  * kill switch: robot.py pause / flat, or /stop /flat from Telegram

    python3 robot.py setup --pairs EURUSD XAUUSD --strategy trend_pullback --tf M15 --risk 0.5
    python3 robot.py show | set KEY VALUE...
    python3 robot.py bias EURUSD buy --note "USD weak after CPI"      # Claude's daily direction
    python3 robot.py backtest --days 60                               # test the exact rules first
    python3 robot.py once --dry-run                                   # one cycle, no orders
    python3 robot.py run                                              # the loop (on the VPS)
    python3 robot.py status | pause | resume | flat
    python3 robot.py telegram-chats | telegram-test

Files: trader/robot.json (settings), trader/robot_state.json (heartbeat, bar
tracking), trader/robot.log, journal in trader/journal.json (mode "robot" / "mt5").
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import time
import traceback
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402


def _sibling(skill, mod):
    path = os.path.join(_HERE, "..", skill, mod + ".py")
    spec = importlib.util.spec_from_file_location("tadawal_%s_%s" % (skill.replace("-", "_"), mod), path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


order = _sibling("order-executor", "order")
risk = _sibling("risk-manager", "risk")
bt = _sibling("time-machine", "backtest")

PRESETS = {
    "trend_pullback": {
        "about": "trade with the EMA200 trend; enter when RSI turns back from a pullback",
        "rules": {"entry_long": [["close", ">", "ema200"], ["ema50", ">", "ema200"], ["rsi14", "crosses_above", 40]],
                  "entry_short": [["close", "<", "ema200"], ["ema50", "<", "ema200"], ["rsi14", "crosses_below", 60]]},
        "sl_atr": 1.5, "tp_r": 2.0},
    "breakout": {
        "about": "close beyond the 20-candle high/low in the direction of the EMA200 trend",
        "rules": {"entry_long": [["close", ">", "highest20"], ["close", ">", "ema200"]],
                  "entry_short": [["close", "<", "lowest20"], ["close", "<", "ema200"]]},
        "sl_atr": 1.5, "tp_r": 2.0},
    "ema_cross": {
        "about": "EMA20 crosses EMA50, filtered by the EMA200 trend",
        "rules": {"entry_long": [["ema20", "crosses_above", "ema50"], ["close", ">", "ema200"]],
                  "entry_short": [["ema20", "crosses_below", "ema50"], ["close", "<", "ema200"]]},
        "sl_atr": 2.0, "tp_r": 2.0},
}

DEFAULTS = {
    "enabled": True, "live": False, "pairs": ["EURUSD", "XAUUSD"], "strategy": "trend_pullback", "rules": None,
    "tf": "M15", "sl_atr": None, "tp_r": None, "risk_pct": 0.5, "max_trades_per_day": 3, "max_losses_per_day": 2,
    "max_weekly_loss_pct": 4.0, "max_open": 2, "sessions": ["London", "New York"], "news_pause_minutes": [30, 15],
    "bias": {}, "bias_hours": 24, "breakeven_at_r": 1.0, "trail_atr": None, "poll_seconds": 20,
    "require_calendar": False, "telegram_chat_id": None, "flat_requested": False,
}
SESSION_NAMES = {"london": "London", "newyork": "New York", "ny": "New York", "tokyo": "Tokyo", "sydney": "Sydney"}


def rc_path():
    return T.tpath("robot.json")


def load_rc():
    rc = dict(DEFAULTS)
    rc.update(T.load_json(rc_path(), {}) or {})
    return rc


def save_rc(rc):
    T.save_json(rc_path(), rc)


def strategy_of(rc):
    p = PRESETS.get(rc.get("strategy")) or {}
    return {"rules": rc.get("rules") or p.get("rules"), "sl_atr": float(rc.get("sl_atr") or p.get("sl_atr") or 1.5),
            "tp_r": float(rc.get("tp_r") or p.get("tp_r") or 2.0), "name": rc.get("strategy") if not rc.get("rules") else "custom"}


def log(msg):
    line = "%s  %s" % (T.ny_now().strftime("%Y-%m-%d %H:%M:%S NY"), msg)
    try:
        with open(T.tpath("robot.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return line


# ------------------------------------------------------------------ Telegram (optional)

class Telegram:
    """Messages and commands through a Telegram bot. Token: env TELEGRAM_BOT_TOKEN."""

    def __init__(self, chat_id=None, token=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "") or None

    @property
    def on(self):
        return bool(self.token and self.chat_id)

    def _call(self, method, params):
        url = "https://api.telegram.org/bot%s/%s?%s" % (self.token, method, urllib.parse.urlencode(params))
        return json.loads(T.http_get(url, timeout=20, retries=2))

    def send(self, text):
        if not self.on:
            return False
        try:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": text[:3900]})
            return True
        except Exception:
            return False

    def updates(self, offset):
        if not self.token:
            return []
        try:
            return self._call("getUpdates", {"offset": offset, "timeout": 0}).get("result", [])
        except Exception:
            return []


# ------------------------------------------------------------------ the robot

class Robot:
    def __init__(self, cfg=None, rc=None, now_fn=None, bars_fn=None, telegram=None, dry_run=False):
        self.cfg = cfg or T.load_config()
        self.rc = rc or load_rc()
        self.now_fn = now_fn or T.ny_now
        self.bars_fn = bars_fn or (lambda sym, tf, n: T.get_bars(sym, tf, n, cfg=self.cfg))
        self.tg = telegram if telegram is not None else Telegram(self.rc.get("telegram_chat_id"))
        self.dry = dry_run
        self.state = T.load_json(T.tpath("robot_state.json"), {}) or {}
        self.messages = []

    # -------------------------------------------------------------- helpers
    def now(self):
        return self.now_fn()

    def say(self, msg, push=True):
        line = log(msg)
        self.messages.append(msg)
        if push:
            self.tg.send("🤖 " + msg)
        return line

    def ocfg(self):
        """Config with the no-overnight rule always on for the robot."""
        return dict(self.cfg, no_overnight=True)

    def save_state(self, status):
        self.state.update(heartbeat=self.now().isoformat(timespec="seconds"), status=status, pid=os.getpid(),
                          mode=self.cfg.get("mode"))
        T.save_json(T.tpath("robot_state.json"), self.state)

    def bias(self, sym):
        b = (self.rc.get("bias") or {}).get(sym)
        if not b:
            return "both"
        if isinstance(b, str):
            return b
        try:
            age = (self.now() - dt.datetime.fromisoformat(b.get("set"))).total_seconds() / 3600
        except (TypeError, ValueError):
            age = 0
        return b.get("side", "both") if age <= float(self.rc.get("bias_hours", 24)) else "both"

    def closed_bars(self, sym):
        b = self.bars_fn(sym, self.rc["tf"], 400)
        step = T.TF_SECONDS[self.rc["tf"]]
        if len(b) and b.t[-1] + step > self.now().timestamp():
            b = b.slice(0, -1)      # drop the candle that is still forming
        return b

    # -------------------------------------------------------------- limits
    def day_stats(self):
        start = T.last_rollover(self.now())
        week = start - dt.timedelta(days=(start.weekday() + 1) % 7)
        trades, closed, week_pnl = 0, [], 0.0
        if self.cfg.get("mode") == "mt5":
            mt5 = T.mt5_connect(self.cfg, required=True)
            magic = int(self.cfg.get("magic", 2126))
            deals = mt5.history_deals_get(week.astimezone(dt.timezone.utc).replace(tzinfo=None),
                                          dt.datetime.utcnow() + dt.timedelta(days=1)) or []
            for d in deals:
                when = dt.datetime.fromtimestamp(int(d.time), dt.timezone.utc)
                if d.entry == 0 and d.magic == magic and when >= start:
                    trades += 1
                if d.entry in (1, 3):
                    pnl = d.profit + d.commission + d.swap
                    week_pnl += pnl
                    if when >= start and d.magic == magic:
                        closed.append((when, pnl))
        else:
            for t in T.journal()["trades"]:
                o = _ts(t.get("opened_at"))
                if t.get("mode") == "robot" and o and o >= start:
                    trades += 1
                c = _ts(t.get("closed_at"))
                if t.get("status") == "closed" and c:
                    if c >= week:
                        week_pnl += float(t.get("pnl") or 0)
                    if c >= start and t.get("mode") == "robot":
                        closed.append((c, float(t.get("pnl") or 0)))
        losses = sum(1 for _, p in closed if p < 0)
        return {"trades": trades, "losses": losses, "week_pnl": round(week_pnl, 2), "pnl_today": round(sum(p for _, p in closed), 2)}

    def news_block(self, sym):
        cal = T.load_json(T.tpath("calendar.json"), None)
        if not cal:
            return "calendar unavailable" if self.rc.get("require_calendar") else None
        before, after = self.rc.get("news_pause_minutes", [30, 15])
        now = self.now().astimezone(dt.timezone.utc)
        for e in cal.get("events", []):
            if e.get("impact") != "High" or e.get("currency") not in T.split_pair(sym):
                continue
            t = _ts(e.get("time"))
            if t is None:
                continue
            mins = (t - now).total_seconds() / 60
            if -after <= mins <= before:
                return "%s %s %s" % (e["currency"], e["title"], "in %d min" % mins if mins >= 0 else "%d min ago" % -mins)
        return None

    def entry_blocks(self, acct, stats):
        """Reasons that stop ALL new entries right now."""
        R = []
        if not self.rc.get("enabled", True):
            R.append("robot paused")
        clock = T.market_clock(self.now(), self.cfg)
        if not clock["open"]:
            R.append(clock["status"])
        ovn = T.overnight_status(self.ocfg(), self.now())
        if not ovn["can_open"]:
            R.append("no new trades after %s (no overnight)" % ovn["last_entry_local"])
        ses = self.rc.get("sessions") or []
        if clock["open"] and ses and not set(ses) & set(clock.get("sessions", [])):
            R.append("outside sessions %s" % "/".join(ses))
        if stats["trades"] >= int(self.rc["max_trades_per_day"]):
            R.append("daily trade limit %d reached" % self.rc["max_trades_per_day"])
        if stats["losses"] >= int(self.rc["max_losses_per_day"]):
            R.append("%d losses today: done for the day" % stats["losses"])
        if stats["week_pnl"] <= -acct["balance"] * float(self.rc["max_weekly_loss_pct"]) / 100:
            R.append("weekly loss limit reached")
        if len(acct["positions"]) >= int(self.rc["max_open"]):
            R.append("%d trades open (max %d)" % (len(acct["positions"]), self.rc["max_open"]))
        return R

    # -------------------------------------------------------------- trading
    def signal(self, b, strat):
        S = bt.Series(b)
        i = len(b) - 1
        if bt.all_true(S, strat["rules"].get("entry_long"), i):
            return "buy"
        if bt.all_true(S, strat["rules"].get("entry_short"), i):
            return "sell"
        return None

    def build_order(self, sym, side, b, strat, acct):
        a = T.atr(b, 14)[-1]
        try:
            q = T.get_price(sym, self.cfg)
            entry = q.get("ask" if side == "buy" else "bid") or q["price"]
        except SystemExit:
            entry = b.c[-1]
        sgn = 1 if side == "buy" else -1
        d = T.digits_for(sym)
        stop = round(entry - sgn * strat["sl_atr"] * a, d)
        tp = round(entry + sgn * strat["tp_r"] * abs(entry - stop), d)
        s = risk.size(sym, entry, stop, tp, self.rc["risk_pct"], acct["balance"], cfg=self.cfg)
        return {"id": "r%s" % self.now().strftime("%m%d%H%M%S"), "symbol": sym, "side": side, "type": "market",
                "volume": s["volume"], "entry": round(entry, d), "sl": stop, "tp": tp, "setup": "robot:%s" % strat["name"],
                "status": "proposed", "created": self.now().isoformat(timespec="seconds")}, s

    def place(self, o, acct):
        res = risk.check_orders([o], self.cfg, acct)
        _, verdict, reasons, warns = res[0]
        if verdict != "PASS":
            return False, "risk check BLOCK: " + "; ".join(reasons)
        if self.dry:
            return True, "dry run: would place"
        if self.cfg.get("mode") == "mt5":
            r = order.mt5_place(o, self.cfg)
            ok = r["status"] in ("filled", "pending")
            return ok, r["msg"]
        pos = order.ledger_open(o, self.cfg, o["entry"], "robot", note="robot")
        return True, "paper fill at %s (position %s)" % (T.px(o["entry"], o["symbol"]), pos["id"])

    def manage(self, acct):
        """Flat before rollover, flat on request, breakeven and trailing."""
        ovn = T.overnight_status(self.ocfg(), self.now())
        held = [p for p in acct["positions"] if T.held_overnight(p.get("opened"), self.now())]
        if acct["positions"] and (ovn["must_close"] or held or self.rc.get("flat_requested")):
            why = "flat requested" if self.rc.get("flat_requested") else "no overnight: closing before the rollover"
            self.close_all(acct, why)
            if self.rc.get("flat_requested"):
                self.rc.update(flat_requested=False, enabled=False)
                save_rc(self.rc)
            return
        if self.rc.get("flat_requested"):
            self.rc.update(flat_requested=False, enabled=False)
            save_rc(self.rc)
        be_r, trail = self.rc.get("breakeven_at_r"), self.rc.get("trail_atr")
        for p in acct["positions"]:
            if not p.get("sl") or not p.get("price"):
                continue
            sgn = 1 if p["side"] == "buy" else -1
            risk_dist = (p["entry"] - p["sl"]) * sgn
            new = None
            if be_r and risk_dist > 0 and (p["price"] - p["entry"]) * sgn >= float(be_r) * risk_dist:
                new = p["entry"] + sgn * T.pip_size(p["symbol"])
            if trail:
                try:
                    b = self.closed_bars(p["symbol"])
                    cand = p["price"] - sgn * float(trail) * T.atr(b, 14)[-1]
                    if (cand - p["sl"]) * sgn > 0 and (new is None or (cand - new) * sgn > 0):
                        new = cand
                except SystemExit:
                    pass
            if new is not None and (new - p["sl"]) * sgn > 0 and (p["price"] - new) * sgn > 0:
                new = round(new, T.digits_for(p["symbol"]))
                if self.modify_sl(p, new):
                    self.say("%s %s: stop moved %s -> %s" % (p["symbol"], p["side"], T.px(p["sl"], p["symbol"]),
                                                             T.px(new, p["symbol"])))

    def modify_sl(self, p, new):
        if self.dry:
            return True
        if self.cfg.get("mode") == "mt5":
            mt5, _ = order.mt5_guard(self.cfg)
            ok, _info = order.mt5_send(mt5, {"action": mt5.TRADE_ACTION_SLTP, "position": int(p["id"]),
                                             "symbol": p.get("broker_symbol", p["symbol"]), "sl": float(new),
                                             "tp": float(p.get("tp") or 0.0)})
            return ok
        st = T.paper_state(self.cfg)
        for q in st["positions"]:
            if q["id"] == p["id"]:
                q["sl"] = new
        T.save_paper(st)
        return True

    def close_all(self, acct, why):
        if self.dry:
            self.say("dry run: would close %d trades (%s)" % (len(acct["positions"]), why))
            return
        if self.cfg.get("mode") == "mt5":
            mt5, _ = order.mt5_guard(self.cfg)
            for x in mt5.orders_get() or []:
                order.mt5_send(mt5, {"action": mt5.TRADE_ACTION_REMOVE, "order": int(x.ticket)})
            for p in acct["positions"]:
                bs = p.get("broker_symbol", p["symbol"])
                si, tk = mt5.symbol_info(bs), mt5.symbol_info_tick(bs)
                long = p["side"] == "buy"
                ok, info = order.mt5_send(mt5, {
                    "action": mt5.TRADE_ACTION_DEAL, "position": int(p["id"]), "symbol": bs, "volume": float(p["volume"]),
                    "type": mt5.ORDER_TYPE_SELL if long else mt5.ORDER_TYPE_BUY, "price": tk.bid if long else tk.ask,
                    "deviation": 30, "magic": int(self.cfg.get("magic", 2126)), "comment": "tadawal robot flat",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": order.filling(mt5, si)})
                self.say("closed %s %s (%s): %s" % (p["symbol"], p["side"], why, "ok" if ok else info))
        else:
            st = T.paper_state(self.cfg)
            st["pending"] = []
            for pos in list(st["positions"]):
                try:
                    px = T.get_price(pos["symbol"], self.cfg)["price"]
                except SystemExit:
                    px = pos.get("price") or pos["entry"]
                pnl = order.ledger_close(st, pos, px, why)
                self.say("closed %s %s at %s, %+.2f (%s)" % (pos["symbol"], pos["side"], T.px(px, pos["symbol"]), pnl, why))
            T.save_paper(st)

    # -------------------------------------------------------------- commands from Telegram
    def telegram_commands(self):
        if not self.tg.token:
            return
        off = int(self.state.get("tg_offset", 0))
        for u in self.tg.updates(off):
            self.state["tg_offset"] = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip().lower()
            if not self.tg.chat_id or chat != self.tg.chat_id:
                log("ignored Telegram message from chat %s" % chat)
                continue
            if text in ("/stop", "/pause", "ايقاف", "وقف"):
                self.rc["enabled"] = False
                save_rc(self.rc)
                self.say("paused: no new trades (open trades keep their stops). /start to resume")
            elif text in ("/start", "/resume", "تشغيل"):
                self.rc["enabled"] = True
                save_rc(self.rc)
                self.say("resumed")
            elif text in ("/flat", "سكر", "سكر الكل"):
                self.rc["flat_requested"] = True
                save_rc(self.rc)
                self.say("closing everything now and pausing")
            elif text in ("/status", "/today", "الحالة"):
                self.tg.send(self.status_text())

    def status_text(self):
        acct = T.account_snapshot(self.cfg)
        st = self.day_stats()
        L = ["Robot %s | %s | balance %.2f equity %.2f" % ("ON" if self.rc.get("enabled") else "PAUSED", self.cfg.get("mode"),
                                                          acct["balance"], acct["equity"]),
             "today: %d trades, %d losses, P&L %+.2f | week %+.2f" % (st["trades"], st["losses"], st["pnl_today"], st["week_pnl"])]
        L += ["%s %s %s @%s P&L %+.2f" % (p["side"], p["volume"], p["symbol"], T.px(p["entry"], p["symbol"]), p.get("profit", 0))
              for p in acct["positions"]]
        return "\n".join(L)

    # -------------------------------------------------------------- one cycle
    def cycle(self):
        self.rc = load_rc() if os.path.exists(rc_path()) else self.rc
        self.telegram_commands()
        if self.cfg.get("mode") not in ("paper", "mt5"):
            raise SystemExit("the robot runs in mode paper or mt5 (config mode is %r)" % self.cfg.get("mode"))
        if self.cfg.get("mode") == "mt5":
            mt5, ai = order.mt5_guard(self.cfg)           # refuses REAL unless allow_live
            if ai.trade_mode == 2 and not self.rc.get("live"):
                raise SystemExit("REAL account: set \"live\": true in trader/robot.json as well (after the demo period)")
        else:
            events = order.ledger_sync(self.cfg)
            for e in events:
                self.say(e)
        acct = T.account_snapshot(self.cfg)
        self.manage(acct)
        acct = T.account_snapshot(self.cfg)
        stats = self.day_stats()
        blocks = self.entry_blocks(acct, stats)
        strat = strategy_of(self.rc)
        last = self.state.setdefault("last_bar", {})
        done = []
        for sym in [T.norm_symbol(x) for x in self.rc.get("pairs", [])]:
            try:
                b = self.closed_bars(sym)
            except SystemExit:
                done.append("%s: no data" % sym)
                continue
            if len(b) < 210:
                done.append("%s: not enough bars" % sym)
                continue
            if str(last.get(sym)) == str(b.t[-1]):
                continue                                   # this candle was already evaluated
            last[sym] = b.t[-1]
            side = self.signal(b, strat)
            if not side:
                done.append("%s: no signal" % sym)
                continue
            why = list(blocks)
            bias = self.bias(sym)
            if bias == "none" or (bias in ("buy", "sell") and bias != side):
                why.append("bias is %s" % bias)
            nb = self.news_block(sym)
            if nb:
                why.append("news: " + nb)
            if any(p["symbol"] == sym for p in acct["positions"]):
                why.append("already in a %s trade" % sym)
            if why:
                self.say("%s %s signal skipped: %s" % (sym, side, "; ".join(why)), push=False)
                done.append("%s: %s skipped" % (sym, side))
                continue
            o, s = self.build_order(sym, side, b, strat, acct)
            if o["volume"] <= 0:
                self.say("%s %s signal skipped: %s" % (sym, side, "; ".join(s["notes"]) or "size 0"), push=False)
                continue
            ok, msg = self.place(o, acct)
            self.say("%s %s %s lots %s @%s SL %s (%s pips) TP %s | risk %.2f | %s" % (
                "OPENED" if ok else "NOT OPENED", side.upper(), o["volume"], sym, T.px(o["entry"], sym), T.px(o["sl"], sym),
                s["stop_pips"], T.px(o["tp"], sym), s["risk_cash"], msg))
            if ok:
                stats["trades"] += 1
                acct = T.account_snapshot(self.cfg)
                blocks = self.entry_blocks(acct, stats)
            done.append("%s: %s %s" % (sym, side, "opened" if ok else "failed"))
        self.save_state("running" if self.rc.get("enabled") else "paused")
        return {"blocks": blocks, "pairs": done, "messages": self.messages}


def _ts(s):
    try:
        d = dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=T.NY)


# ------------------------------------------------------------------ CLI

def heartbeat_age():
    st = T.load_json(T.tpath("robot_state.json"), {}) or {}
    h = _ts(st.get("heartbeat"))
    return (T.ny_now() - h).total_seconds() / 60 if h else None, st


def run_loop(a):
    lock = T.tpath("robot.lock")
    age, _ = heartbeat_age()
    if os.path.exists(lock) and age is not None and age < 3:
        T.die("another robot seems to be running (heartbeat %.1f min ago). Stop it first." % age)
    with open(lock, "w") as f:
        f.write(str(os.getpid()))
    r = Robot()
    r.say("started (%s mode, %s, %s on %s, risk %s%%)" % (r.cfg.get("mode"), strategy_of(r.rc)["name"],
                                                         "/".join(r.rc["pairs"]), r.rc["tf"], r.rc["risk_pct"]))
    errors, last_cal = 0, 0
    try:
        while True:
            if time.time() - last_cal > 3600:
                last_cal = time.time()
                try:
                    _sibling("economic-analyst", "econ").fetch()
                except BaseException as e:  # noqa: B902 - the feed is optional
                    log("calendar refresh failed: %s" % e)
            try:
                r.messages = []
                out = r.cycle()
                errors = 0
                if a.verbose:
                    print("; ".join(out["pairs"]) or "-", "|", "; ".join(out["blocks"]) or "entries allowed", flush=True)
            except SystemExit as e:
                r.say("STOPPED: %s" % e)
                raise
            except Exception as e:
                errors += 1
                log("error: %s\n%s" % (e, traceback.format_exc()))
                if errors in (1, 5) or errors % 30 == 0:
                    r.say("error #%d: %s (will retry)" % (errors, e))
                if errors >= 10:
                    T._MT5_READY = None   # force a reconnect
            time.sleep(max(5, int(r.rc.get("poll_seconds", 20))))
    except KeyboardInterrupt:
        r.say("stopped by keyboard")
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def backtest(a):
    rc = load_rc()
    strat = strategy_of(rc)
    cfg = T.load_config()
    bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6}.get(rc["tf"], 24)
    L = ["ROBOT BACKTEST  %s on %s, %s sessions, no overnight, risk %s%%, stop %s ATR, target %sR" % (
        strat["name"], rc["tf"], "/".join(rc["sessions"]) or "all", rc["risk_pct"], strat["sl_atr"], strat["tp_r"])]
    for sym in rc["pairs"]:
        try:
            b = T.get_bars(sym, rc["tf"], a.days * bars_per_day + 250, cfg=cfg)
        except SystemExit:
            L.append("%s: no data" % sym)
            continue
        spread = (cfg.get("max_spread_by_symbol") or {}).get(T.norm_symbol(sym), 1.0 if "JPY" not in sym else 1.5)
        o = bt.backtest(b, strat["rules"], strat["name"], {
            "capital": a.capital, "risk_pct": rc["risk_pct"], "sl_atr": strat["sl_atr"], "tp_r": strat["tp_r"],
            "spread_pips": a.spread if a.spread is not None else spread / 2, "no_overnight": True,
            "last_entry_ny": cfg.get("last_entry_ny", "16:00"), "flat_ny": cfg.get("flat_by_ny", "16:45"),
            "sessions": rc["sessions"]})
        m = o["metrics"]
        L.append("%s  %s..%s  trades %s | return %s%% | max DD %s%% | win %s%% | PF %s | expectancy %sR" % (
            sym, o["from"], o["to"], m.get("trades"), m.get("return_pct"), m.get("max_drawdown_pct"),
            m.get("win_rate_pct"), m.get("profit_factor"), m.get("expectancy_r")))
        if (m.get("trades") or 0) < 30:
            L.append("   too few trades to trust; test a longer period (MT5 history via --days)")
        elif (m.get("profit_factor") or 0) < 1.1:
            L.append("   not profitable enough after costs: do not run this live")
    T.emit({"text": "\n".join(L)}, a.json)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--pairs", nargs="*", default=["EURUSD", "XAUUSD"])
    s.add_argument("--strategy", choices=sorted(PRESETS), default="trend_pullback")
    s.add_argument("--tf", default="M15")
    s.add_argument("--risk", type=float, default=0.5)
    s.add_argument("--sessions", nargs="*", default=["london", "newyork"])
    s.add_argument("--force", action="store_true")
    sub.add_parser("show")
    st = sub.add_parser("set")
    st.add_argument("key")
    st.add_argument("values", nargs="+")
    bi = sub.add_parser("bias")
    bi.add_argument("pair")
    bi.add_argument("side", choices=["buy", "sell", "both", "none"])
    bi.add_argument("--note", default="")
    for name in ("status", "pause", "resume", "flat", "telegram-chats", "telegram-test"):
        sub.add_parser(name)
    o = sub.add_parser("once")
    o.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--verbose", action="store_true")
    b = sub.add_parser("backtest")
    b.add_argument("--days", type=int, default=60)
    b.add_argument("--capital", type=float, default=10000.0)
    b.add_argument("--spread", type=float, help="spread in pips (default: half your max_spread_by_symbol)")
    a = ap.parse_args(argv)

    if a.cmd == "setup":
        if os.path.exists(rc_path()) and not a.force:
            T.die("robot already set up (robot.py show); use --force to start again")
        rc = dict(DEFAULTS, pairs=[T.norm_symbol(x) for x in a.pairs], strategy=a.strategy, tf=a.tf.upper(),
                  risk_pct=a.risk, sessions=[SESSION_NAMES[x.lower()] for x in a.sessions if x.lower() != "any"])
        save_rc(rc)
        c = T.load_json(T.tpath("config.json"), {}) or {}
        c["no_overnight"] = True
        T.save_json(T.tpath("config.json"), c)
        a.cmd = "show"
    rc = load_rc()
    if a.cmd == "show":
        stg = strategy_of(rc)
        txt = ["ROBOT  %s | strategy %s (%s) | %s on %s" % ("ON" if rc["enabled"] else "PAUSED", stg["name"],
                                                          PRESETS.get(stg["name"], {}).get("about", "custom rules"),
                                                          "/".join(rc["pairs"]), rc["tf"]),
               "risk %s%% per trade | stop %s ATR | target %sR | breakeven at %sR | trail %s ATR" % (
                   rc["risk_pct"], stg["sl_atr"], stg["tp_r"], rc["breakeven_at_r"], rc["trail_atr"] or "-"),
               "max %s trades/day, stop after %s losses, weekly loss limit %s%%, max %s open" % (
                   rc["max_trades_per_day"], rc["max_losses_per_day"], rc["max_weekly_loss_pct"], rc["max_open"]),
               "sessions %s | news pause %s min before / %s after | NO OVERNIGHT (always)" % (
                   "/".join(rc["sessions"]) or "all", rc["news_pause_minutes"][0], rc["news_pause_minutes"][1]),
               "bias: " + (", ".join("%s %s" % (k, v.get("side") if isinstance(v, dict) else v) for k, v in rc["bias"].items())
                           or "both directions"),
               "real account: %s" % ("ALLOWED" if rc.get("live") and T.load_config().get("allow_live") else "refused (demo/paper only)"),
               "rules: " + json.dumps(stg["rules"])]
        T.emit({"robot": rc, "text": "\n".join(txt)}, a.json)
        return 0
    if a.cmd == "set":
        k, v = a.key, a.values
        if k == "pairs":
            rc[k] = [T.norm_symbol(x) for x in v]
        elif k == "sessions":
            rc[k] = [SESSION_NAMES[x.lower()] for x in v if x.lower() != "any"]
        elif k in ("strategy",):
            if v[0] not in PRESETS:
                T.die("strategy must be one of %s" % ", ".join(PRESETS))
            rc[k], rc["rules"] = v[0], None
        elif k == "tf":
            rc[k] = v[0].upper()
        elif k == "rules":
            rc[k] = T.load_json(v[0])
        elif k in ("risk_pct", "sl_atr", "tp_r", "max_weekly_loss_pct", "breakeven_at_r", "trail_atr"):
            rc[k] = None if v[0].lower() in ("none", "off") else float(v[0])
        elif k in ("max_trades_per_day", "max_losses_per_day", "max_open", "poll_seconds", "bias_hours"):
            rc[k] = int(v[0])
        elif k == "news_pause_minutes":
            rc[k] = [int(x) for x in v[:2]]
        elif k in ("require_calendar", "live"):
            rc[k] = v[0].lower() in ("true", "yes", "1", "on")
        elif k == "telegram_chat_id":
            rc[k] = v[0]
        else:
            T.die("unknown key %s" % k)
        if k == "risk_pct" and rc[k] and rc[k] > float(T.load_config().get("risk_per_trade_pct", 1.0)):
            T.die("robot risk %s%% is above your account limit risk_per_trade_pct; lower it" % rc[k])
        save_rc(rc)
        return main(["show"])
    if a.cmd == "bias":
        rc.setdefault("bias", {})[T.norm_symbol(a.pair)] = {"side": a.side, "set": T.ny_now().isoformat(timespec="seconds"),
                                                             "note": a.note}
        save_rc(rc)
        T.emit({"text": "bias %s = %s for the next %sh%s" % (T.norm_symbol(a.pair), a.side, rc.get("bias_hours", 24),
                                                             " (" + a.note + ")" if a.note else "")}, a.json)
        return 0
    if a.cmd in ("pause", "resume", "flat"):
        if a.cmd == "flat":
            rc["flat_requested"] = True
        else:
            rc["enabled"] = a.cmd == "resume"
        save_rc(rc)
        age, _ = heartbeat_age()
        note = "" if age is not None and age < 3 else "\nthe robot is not running now: run robot.py once to apply it"
        T.emit({"text": {"pause": "paused: no new trades", "resume": "resumed",
                         "flat": "flat requested: the robot closes everything on its next cycle and pauses"}[a.cmd] + note}, a.json)
        return 0
    if a.cmd == "status":
        age, st = heartbeat_age()
        r = Robot(rc=rc, telegram=Telegram(token=""))
        alive = "running (heartbeat %.1f min ago)" % age if age is not None and age < 3 else (
            "NOT RUNNING (last heartbeat %s)" % (st.get("heartbeat") or "never"))
        acct = T.account_snapshot(r.cfg)
        blocks = r.entry_blocks(acct, r.day_stats())
        T.emit({"text": "robot %s\n%s\nnew entries: %s" % (alive, r.status_text(),
                                                          "allowed" if not blocks else "blocked (" + "; ".join(blocks) + ")")}, a.json)
        return 0
    if a.cmd == "once":
        r = Robot(dry_run=a.dry_run)
        out = r.cycle()
        T.emit(dict(out, text="\n".join(out["messages"] + out["pairs"] + ["entries: " + ("; ".join(out["blocks"]) or "allowed")])),
               a.json)
        return 0
    if a.cmd == "run":
        return run_loop(a)
    if a.cmd == "backtest":
        return backtest(a)
    tg = Telegram(rc.get("telegram_chat_id"))
    if not tg.token:
        T.die("set the TELEGRAM_BOT_TOKEN environment variable first (see SKILL.md)")
    if a.cmd == "telegram-chats":
        ups = tg.updates(0)
        chats = sorted({(str(u["message"]["chat"]["id"]), u["message"]["chat"].get("first_name", "")) for u in ups if u.get("message")})
        T.emit({"text": "send any message to your bot first, then run this again.\n" + "\n".join(
            "chat id %s (%s): robot.py set telegram_chat_id %s" % (c, n, c) for c, n in chats)}, a.json)
        return 0
    ok = tg.send("🤖 Tadawal robot: test message. Commands: /status /stop /start /flat")
    T.emit({"text": "sent" if ok else "failed: check the token and telegram_chat_id"}, a.json)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
