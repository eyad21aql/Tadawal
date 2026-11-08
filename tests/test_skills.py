"""Offline tests for the Tadawal forex skill pack:  python3 -m unittest discover -s tests -v"""
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
econ = load("economic-analyst", "econ")
cot = load("cot-tracker", "cot")

NY = T.NY
OPEN_CLOCK = {"open": True, "status": "forex open: London/New York overlap", "now_ny": "2026-11-09 10:30 Mon",
              "now_local": "Mon 2026-11-09 18:30", "sessions": ["London", "New York"], "warnings": [],
              "minutes_to_close": 5000}
PRICES = {"EURUSD": 1.0850, "USDJPY": 156.39, "GBPUSD": 1.2316, "GBPJPY": 192.60, "EURGBP": 0.8810,
          "USDCHF": 0.9094, "XAUUSD": 2650.0, "AUDUSD": 0.6269}


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


class TempTrader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TADAWAL_DIR"] = self.tmp
        T._MT5_READY = False  # never touch a real terminal in tests
        self._saved = (T.market_clock, dict(T._PRICE_OVERRIDE))
        T._PRICE_OVERRIDE.clear()
        T._PRICE_OVERRIDE.update(PRICES)

    def tearDown(self):
        T.market_clock = self._saved[0]
        T._PRICE_OVERRIDE.clear()
        T._PRICE_OVERRIDE.update(self._saved[1])
        os.environ.pop("TADAWAL_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        T._MT5_READY = None

    def cfg(self, **kw):
        c = T.load_config()
        c.update(kw)
        T.save_json(T.tpath("config.json"), c)
        return T.load_config()


# ---------------------------------------------------------------- library

class TestLib(unittest.TestCase):
    def test_indicators(self):
        x = [float(i) for i in range(1, 21)]
        self.assertEqual(T.sma(x, 5)[4], 3.0)
        self.assertAlmostEqual(T.ema(x, 5)[-1], 18.0, places=6)
        self.assertEqual(T.rsi(x, 14)[-1], 100.0)
        self.assertEqual(T.rsi(list(reversed(x)), 14)[-1], 0.0)
        a = T.atr(synth.walk(100), 14)
        self.assertTrue(all(v > 0 for v in a[13:]))

    def test_symbols_and_pips(self):
        self.assertEqual(T.norm_symbol("eur/usd"), "EURUSD")
        self.assertEqual(T.norm_symbol("EURUSD=X"), "EURUSD")
        self.assertEqual(T.split_pair("GBPJPY"), ("GBP", "JPY"))
        self.assertEqual(T.split_pair("AAPL"), (None, None))
        self.assertEqual(T.pip_size("EURUSD"), 0.0001)
        self.assertEqual(T.pip_size("USDJPY"), 0.01)
        self.assertEqual(T.pip_size("XAUUSD"), 0.1)
        self.assertEqual(T.pips(0.0030, "EURUSD"), 30.0)
        self.assertEqual(T.px(1.0850, "EURUSD"), "1.08500")
        self.assertEqual(T.px(156.4, "USDJPY"), "156.400")
        self.assertEqual(T.yahoo_symbol("EURUSD"), "EURUSD=X")
        self.assertEqual(T.market_pair("JPY", "USD"), "USDJPY")
        self.assertEqual(T.market_pair("GBP", "EUR"), "EURGBP")

    def test_forex_clock(self):
        sat = T.market_clock(dt.datetime(2026, 11, 7, 12, 0, tzinfo=NY), {"timezone": "Asia/Riyadh"})
        self.assertFalse(sat["open"])
        self.assertIn("weekend", sat["status"])
        self.assertEqual(sat["next_open"], "Mon 2026-11-09 01:00")   # Sunday 17:00 New York
        self.assertTrue(T.market_clock(dt.datetime(2026, 11, 8, 17, 30, tzinfo=NY))["open"])
        overlap = T.market_clock(dt.datetime(2026, 11, 9, 9, 0, tzinfo=NY))
        self.assertIn("London", overlap["sessions"])
        self.assertIn("New York", overlap["sessions"])
        self.assertIn("overlap", overlap["status"])
        roll = T.market_clock(dt.datetime(2026, 11, 10, 16, 58, tzinfo=NY))
        self.assertTrue(any("rollover" in w for w in roll["warnings"]))
        fri = T.market_clock(dt.datetime(2026, 11, 13, 15, 0, tzinfo=NY))
        self.assertTrue(any("weekly close" in w for w in fri["warnings"]))
        self.assertFalse(T.market_clock(dt.datetime(2026, 11, 13, 17, 5, tzinfo=NY))["open"])
        self.assertFalse(T.market_clock(dt.datetime(2026, 12, 25, 10, 0, tzinfo=NY))["open"])

    def test_session_note(self):
        tokyo = T.market_clock(dt.datetime(2026, 11, 9, 20, 0, tzinfo=NY))  # Tuesday morning in Tokyo
        self.assertIn("Tokyo", tokyo["sessions"])
        self.assertIsNotNone(T.session_note("EURGBP", tokyo))
        self.assertIsNone(T.session_note("USDJPY", tokyo))

    def test_csv_mt5_format(self):
        p = os.path.join(tempfile.mkdtemp(), "x.csv")
        with open(p, "w") as f:
            f.write("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n")
            f.write("2026.01.05\t10:00:00\t1.0850\t1.0860\t1.0840\t1.0855\t1200\t0\t8\n")
            f.write("2026.01.05\t09:00:00\t1.0840\t1.0852\t1.0835\t1.0850\t900\t0\t9\n")
        b = T.load_csv(p, "EURUSD", "H1")
        self.assertEqual(b.c, [1.0850, 1.0855])
        self.assertEqual(b.v, [900.0, 1200.0])  # tick volume, not the empty real volume
        self.assertEqual(b.date(0), "2026-01-05 09:00")

    def test_order_hash(self):
        o = {"symbol": "EURUSD", "side": "buy", "type": "limit", "volume": 0.3, "entry": 1.085, "sl": 1.082, "tp": 1.091}
        h = T.order_hash(o)
        self.assertEqual(h, T.order_hash(dict(o, note="x", status="passed", symbol="eur/usd")))
        self.assertNotEqual(h, T.order_hash(dict(o, volume=0.31)))
        self.assertNotEqual(h, T.order_hash(dict(o, sl=1.0819)))

    def test_exposure(self):
        ex = T.exposure([{"symbol": "EURUSD", "side": "buy"}, {"symbol": "GBPUSD", "side": "buy"},
                         {"symbol": "USDJPY", "side": "sell"}])
        self.assertEqual((ex["USD"], ex["EUR"], ex["JPY"]), (-3, 1, 1))


class TestSpecs(TempTrader):
    def test_pip_values(self):
        pv = {s: round(T.pip_value(T.symbol_spec(s)), 2)
              for s in ("EURUSD", "USDJPY", "GBPJPY", "EURGBP", "USDCHF", "XAUUSD")}
        self.assertEqual(pv["EURUSD"], 10.0)
        self.assertEqual(pv["USDJPY"], round(1000 / 156.39, 2))
        self.assertEqual(pv["GBPJPY"], round(1000 / 156.39, 2))
        self.assertEqual(pv["EURGBP"], round(10 * 1.2316, 2))
        self.assertEqual(pv["USDCHF"], round(10 / 0.9094, 2))
        self.assertEqual(pv["XAUUSD"], 10.0)

    def test_notional(self):
        self.assertAlmostEqual(T.notional(T.symbol_spec("EURUSD"), 1.0, 1.085), 108500.0)
        self.assertAlmostEqual(T.notional(T.symbol_spec("USDJPY"), 1.0, 156.39), 100000.0)


# ---------------------------------------------------------------- analysis skills

def fx_walk(n=400, drift=0.0, seed=2, start=1.08):
    return synth.walk(n, start=start, drift=drift, sigma=0.002, seed=seed, symbol="EURUSD", tf="H1", spread=0.0008)


class TestEntryTimer(TempTrader):
    def test_uptrend_scores_buy(self):
        o = timer.analyze(fx_walk(drift=0.0008, seed=2), None, dict(OPEN_CLOCK), [])
        self.assertEqual(o["trend"], "up")
        self.assertGreater(o["buy"]["score"], o["sell"]["score"])
        self.assertIn("pips", o["text"])
        self.assertRegex(o["text"], r"price 1\.\d{5}")

    def test_downtrend_scores_sell(self):
        o = timer.analyze(fx_walk(drift=-0.0008, seed=4), None, dict(OPEN_CLOCK), [])
        self.assertEqual(o["trend"], "down")
        self.assertGreater(o["sell"]["score"], o["buy"]["score"])

    def test_news_warning(self):
        now = dt.datetime.now(dt.timezone.utc)
        T.save_json(T.tpath("calendar.json"), {"events": [
            {"title": "CPI m/m", "currency": "USD", "impact": "High", "time": (now + dt.timedelta(minutes=45)).isoformat()},
            {"title": "Retail Sales", "currency": "JPY", "impact": "High",
             "time": (now + dt.timedelta(minutes=30)).isoformat()}]})
        ev = timer.upcoming_news("EURUSD")
        self.assertEqual([e["title"] for e in ev], ["CPI m/m"])
        o = timer.analyze(fx_walk(), None, dict(OPEN_CLOCK), ev)
        self.assertTrue(any("HIGH-IMPACT NEWS" in t for t in o["timing"]))


class TestChartScanner(unittest.TestCase):
    def names(self, b, k=1.0):
        return [f["name"] for f in scan.scan(b, k)["findings"]]

    def test_double_top_confirmed(self):
        b = synth.path([80, 90, 100, 110, 120, 112, 105, 112, 120, 112, 103, 100], steps=8)
        dtop = [f for f in scan.scan(b, 1.0)["findings"] if f["name"] == "double top"]
        self.assertTrue(dtop)
        self.assertTrue(dtop[0]["status"].startswith("confirmed"))
        self.assertLess(dtop[0]["target"], dtop[0]["level"])

    def test_head_and_shoulders(self):
        self.assertIn("head and shoulders", self.names(synth.path([70, 80, 100, 92, 110, 92, 100, 90, 85], steps=10)))

    def test_symmetrical_triangle(self):
        self.assertIn("symmetrical triangle",
                      self.names(synth.path([70, 80, 120, 100, 116, 103, 113, 106, 111, 108], steps=10)))

    def test_bullish_engulfing(self):
        b = synth.walk(80, drift=-0.004, sigma=0.004, seed=9)
        i = len(b) - 1
        b.o[i - 1], b.c[i - 1], b.h[i - 1], b.l[i - 1] = 100.0, 98.0, 100.5, 97.5
        b.o[i], b.c[i], b.h[i], b.l[i] = 97.8, 101.0, 101.2, 97.6
        self.assertIn("bullish engulfing", [f["name"] for f in scan.candles(b, 1)])

    def test_forex_output_and_svg(self):
        b = fx_walk(300, seed=11)
        o = scan.scan(b)
        self.assertIn("pips", o["text"])
        p = os.path.join(tempfile.mkdtemp(), "c.svg")
        scan.svg(b, o, p)
        self.assertIn("<svg", rd(p))


# ---------------------------------------------------------------- risk

class TestRisk(TempTrader):
    def test_size_eurusd(self):
        o = risk.size("EURUSD", 1.0850, 1.0820, 1.0910, balance=10000, cfg=T.load_config())
        self.assertEqual(o["stop_pips"], 30.0)
        self.assertEqual(o["volume"], 0.33)        # 100 / (30 pips x $10)
        self.assertEqual(o["rr"], 2.0)

    def test_size_jpy_cross_and_gold(self):
        o = risk.size("GBPJPY", 192.60, 193.00, balance=10000, cfg=T.load_config())
        self.assertEqual(o["side"], "sell")
        self.assertEqual(o["volume"], 0.39)        # 100 / (40 pips x $6.39)
        o = risk.size("XAUUSD", 2650, 2635, balance=10000, cfg=T.load_config())
        self.assertEqual(o["volume"], 0.06)        # $15 move x 100 oz = $1500 per lot

    def test_leverage_cap(self):
        o = risk.size("EURUSD", 1.0850, 1.0845, balance=10000, cfg=T.load_config())   # 5-pip stop -> 2 lots wanted
        self.assertLessEqual(o["notional"], 10000 * 10 + 1)
        self.assertTrue(any("max_leverage" in n for n in o["notes"]))

    def check(self, orders, acct=None, today=0.0, **cfg):
        c = T.load_config()
        c.update(cfg)
        acct = acct or {"balance": 10000, "positions": []}
        return risk.check_orders(orders, c, acct, today, None, lambda s: T.symbol_spec(s, c))

    def test_check_pass_and_blocks(self):
        good = {"id": "o1", "symbol": "EURUSD", "side": "buy", "type": "limit", "volume": 0.33,
                "entry": 1.0850, "sl": 1.0820, "tp": 1.0910}
        self.assertEqual(self.check([good])[0][1], "PASS")
        cases = [
            (dict(good, sl=None), "no stop-loss"),
            (dict(good, sl=1.0870), "wrong side"),
            (dict(good, volume=0.5), "over your"),
            (dict(good, tp=1.0870), "reward:risk"),
            (dict(good, volume=0.335), "lot step"),
        ]
        for o, why in cases:
            v, reasons = self.check([o])[0][1:3]
            self.assertEqual(v, "BLOCK", o)
            self.assertTrue(any(why in r for r in reasons), (why, reasons))
        sell = dict(good, side="sell", sl=1.0880, tp=1.0790)
        self.assertEqual(self.check([sell])[0][1], "PASS")
        self.assertEqual(self.check([sell], allow_short=False)[0][1], "BLOCK")

    def test_daily_loss_positions_and_correlation(self):
        good = {"symbol": "EURUSD", "side": "buy", "volume": 0.1, "entry": 1.0850, "sl": 1.0820, "tp": 1.0910}
        self.assertEqual(self.check([good], today=-301)[0][1], "BLOCK")
        acct = {"balance": 10000, "positions": [
            {"symbol": "GBPUSD", "side": "buy", "volume": 0.05, "entry": 1.2316, "sl": 1.2286},
            {"symbol": "AUDUSD", "side": "buy", "volume": 0.05, "entry": 0.6269, "sl": 0.6239}]}
        o, v, reasons, warns = self.check([good], acct=acct)[0]
        self.assertEqual(v, "PASS")
        self.assertTrue(any("short USD" in w for w in warns), warns)
        full = {"balance": 10000, "positions": [dict(acct["positions"][0]) for _ in range(5)]}
        self.assertTrue(any("open positions" in r for r in self.check([good], acct=full)[0][2]))

    def test_cli_size_write_check(self):
        self.cfg()
        rc, out, err = quiet(risk.main, ["size", "EURUSD", "--side", "buy", "--stop-pips", "30", "--rr", "2", "--write"])
        self.assertEqual(rc, 0, err)
        o = T.load_orders()["orders"][0]
        self.assertEqual((o["sl"], o["tp"], o["type"]), (1.082, 1.091, "market"))
        rc, out, _ = quiet(risk.main, ["check"])
        self.assertIn("PASS  o1", out)


# ---------------------------------------------------------------- exits

class TestExit(unittest.TestCase):
    def test_plan_buy_and_sell(self):
        b = fx_walk(300, seed=11)
        e = b.c[-1]
        o = exitp.plan(b, e)
        a = o["atr14"]
        self.assertLess(o["stop"], e)
        self.assertTrue(0.99 * a <= e - o["stop"] <= 3.01 * a)
        self.assertGreater(o["tp_single"], e)
        self.assertGreaterEqual(o["rr_single"], 1.49)
        self.assertIn("pips", o["text"])
        s = exitp.plan(b, e, side="sell")
        self.assertGreater(s["stop"], e)
        self.assertLess(s["tp_single"], e)

    def test_trail_breakeven_and_never_loosen(self):
        closes = [1.0800 + 0.0005 * i for i in range(60)]
        b = synth.from_closes(closes, symbol="EURUSD", spread=0.0002)
        entry, stop = closes[30], closes[30] - 0.0030
        o = exitp.trail(b, entry, stop, "buy", 30)
        self.assertTrue(o["move"])
        self.assertGreaterEqual(o["stop_new"], round(entry, 5))
        self.assertGreaterEqual(exitp.trail(b, entry, o["stop_new"], "buy", 30)["stop_new"], o["stop_new"])

    def test_trail_reports_stop_hit(self):
        closes = [1.0900 - 0.0005 * i for i in range(40)]
        b = synth.from_closes(closes, symbol="EURUSD", spread=0.0002)
        o = exitp.trail(b, closes[10], closes[10] - 0.0020, "buy", 10)
        self.assertTrue(any("through the stop" in e for e in o["events"]))


# ---------------------------------------------------------------- backtest

class TestBacktest(unittest.TestCase):
    def test_fill_next_open_and_spread(self):
        b = fx_walk(300, drift=0.0005, seed=3)
        rules = {"entry_long": [["close", ">", 0]], "exit_long": [["close", "<", 0]]}
        trades, _ = bt.run(b, rules, sl_atr=None, spread=0.0002)
        self.assertAlmostEqual(trades[0]["entry"], round(b.o[2] + 0.0001, 5))   # next open + half spread
        self.assertEqual(trades[0]["why"], "still open at end")
        self.assertAlmostEqual(trades[0]["lots"] * 100, round(trades[0]["lots"] * 100))

    def test_stop_first_when_both_hit(self):
        b = synth.from_closes([1.08] * 30, symbol="EURUSD", spread=0.001)
        b.h[25], b.l[25] = 1.20, 0.90
        trades, _ = bt.run(b, {"entry_long": [["close", ">=", 1.0]]}, sl_atr=2, tp_r=1, start_ts=b.t[20])
        self.assertEqual(trades[0]["why"], "stop")

    def test_jpy_conversion(self):
        b = synth.walk(400, start=150.0, sigma=0.003, seed=6, symbol="USDJPY", tf="D1", spread=0.002)
        o = bt.backtest(b, bt.STRATEGIES["ema_cross"]({"fast": 10, "slow": 30}), "x",
                        {"capital": 10000, "risk_pct": 1, "sl_atr": 2, "spread_pips": 1.0})
        stopped = [t for t in o["trades"] if t["why"] == "stop"]
        self.assertTrue(stopped)
        for t in stopped:
            self.assertAlmostEqual(t["r"], -1.0, delta=0.15)   # a stop costs ~1% of 10k USD, not yen

    def test_all_strategies_and_rules(self):
        b = fx_walk(800, drift=0.0001, seed=5)
        for name, f in bt.STRATEGIES.items():
            o = bt.backtest(b, f({"fast": 20, "slow": 50, "rsi_low": 30, "rsi_high": 70, "lookback": 20}), name,
                            {"capital": 10000, "risk_pct": 1, "sl_atr": 2, "spread_pips": 1})
            self.assertLessEqual(o["metrics"]["max_drawdown_pct"], 0)
            self.assertIn(name, bt.render(o))
        S = bt.Series(fx_walk(100))
        for s in ("sma20", "ema50", "rsi14", "atr14", "macd_hist", "bb_upper20", "highest10", "lowest10"):
            self.assertEqual(len(S.get(s)), 100)


# ---------------------------------------------------------------- alerts

class TestAlerts(unittest.TestCase):
    def test_evaluate(self):
        al = [{"id": 1, "symbol": "EURUSD", "kind": "above", "level": 1.09, "repeat": True},
              {"id": 2, "symbol": "EURUSD", "kind": "cross", "level": 1.085},
              {"id": 3, "symbol": "EURUSD", "kind": "pct", "level": 0.5},
              {"id": 4, "symbol": "EURUSD", "kind": "session", "session": "London", "level": None},
              {"id": 5, "symbol": "EURUSD", "kind": "session", "session": "Tokyo", "level": None}]
        rung = []
        for p in (1.080, 1.0905, 1.0910, 1.0890, 1.0920):
            rung.append(sorted(r["id"] for r in alerts.evaluate(al, {"EURUSD": {"price": p, "prev_close": 1.0840}},
                                                                 dict(OPEN_CLOCK), "2026-11-09")))
        self.assertEqual(rung, [[4], [1, 2, 3], [], [], [1]])


# ---------------------------------------------------------------- order executor

class FakeMT5(types.SimpleNamespace):
    """Just enough of the MetaTrader5 module for the executor."""

    def __init__(self, trade_mode=0, spread=0.00012):
        super().__init__()
        self.sent = []
        for k, v in dict(TRADE_ACTION_DEAL=1, TRADE_ACTION_PENDING=5, TRADE_ACTION_SLTP=6, TRADE_ACTION_REMOVE=8,
                         ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, ORDER_TYPE_BUY_LIMIT=2, ORDER_TYPE_SELL_LIMIT=3,
                         ORDER_TYPE_BUY_STOP=4, ORDER_TYPE_SELL_STOP=5, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1,
                         ORDER_FILLING_RETURN=2, ORDER_TIME_GTC=0, TRADE_RETCODE_DONE=10009,
                         TRADE_RETCODE_PLACED=10008, TIMEFRAME_D1=16408).items():
            setattr(self, k, v)
        self._mode, self._spread = trade_mode, spread

    def account_info(self):
        return types.SimpleNamespace(login=123, server="Broker-Demo", trade_mode=self._mode, balance=10000.0,
                                     equity=10000.0, margin_free=10000.0, margin=0.0, leverage=100, currency="USD")

    def terminal_info(self):
        return types.SimpleNamespace(trade_allowed=True)

    def symbol_select(self, s, on):
        return True

    def symbol_info(self, s):
        return types.SimpleNamespace(digits=5, filling_mode=2, trade_tick_size=0.00001, trade_tick_value=1.0,
                                     trade_contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01,
                                     point=0.00001, currency_base="EUR", currency_profit="USD")

    def symbol_info_tick(self, s):
        return types.SimpleNamespace(bid=1.08500, ask=1.08500 + self._spread, last=0.0, time=0)

    def positions_get(self):
        return []

    def history_deals_get(self, a, b):
        return []

    def copy_rates_from_pos(self, *a):
        return None

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
        for y in ("yes", "Yes, place it", "نعم", "ايوه نفذ", "اي", "موافق", "تمام", "اوكي"):
            self.assertTrue(order.affirmative(y), y)
        for n in ("no", "yes but wait", "لا", "لا تنفذ", "ok don't", "", "maybe", "انتظر", "نعم بس لحظة", "مو الحين"):
            self.assertFalse(order.affirmative(n), n)

    def prepare(self, **cfg):
        self.cfg(**cfg)
        T.market_clock = lambda now=None, cfg=None: dict(OPEN_CLOCK)
        rc, out, err = quiet(risk.main, ["size", "EURUSD", "--side", "buy", "--stop-pips", "30", "--rr", "2", "--write"])
        self.assertEqual(rc, 0, err)
        rc, out, err = quiet(risk.main, ["check"])
        self.assertIn("PASS", out, out + err)
        o = T.load_orders()["orders"][0]
        return o, T.order_hash(o)

    def test_paper_flow(self):
        o, h = self.prepare()
        rc, out, _ = quiet(order.main, ["tickets"])
        self.assertIn("READY  o1  ticket %s" % h, out)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", "deadbeef0000", "--confirm", "yes"])
        self.assertIn("does not match", err)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "no"])
        self.assertNotEqual(rc, 0)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "نعم"])
        self.assertEqual(rc, 0, err)
        self.assertIn("PLACED", out)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertIn("already placed", err)
        pid = str(T.paper_state()["positions"][0]["id"])
        rc, out, err = quiet(order.main, ["modify", pid, "--sl", "1.0800", "--confirm", "yes"])
        self.assertIn("refusing to loosen", err)
        rc, out, err = quiet(order.main, ["modify", pid, "--sl", "1.0830", "--confirm", "yes"])
        self.assertEqual(rc, 0, err)
        T._PRICE_OVERRIDE["EURUSD"] = 1.0825
        rc, out, err = quiet(order.main, ["positions"])
        self.assertIn("closed at stop 1.08300", out)
        t = T.journal()["trades"][0]
        self.assertEqual(t["status"], "closed")
        self.assertEqual(t["pips"], -20.0)
        self.assertAlmostEqual(t["pnl"], -20 * 10 * t["volume"], places=2)
        self.assertEqual(T.load_json(T.tpath("executions.json"))["executions"][0]["user_confirmation"], "نعم")

    def test_manual_flow(self):
        o, h = self.prepare(mode="manual")
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "نعم"])
        self.assertEqual(rc, 0, err)
        self.assertIn("PLACE IT YOURSELF", out)
        self.assertIn("Stop Loss: 1.08200", out)
        self.assertEqual(T.paper_state()["positions"], [])
        rc, out, err = quiet(order.main, ["positions"])
        self.assertIn("waiting for you to place", out)
        rc, out, err = quiet(order.main, ["filled", "o1", "--price", "1.08512"])
        self.assertEqual(rc, 0, err)
        pos = T.paper_state()["positions"][0]
        self.assertEqual(pos["entry"], 1.08512)
        T._PRICE_OVERRIDE["EURUSD"] = 1.0815
        rc, out, err = quiet(order.main, ["positions"])
        self.assertIn("passed the stop", out)
        self.assertEqual(len(T.paper_state()["positions"]), 1)   # manual: never auto-closed
        rc, out, err = quiet(order.main, ["close", str(pos["id"]), "--confirm", "yes"])
        self.assertIn("close price", err)
        rc, out, err = quiet(order.main, ["close", str(pos["id"]), "--price", "1.08200", "--confirm", "yes"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(T.journal()["trades"][0]["pips"], -31.2)

    def test_edit_voids_pass(self):
        self.prepare()
        data = T.load_orders()
        data["orders"][0]["volume"] = 0.01
        T.save_orders(data)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", T.order_hash(data["orders"][0]), "--confirm", "yes"])
        self.assertIn("no PASS", err)

    def test_mt5_demo_places_with_stop(self):
        fake = FakeMT5(trade_mode=0)
        T._MT5_READY = fake
        o, h = self.prepare(mode="mt5")
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertEqual(rc, 0, err)
        req = fake.sent[0]
        self.assertEqual((req["sl"], req["tp"], req["type"]), (o["sl"], o["tp"], fake.ORDER_TYPE_BUY))
        self.assertEqual(req["type_filling"], fake.ORDER_FILLING_IOC)

    def test_mt5_wide_spread_refused(self):
        fake = FakeMT5(trade_mode=0, spread=0.0005)
        T._MT5_READY = fake
        o, h = self.prepare(mode="mt5")
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertIn("spread", err)
        self.assertEqual(fake.sent, [])

    def test_mt5_real_account_refused(self):
        fake = FakeMT5(trade_mode=2)
        T._MT5_READY = fake
        o, h = self.prepare(mode="mt5")
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertIn("REAL account", err)
        self.assertEqual(fake.sent, [])

    def test_mt5_not_connected_is_clear(self):
        self.cfg(mode="mt5")
        rc, out, err = quiet(order.main, ["account"])
        self.assertNotEqual(rc, 0)
        self.assertIn("phone app cannot be connected", err)


# ---------------------------------------------------------------- budget

class TestBudget(TempTrader):
    def test_stats(self):
        tr = [{"status": "closed", "pnl": p, "closed_at": "2026-10-%02d" % (i + 1), "symbol": "EURUSD", "r": p / 50}
              for i, p in enumerate([100, -50, -50, 150, -50])]
        s = budget.stats(tr)
        self.assertEqual((s["trades"], s["net_pnl"], s["win_rate_pct"]), (5, 100.0, 40.0))
        self.assertEqual(s["max_drawdown"], -100.0)
        self.assertEqual(s["max_losing_streak"], 2)

    def test_plan_table(self):
        rc, out, err = quiet(budget.main, ["plan", "--capital", "5000", "--pairs", "EURUSD", "GBPJPY", "--stops", "25"])
        self.assertEqual(rc, 0, err)
        self.assertRegex(out, r"EURUSD\s+0\.2\b")

    def test_journal_add_pips(self):
        rc, out, err = quiet(budget.main, ["journal-add", "USDJPY", "sell", "0.5", "156.40", "--exit", "155.90",
                                           "--sl", "156.80"])
        self.assertEqual(rc, 0, err)
        t = T.journal()["trades"][0]
        self.assertEqual(t["pips"], 50.0)
        self.assertAlmostEqual(t["pnl"], 50 * 0.5 * 1000 / 156.39, places=1)


# ---------------------------------------------------------------- news, calendar, COT

class TestNews(unittest.TestCase):
    def test_parse_and_select(self):
        items = news.parse_rss(rd(os.path.join(FX, "yahoo-EURUSD.xml"), "rb"), "EURUSD")
        items += news.parse_rss(rd(os.path.join(FX, "fed-press.xml"), "rb"), "Fed", "USD", severity=2)
        self.assertEqual(len(items), 3)
        fed = [i for i in items if i["source"] == "Fed"][0]
        self.assertEqual(fed["severity"], 3)                # a rate decision
        self.assertIn("rates", fed["tags"])
        sel = news.select(items, {}, 0, False)
        self.assertEqual(sel[0]["source"], "Fed")
        self.assertEqual(news.select(items, {i["key"]: 1 for i in sel}, 0, False), [])


class TestEcon(TempTrader):
    def events(self):
        return econ.normalize(json.loads(rd(os.path.join(FX, "ff-week.json"))))

    def test_num_and_surprise(self):
        self.assertEqual(econ.num("254K"), 254000)
        self.assertEqual(econ.num("-0.9%"), -0.9)
        self.assertEqual(econ.num("<0.1%"), 0.1)
        self.assertIsNone(econ.num(""))
        ev = {e["title"]: e for e in self.events()}
        self.assertEqual(econ.surprise(ev["ISM Services PMI"])["effect"], 1)
        self.assertEqual(econ.surprise(ev["Unemployment Claims"])["effect"], -1)   # higher claims = bad
        self.assertEqual(econ.surprise(ev["Official Bank Rate"])["effect"], 0)
        self.assertIsNone(econ.surprise(ev["Non-Farm Employment Change"]))

    def test_cli_calendar_actual_bias(self):
        f = os.path.join(FX, "ff-week.json")
        rc, out, err = quiet(econ.main, ["--file", f, "calendar", "--past", "--impact", "high"])
        self.assertEqual(rc, 0, err)
        self.assertIn("Non-Farm", out)
        self.assertNotIn("Bank Holiday", out)
        rc, out, err = quiet(econ.main, ["actual", "Non-Farm", "USD", "254K"])
        self.assertEqual(rc, 0, err)
        hist = list(T.load_json(T.tpath("calendar_history.json"))["events"].values())
        b = econ.bias("EURUSD", hist, 14, dt.datetime(2026, 11, 8, tzinfo=dt.timezone.utc))
        self.assertTrue(b["tilt"].startswith("bearish"))
        self.assertGreater(b["scores"]["USD"], 0)
        self.assertLess(b["scores"]["EUR"], 0)


class TestCOT(unittest.TestCase):
    def data(self):
        raw = json.loads(rd(os.path.join(FX, "cot-sample.json")))
        return {c: cot.series([r for r in raw if r["cftc_contract_market_code"] == cot.CODES[c]])
                for c in ("EUR", "JPY", "USD", "GBP")}

    def test_summary(self):
        d = self.data()
        s = cot.summarize("EUR", d["EUR"])
        self.assertEqual(s["date"], "2025-12-25")
        self.assertEqual(s["net"], d["EUR"][-1]["long"] - d["EUR"][-1]["short"])
        self.assertTrue(0 <= s["cot_index"] <= 100)
        self.assertEqual(s["change_1w"], d["EUR"][-1]["net"] - d["EUR"][-2]["net"])

    def test_pair_view(self):
        d = self.data()
        summ = {c: cot.summarize(c, d[c]) for c in d}
        v = cot.pair_view("EURUSD", summ)
        self.assertEqual(v["difference"], summ["EUR"]["cot_index"] - summ["USD"]["cot_index"])
        self.assertIn("error", cot.pair_view("AUDNZD", summ))


# ---------------------------------------------------------------- packaging

class TestPackaging(unittest.TestCase):
    EXPECTED = {"entry-timer", "news-monitor", "chart-scanner", "risk-manager", "time-machine", "exit-planner",
                "price-alerts", "order-executor", "budget-manager", "cot-tracker", "economic-analyst",
                "trading-plan", "currency-strength", "market-structure", "trade-coach", "auto-trader"}

    def test_all_skills_with_valid_frontmatter(self):
        found = set()
        for d in os.listdir(SK):
            p = os.path.join(SK, d, "SKILL.md")
            if not os.path.isfile(p):
                continue
            m = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: >-\n(.*?)\n---\n", rd(p), re.S)
            self.assertTrue(m, d)
            self.assertEqual(m.group(1), d)
            self.assertLess(len(" ".join(m.group(2).split())), 1024, d)
            found.add(d)
        self.assertEqual(found, self.EXPECTED)

    def test_plugin_manifests(self):
        for f in ("plugin.json", "marketplace.json"):
            json.loads(rd(os.path.join(ROOT, ".claude-plugin", f)))

    def test_scripts_have_help(self):
        for d in self.EXPECTED:
            for f in os.listdir(os.path.join(SK, d)):
                if f.endswith(".py"):
                    rc, out, _ = quiet(load(d, f[:-3]).main, ["--help"])
                    self.assertEqual(rc, 0, (d, f))


if __name__ == "__main__":
    unittest.main()
