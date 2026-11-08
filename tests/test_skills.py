"""Offline tests for the Tadawal skill pack:  python3 -m unittest discover -s tests -v"""
import datetime as dt
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SK = os.path.join(ROOT, "skills")
FX = os.path.join(HERE, "fixtures")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SK, "_lib"))
import synth  # noqa: E402
import tadawal as T  # noqa: E402


def load(skill, mod):
    spec = importlib.util.spec_from_file_location("%s_%s" % (skill.replace("-", "_"), mod),
                                                  os.path.join(SK, skill, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


timer = load("entry-timer", "timer")
scan = load("chart-scanner", "scan")
risk = load("risk-manager", "risk")
exitp = load("exit-planner", "exit")
bt = load("time-machine", "backtest")
alerts = load("price-alerts", "alerts")
order = load("order-executor", "order")
budget = load("budget-manager", "budget")
news = load("news-monitor", "news")
ptr = load("pelosi-tracker", "ptr")
earn = load("earnings-analyst", "earnings")

OPEN_CLOCK = {"open": True, "status": "NYSE open", "now_ny": "2026-11-09 10:30 Mon", "minutes_since_open": 60,
              "minutes_to_close": 330}


class TempTrader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TADAWAL_DIR"] = self.tmp
        T._MT5_READY = False  # never touch a real terminal in tests
        self._saved = (T.get_price, T.market_clock)

    def tearDown(self):
        T.get_price, T.market_clock = self._saved
        os.environ.pop("TADAWAL_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        T._MT5_READY = None

    def cfg(self, **kw):
        c = T.load_config()
        c.update(kw)
        T.save_json(T.tpath("config.json"), c)
        return T.load_config()


def rd(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = fn(*a, **kw)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- library

class TestLib(unittest.TestCase):
    def test_sma_ema_rsi(self):
        x = [float(i) for i in range(1, 21)]
        self.assertEqual(T.sma(x, 5)[4], 3.0)
        self.assertIsNone(T.sma(x, 5)[3])
        self.assertAlmostEqual(T.ema(x, 5)[-1], 18.0, places=6)
        self.assertEqual(T.rsi(x, 14)[-1], 100.0)
        down = list(reversed(x))
        self.assertEqual(T.rsi(down, 14)[-1], 0.0)

    def test_atr_positive(self):
        b = synth.walk(100)
        a = T.atr(b, 14)
        self.assertIsNone(a[12])
        self.assertTrue(all(v > 0 for v in a[13:]))

    def test_market_clock(self):
        ny = T.NY
        self.assertTrue(T.market_clock(dt.datetime(2026, 11, 9, 10, 0, tzinfo=ny))["open"])
        thanks = T.market_clock(dt.datetime(2026, 11, 26, 11, 0, tzinfo=ny))
        self.assertFalse(thanks["open"])
        self.assertIn("holiday", thanks["status"])
        self.assertTrue(thanks["next_open"].startswith("2026-11-27 09:30"))
        early = T.market_clock(dt.datetime(2026, 11, 27, 13, 30, tzinfo=ny))
        self.assertFalse(early["open"])
        self.assertTrue(T.market_clock(dt.datetime(2026, 11, 27, 12, 30, tzinfo=ny))["early_close"])
        sat = T.market_clock(dt.datetime(2026, 11, 7, 12, 0, tzinfo=ny))
        self.assertIn("weekend", sat["status"])
        self.assertTrue(sat["next_open"].startswith("2026-11-09"))

    def test_csv_mt5_format(self):
        p = os.path.join(tempfile.mkdtemp(), "x.csv")
        with open(p, "w") as f:
            f.write("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n")
            f.write("2026.01.05\t00:00:00\t10\t11\t9\t10.5\t100\n2026.01.02\t00:00:00\t9\t10\t8\t9.5\t90\n")
        b = T.load_csv(p, "X", "D1")
        self.assertEqual(b.c, [9.5, 10.5])  # sorted by time
        self.assertEqual(b.date(0), "2026-01-01" if T.NY else "2026-01-02")

    def test_order_hash_changes_with_contents(self):
        o = {"symbol": "AAPL", "side": "buy", "type": "limit", "volume": 10, "entry": 190, "sl": 185, "tp": 200}
        h = T.order_hash(o)
        self.assertEqual(h, T.order_hash(dict(o, note="x", status="passed")))
        self.assertNotEqual(h, T.order_hash(dict(o, volume=11)))
        self.assertNotEqual(h, T.order_hash(dict(o, sl=184)))


# ---------------------------------------------------------------- analysis skills

class TestEntryTimer(unittest.TestCase):
    def test_uptrend_scores_long(self):
        b = synth.walk(400, drift=0.003, sigma=0.01, seed=2)
        o = timer.analyze(b, None, dict(OPEN_CLOCK))
        self.assertEqual(o["trend"], "up")
        self.assertGreater(o["long"]["score"], o["short"]["score"])
        self.assertIn(o["long"]["verdict"], ("ENTRY ZONE", "WAIT", "NO TRADE"))
        self.assertIn("LONG", o["text"])

    def test_downtrend_scores_short(self):
        b = synth.walk(400, drift=-0.003, sigma=0.01, seed=4)
        o = timer.analyze(b, None, dict(OPEN_CLOCK))
        self.assertEqual(o["trend"], "down")
        self.assertGreater(o["short"]["score"], o["long"]["score"])


class TestChartScanner(unittest.TestCase):
    def names(self, b, k=1.0):
        return [f["name"] for f in scan.scan(b, k)["findings"]]

    def test_double_top_confirmed(self):
        b = synth.path([80, 90, 100, 110, 120, 112, 105, 112, 120, 112, 103, 100], steps=8)
        o = scan.scan(b, 1.0)
        dtop = [f for f in o["findings"] if f["name"] == "double top"]
        self.assertTrue(dtop)
        self.assertTrue(dtop[0]["status"].startswith("confirmed"))
        self.assertLess(dtop[0]["target"], dtop[0]["level"])

    def test_head_and_shoulders(self):
        b = synth.path([70, 80, 100, 92, 110, 92, 100, 90, 85], steps=10)
        self.assertIn("head and shoulders", self.names(b))

    def test_symmetrical_triangle(self):
        b = synth.path([70, 80, 120, 100, 116, 103, 113, 106, 111, 108], steps=10)
        self.assertIn("symmetrical triangle", self.names(b))

    def test_bullish_engulfing(self):
        b = synth.walk(80, drift=-0.004, sigma=0.004, seed=9)
        i = len(b) - 1
        b.o[i - 1], b.c[i - 1] = 100.0, 98.0
        b.h[i - 1], b.l[i - 1] = 100.5, 97.5
        b.o[i], b.c[i], b.h[i], b.l[i] = 97.8, 101.0, 101.2, 97.6
        self.assertIn("bullish engulfing", [f["name"] for f in scan.candles(b, 1)])

    def test_svg(self):
        b = synth.walk(200)
        o = scan.scan(b)
        p = os.path.join(tempfile.mkdtemp(), "c.svg")
        scan.svg(b, o, p)
        self.assertIn("<svg", rd(p))


# ---------------------------------------------------------------- risk

STOCK = {"symbol": "AAPL", "tick_size": 0.01, "tick_value": 0.01, "contract_size": 1, "volume_min": 1,
         "volume_max": 1e9, "volume_step": 1, "digits": 2, "source": "paper (shares)"}
CFD = {"symbol": "AAPL.US", "tick_size": 0.01, "tick_value": 1.0, "contract_size": 100, "volume_min": 0.01,
       "volume_max": 100, "volume_step": 0.01, "digits": 2, "source": "mt5"}


class TestRisk(TempTrader):
    def test_size_shares(self):
        o = risk.size("AAPL", 100, 95, 115, balance=10000, cfg=T.load_config(), spec=STOCK)
        self.assertEqual(o["volume"], 20)          # 100 risk / 5 per share
        self.assertEqual(o["risk_cash"], 100.0)
        self.assertEqual(o["rr"], 3.0)

    def test_size_capped_by_position_pct(self):
        o = risk.size("AAPL", 100, 99, balance=10000, cfg=T.load_config(), spec=STOCK)
        self.assertEqual(o["volume"], 25)          # 2500 cap / 100
        self.assertTrue(any("max_position_pct" in n for n in o["notes"]))

    def test_size_mt5_lots(self):
        o = risk.size("AAPL", 100, 95, balance=10000, cfg=T.load_config(), spec=CFD)
        # 1 lot = 100 shares -> 5$ * 100 = 500 per lot; 100 risk -> 0.2 lots; cap 2500/(100*100)=0.25
        self.assertAlmostEqual(o["volume"], 0.2)

    def check(self, orders, acct=None, today=0.0, **cfg):
        c = T.load_config()
        c.update(cfg)
        c.setdefault("max_total_risk_pct", 6.0)
        acct = acct or {"balance": 10000, "positions": []}
        return risk.check_orders(orders, c, acct, today, None, lambda s: STOCK)

    def test_check_pass_and_blocks(self):
        good = {"id": "o1", "symbol": "AAPL", "side": "buy", "type": "limit", "volume": 20, "entry": 100, "sl": 95, "tp": 110}
        self.assertEqual(self.check([good])[0][1], "PASS")
        cases = [
            (dict(good, sl=None), "no stop-loss"),
            (dict(good, sl=105), "wrong side"),
            (dict(good, volume=40), "over your"),
            (dict(good, tp=104), "reward:risk"),
            (dict(good, side="sell", sl=105, tp=90), "short selling is off"),
            (dict(good, volume=2.5), "not a multiple"),
        ]
        for o, why in cases:
            v, reasons = self.check([o])[0][1:3]
            self.assertEqual(v, "BLOCK", o)
            self.assertTrue(any(why in r for r in reasons), (why, reasons))

    def test_daily_loss_and_max_positions(self):
        good = {"symbol": "AAPL", "side": "buy", "volume": 10, "entry": 100, "sl": 95, "tp": 110}
        self.assertEqual(self.check([good], today=-301)[0][1], "BLOCK")
        acct = {"balance": 10000, "positions": [{"symbol": "X%d" % i, "side": "buy", "volume": 1, "entry": 10, "sl": 9}
                                                for i in range(5)]}
        r = self.check([good], acct=acct)[0]
        self.assertEqual(r[1], "BLOCK")
        self.assertTrue(any("open positions" in x for x in r[2]))

    def test_cli_size_write_check(self):
        self.cfg()
        rc, out, _ = quiet(risk.main, ["size", "AAPL", "--entry", "190", "--stop", "185", "--target", "200", "--write"])
        self.assertEqual(rc, 0)
        T.get_price = lambda s, cfg=None, source=None: {"price": 190.0}
        rc, out, _ = quiet(risk.main, ["check"])
        self.assertIn("PASS  o1", out)
        v = T.load_json(T.tpath("risk_verdicts.json"))
        self.assertEqual(list(v.values())[0]["verdict"], "PASS")


# ---------------------------------------------------------------- exits

class TestExit(unittest.TestCase):
    def test_plan_long(self):
        b = synth.walk(300, seed=11)
        e = b.c[-1]
        o = exitp.plan(b, e)
        a = o["atr14"]
        self.assertLess(o["stop"], e)
        self.assertGreaterEqual(e - o["stop"], 0.99 * a)
        self.assertLessEqual(e - o["stop"], 3.01 * a)
        self.assertGreater(o["tp_single"], e)
        self.assertGreaterEqual(o["rr_single"], 1.49)

    def test_plan_short(self):
        b = synth.walk(300, seed=12)
        o = exitp.plan(b, b.c[-1], side="sell")
        self.assertGreater(o["stop"], b.c[-1])
        self.assertLess(o["tp_single"], b.c[-1])

    def test_trail_breakeven_and_never_loosen(self):
        closes = [100 + 0.5 * i for i in range(60)]
        b = synth.from_closes(closes, spread=0.002)
        entry, stop = closes[30], closes[30] - 3
        o = exitp.trail(b, entry, stop, "buy", 30)
        self.assertTrue(o["move"])
        self.assertGreaterEqual(o["stop_new"], entry)
        o2 = exitp.trail(b, entry, o["stop_new"] + 100 - 100, "buy", 30)
        self.assertGreaterEqual(o2["stop_new"], o["stop_new"])

    def test_trail_reports_stop_hit(self):
        closes = [100 - 0.5 * i for i in range(40)]
        b = synth.from_closes(closes, spread=0.002)
        o = exitp.trail(b, closes[10], closes[10] - 2, "buy", 10)
        self.assertTrue(any("through the stop" in e for e in o["events"]))


# ---------------------------------------------------------------- backtest

class TestBacktest(unittest.TestCase):
    def test_fill_next_open_no_lookahead(self):
        b = synth.walk(300, drift=0.002, seed=3)
        rules = {"entry_long": [["close", ">", 0]], "exit_long": [["close", "<", 0]]}
        trades, curve = bt.run(b, rules, sl_atr=None, slippage_bps=0)
        self.assertEqual(trades[0]["entry"], round(b.o[2], 4))   # signal on bar 1 close -> bar 2 open
        self.assertEqual(trades[0]["why"], "still open at end")

    def test_stop_first_when_both_hit(self):
        closes = [100.0] * 30
        b = synth.from_closes(closes, spread=0.0)
        b.h[25], b.l[25] = 130.0, 70.0
        rules = {"entry_long": [["close", ">=", 100]]}
        trades, _ = bt.run(b, rules, sl_atr=None, tp_r=None, slippage_bps=0, start_ts=b.t[20])
        # no stop configured -> held; now with explicit stop/target via ATR
        b2 = synth.from_closes(closes, spread=0.01)
        b2.h[25], b2.l[25] = 200.0, 1.0
        trades, _ = bt.run(b2, rules, sl_atr=2, tp_r=1, slippage_bps=0, start_ts=b2.t[20])
        self.assertEqual(trades[0]["why"], "stop")

    def test_metrics_and_grid_strategies(self):
        b = synth.walk(800, drift=0.0006, sigma=0.015, seed=5)
        for name, f in bt.STRATEGIES.items():
            o = bt.backtest(b, f({"fast": 20, "slow": 50, "rsi_low": 30, "rsi_high": 70, "lookback": 20}), name,
                            {"capital": 10000, "risk_pct": 1, "sl_atr": 2, "slippage_bps": 2})
            m = o["metrics"]
            self.assertIn("return_pct", m, name)
            self.assertLessEqual(m["max_drawdown_pct"], 0)
            self.assertIn(name, bt.render(o))

    def test_rules_series(self):
        S = bt.Series(synth.walk(100))
        for s in ("sma20", "ema50", "rsi14", "atr14", "macd_hist", "bb_upper20", "highest10", "lowest10", "vol_sma20"):
            self.assertEqual(len(S.get(s)), 100)
        with self.assertRaises(SystemExit):
            quiet(S.get, "nonsense9")
            S.get("nonsense9")


# ---------------------------------------------------------------- alerts

class TestAlerts(unittest.TestCase):
    def test_evaluate(self):
        al = [{"id": 1, "symbol": "AAPL", "kind": "above", "level": 200, "repeat": True},
              {"id": 2, "symbol": "AAPL", "kind": "cross", "level": 195},
              {"id": 3, "symbol": "AAPL", "kind": "pct", "level": 3},
              {"id": 4, "symbol": "SPY", "kind": "bell", "level": None}]
        rung = []
        for px in (190, 201, 202, 199, 203):
            rung.append([r["id"] for r in alerts.evaluate(al, {"AAPL": {"price": px, "prev_close": 194}},
                                                          dict(OPEN_CLOCK), "2026-11-09")])
        self.assertEqual(rung[0], [4])
        self.assertEqual(sorted(rung[1]), [1, 2, 3])
        self.assertEqual(rung[2], [])
        self.assertEqual(rung[3], [])
        self.assertEqual(rung[4], [1])


# ---------------------------------------------------------------- order executor

class FakeMT5(types.SimpleNamespace):
    """Just enough of the MetaTrader5 module for the executor."""

    def __init__(self, trade_mode=0):
        super().__init__()
        self.sent = []
        for k, v in dict(TRADE_ACTION_DEAL=1, TRADE_ACTION_PENDING=5, TRADE_ACTION_SLTP=6, TRADE_ACTION_REMOVE=8,
                         ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, ORDER_TYPE_BUY_LIMIT=2, ORDER_TYPE_SELL_LIMIT=3,
                         ORDER_TYPE_BUY_STOP=4, ORDER_TYPE_SELL_STOP=5, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1,
                         ORDER_FILLING_RETURN=2, ORDER_TIME_GTC=0, TRADE_RETCODE_DONE=10009,
                         TRADE_RETCODE_PLACED=10008, TIMEFRAME_D1=16408).items():
            setattr(self, k, v)
        self._mode = trade_mode

    def account_info(self):
        return types.SimpleNamespace(login=123, server="Demo-Server", trade_mode=self._mode, balance=10000.0,
                                     equity=10000.0, margin_free=10000.0, currency="USD")

    def terminal_info(self):
        return types.SimpleNamespace(trade_allowed=True)

    def symbol_select(self, s, on):
        return True

    def symbol_info(self, s):
        return types.SimpleNamespace(digits=2, filling_mode=1, trade_tick_size=0.01, trade_tick_value=0.01,
                                     trade_contract_size=1, volume_min=1, volume_max=1000, volume_step=1, point=0.01)

    def symbol_info_tick(self, s):
        return types.SimpleNamespace(bid=189.9, ask=190.1, last=190.0, time=0)

    def positions_get(self):
        return []

    def history_deals_get(self, a, b):
        return []

    def order_check(self, req):
        return types.SimpleNamespace(retcode=0, comment="ok")

    def order_send(self, req):
        self.sent.append(req)
        return types.SimpleNamespace(retcode=10009, comment="done", order=555, deal=777, price=req["price"],
                                     volume=req["volume"])

    def last_error(self):
        return (0, "")


class TestOrderExecutor(TempTrader):
    def test_affirmative(self):
        for y in ("yes", "Yes, place it", "نعم", "ايوه نفذ", "اي", "موافق", "تمام"):
            self.assertTrue(order.affirmative(y), y)
        for n in ("no", "yes but wait", "لا", "لا تنفذ", "ok don't", "", "maybe", "انتظر", "نعم بس لحظة", "مو الحين"):
            self.assertFalse(order.affirmative(n), n)

    def prepare(self, **cfg):
        self.cfg(**cfg)
        T.get_price = lambda s, cfg=None, source=None: {"price": 190.0, "prev_close": 188.0}
        T.market_clock = lambda now=None: dict(OPEN_CLOCK)
        quiet(risk.main, ["size", "AAPL", "--entry", "190", "--stop", "185", "--target", "200", "--write",
                          "--type", "market"])
        rc, out, _ = quiet(risk.main, ["check"])
        self.assertIn("PASS", out)
        o = T.load_orders()["orders"][0]
        return o, T.order_hash(o)

    def test_paper_flow(self):
        o, h = self.prepare()
        rc, out, err = quiet(order.main, ["tickets"])
        self.assertIn("READY  o1  ticket %s" % h, out)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", "deadbeef0000", "--confirm", "yes"])
        self.assertNotEqual(rc, 0)
        self.assertIn("does not match", err)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "no"])
        self.assertNotEqual(rc, 0)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "نعم"])
        self.assertEqual(rc, 0, err)
        self.assertIn("PLACED", out)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertIn("already placed", err)
        st = T.paper_state()
        self.assertEqual(len(st["positions"]), 1)
        pid = str(st["positions"][0]["id"])
        rc, out, err = quiet(order.main, ["modify", pid, "--sl", "180", "--confirm", "yes"])
        self.assertIn("refusing to loosen", err)
        rc, out, err = quiet(order.main, ["modify", pid, "--sl", "187", "--confirm", "yes"])
        self.assertEqual(rc, 0, err)
        T.get_price = lambda s, cfg=None, source=None: {"price": 186.0}
        rc, out, err = quiet(order.main, ["positions"])
        self.assertIn("stopped at 187", out)
        j = T.journal()["trades"]
        self.assertEqual(j[0]["status"], "closed")
        self.assertEqual(j[0]["pnl"], -3.0 * j[0]["volume"])
        log = T.load_json(T.tpath("executions.json"))["executions"]
        self.assertEqual(log[0]["user_confirmation"], "نعم")

    def test_edit_voids_pass(self):
        o, h = self.prepare()
        data = T.load_orders()
        data["orders"][0]["volume"] = 1
        T.save_orders(data)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", T.order_hash(data["orders"][0]), "--confirm", "yes"])
        self.assertNotEqual(rc, 0)
        self.assertIn("no PASS", err)

    def test_mt5_demo_places_with_stop(self):
        fake = FakeMT5(trade_mode=0)
        T._MT5_READY = fake
        o, h = self.prepare(mode="mt5")
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertEqual(rc, 0, err)
        req = fake.sent[0]
        self.assertEqual(req["sl"], 185.0)
        self.assertEqual(req["tp"], 200.0)
        self.assertEqual(req["type"], fake.ORDER_TYPE_BUY)
        self.assertEqual(req["price"], 190.1)

    def test_mt5_not_connected_is_a_clear_error(self):
        self.cfg(mode="mt5")
        rc, out, err = quiet(order.main, ["account"])
        self.assertNotEqual(rc, 0)
        self.assertIn("not connected", err)

    def test_mt5_real_account_refused(self):
        fake = FakeMT5(trade_mode=2)
        T._MT5_READY = fake
        o, h = self.prepare(mode="mt5")
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertNotEqual(rc, 0)
        self.assertIn("REAL account", err)
        self.assertEqual(fake.sent, [])


# ---------------------------------------------------------------- budget

class TestBudget(unittest.TestCase):
    def test_stats(self):
        tr = [{"status": "closed", "pnl": p, "closed_at": "2026-10-%02d" % (i + 1), "symbol": "A", "r": p / 50}
              for i, p in enumerate([100, -50, -50, 150, -50])]
        s = budget.stats(tr)
        self.assertEqual(s["trades"], 5)
        self.assertEqual(s["net_pnl"], 100.0)
        self.assertEqual(s["win_rate_pct"], 40.0)
        self.assertEqual(s["profit_factor"], round(250 / 150, 2))
        self.assertEqual(s["max_drawdown"], -100.0)
        self.assertEqual(s["max_losing_streak"], 2)


# ---------------------------------------------------------------- data parsers

class TestNews(unittest.TestCase):
    def test_parse_and_select(self):
        items = news.parse_rss(rd(os.path.join(FX, "yahoo-AAPL.xml"), "rb"), "AAPL")
        items += news.parse_8k_atom(rd(os.path.join(FX, "sec-8k-AAPL.xml"), "rb"), "AAPL")
        self.assertEqual(len(items), 5)
        sec = [i for i in items if i["source"] == "sec"]
        self.assertEqual(sec[0]["items"], ["2.02", "9.01"])
        self.assertIn("earnings", sec[0]["tags"])
        self.assertEqual(sec[1]["severity"], 3)
        beat = [i for i in items if "beats" in i["title"]][0]
        self.assertEqual(beat["hint"], "+")
        sel = news.select(items, {}, 0, False)
        self.assertEqual(sel[0]["severity"], 3)
        seen = {i["key"]: 1 for i in sel}
        self.assertEqual(news.select(items, seen, 0, False), [])


class TestPelosi(unittest.TestCase):
    def test_parse_ptr(self):
        out = ptr.parse_ptr_text(rd(os.path.join(FX, "ptr-sample.txt")), "2026-01-23")
        rows = out["rows"]
        self.assertEqual(len(rows), 3)
        g = rows[0]
        self.assertEqual((g["ticker"], g["tx"], g["owner"], g["asset_type"]), ("GOOGL", "buy", "spouse", "OP"))
        self.assertEqual((g["amount_low"], g["amount_high"]), (500001, 1000000))
        self.assertEqual(g["option"], {"contracts": 50, "right": "call", "strike": 150.0, "expiry": "1/15/2027"})
        self.assertEqual(g["days_to_disclose"], 9)
        self.assertEqual((rows[1]["ticker"], rows[1]["tx"]), ("NVDA", "sell (partial)"))
        self.assertEqual(rows[2]["owner"], "joint")
        self.assertEqual(out["filer"]["district"], "CA11")

    def test_index(self):
        rows = ptr.parse_index_zip(rd(os.path.join(FX, "2026FD.zip"), "rb"))
        p = ptr.ptrs(rows, ["pelosi"])
        self.assertEqual([r["doc_id"] for r in p], ["20035590", "20033725"])
        self.assertEqual(ptr.pdf_url(p[1]), "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033725.pdf")
        self.assertEqual(ptr.amount_range("Over $50,000,000"), (50000000, None))


class TestEarnings(unittest.TestCase):
    def test_quarters_and_derived_q4(self):
        facts = json.loads(rd(os.path.join(FX, "companyfacts-synthetic.json")))
        o = earn.analyze(facts, "SYN", 8)
        q = o["quarters"]
        self.assertEqual(len(q), 8)
        q4 = [r for r in q if r["end"] == "2026-09-26"][0]
        self.assertEqual(q4["revenue"], 110e9)
        self.assertIn("revenue", q4["derived"])
        self.assertAlmostEqual(q4["eps_diluted"], 2.05, places=2)
        self.assertEqual(q[-1]["revenue_yoy"], round((110 - 102) / 102 * 100, 1))
        self.assertEqual(o["ttm"]["revenue"], 449e9)
        self.assertIn("SYN", earn.render(o))


# ---------------------------------------------------------------- packaging

class TestPackaging(unittest.TestCase):
    EXPECTED = {"entry-timer", "news-monitor", "chart-scanner", "risk-manager", "time-machine", "exit-planner",
                "price-alerts", "order-executor", "budget-manager", "pelosi-tracker", "earnings-analyst"}

    def test_eleven_skills_with_valid_frontmatter(self):
        found = set()
        for d in os.listdir(SK):
            p = os.path.join(SK, d, "SKILL.md")
            if not os.path.isfile(p):
                continue
            txt = rd(p)
            m = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: >-\n(.*?)\n---\n", txt, re.S)
            self.assertTrue(m, d)
            self.assertEqual(m.group(1), d)
            desc = " ".join(m.group(2).split())
            self.assertLess(len(desc), 1024, d)
            found.add(d)
        self.assertEqual(found, self.EXPECTED)

    def test_plugin_manifests(self):
        for f in ("plugin.json", "marketplace.json"):
            json.loads(rd(os.path.join(ROOT, ".claude-plugin", f)))

    def test_scripts_have_help(self):
        for d in self.EXPECTED:
            for f in os.listdir(os.path.join(SK, d)):
                if f.endswith(".py"):
                    m = load(d, f[:-3])
                    rc, out, _ = quiet(m.main, ["--help"])
                    self.assertEqual(rc, 0, (d, f))


if __name__ == "__main__":
    unittest.main()
