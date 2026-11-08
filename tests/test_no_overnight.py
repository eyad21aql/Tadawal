"""No-overnight (no swap) rule: closes before the 17:00 New York rollover, everywhere."""
import collections
import datetime as dt
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_skills import T, TempTrader, OPEN_CLOCK, quiet, load, synth, risk, order, bt, alerts  # noqa: E402

plan = load("trading-plan", "plan")
coach = load("trade-coach", "coach")
NY = T.NY


def at(day, h, m=0):
    return dt.datetime(2026, 11, day, h, m, tzinfo=NY)


class TestStatus(unittest.TestCase):
    def test_windows(self):
        c = dict(T.DEFAULT_CONFIG, no_overnight=True, timezone="Asia/Riyadh")
        s = T.overnight_status(c, at(10, 10))
        self.assertTrue(s["can_open"])
        self.assertFalse(s["must_close"])
        self.assertEqual((s["last_entry_local"], s["flat_local"], s["rollover_local"]), ("00:00", "00:45", "01:00"))
        self.assertFalse(T.overnight_status(c, at(10, 16, 10))["can_open"])
        self.assertTrue(T.overnight_status(c, at(10, 16, 50))["must_close"])
        self.assertTrue(T.overnight_status(c, at(10, 17, 5))["can_open"])
        self.assertTrue(T.overnight_status(dict(c, no_overnight=False), at(10, 16, 50))["can_open"])
        summer = T.overnight_status(c, dt.datetime(2026, 7, 7, 10, 0, tzinfo=NY))
        self.assertEqual(summer["rollover_local"], "00:00")   # US summer time

    def test_held_overnight(self):
        now = at(11, 10)
        self.assertTrue(T.held_overnight(at(10, 16).isoformat(), now))
        self.assertFalse(T.held_overnight(at(10, 18).isoformat(), now))


class TestEnforcement(TempTrader):
    def setUp(self):
        super().setUp()
        self._ovn = T.overnight_status

    def tearDown(self):
        T.overnight_status = self._ovn
        super().tearDown()

    def late(self):
        real = self._ovn
        T.overnight_status = lambda cfg=None, now=None: real(cfg, at(10, 16, 20))

    def test_risk_blocks_late_entries(self):
        self.cfg(no_overnight=True)
        self.late()
        o = {"symbol": "EURUSD", "side": "buy", "volume": 0.1, "entry": 1.0850, "sl": 1.0820, "tp": 1.0910}
        res = risk.check_orders([o], T.load_config(), {"balance": 10000, "positions": []}, 0.0, None,
                                lambda s: T.symbol_spec(s))
        self.assertEqual(res[0][1], "BLOCK")
        self.assertTrue(any("no-overnight" in r for r in res[0][2]))

    def test_executor_refuses_late_even_with_pass(self):
        self.cfg(no_overnight=True)
        T.market_clock = lambda now=None, cfg=None: dict(OPEN_CLOCK)
        quiet(risk.main, ["size", "EURUSD", "--side", "buy", "--stop-pips", "10", "--rr", "2", "--write"])
        quiet(risk.main, ["check"])
        o = T.load_orders()["orders"][0]
        self.late()
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", T.order_hash(o), "--confirm", "yes"])
        self.assertIn("cannot be overridden", err)

    def test_paper_auto_flat_and_manual_warning(self):
        self.cfg(no_overnight=True)
        st = T.paper_state()
        st["positions"].append({"id": 1, "symbol": "EURUSD", "side": "buy", "volume": 0.1, "entry": 1.0840,
                                "sl": 1.0800, "tp": 1.0950, "mpp": 100000, "opened": at(10, 10).isoformat()})
        st["pending"].append({"id": "o9", "symbol": "EURUSD", "side": "buy", "type": "limit", "entry": 1.0700,
                              "volume": 0.1, "sl": 1.0650, "tp": 1.0800})
        T.save_paper(st)
        T.save_journal({"trades": [{"id": "p1", "position": 1, "mode": "paper", "symbol": "EURUSD", "side": "buy",
                                    "volume": 0.1, "entry": 1.0840, "sl": 1.0800, "status": "open",
                                    "opened_at": at(10, 10).isoformat()}]})
        real = self._ovn
        T.overnight_status = lambda cfg=None, now=None: real(cfg, at(10, 16, 50))
        self.cfg(no_overnight=True, mode="manual")
        rc, out, err = quiet(order.main, ["positions"])
        self.assertIn("CLOSE NOW in the MT5 app", out)
        self.assertEqual(len(T.paper_state()["positions"]), 1)
        self.cfg(no_overnight=True, mode="paper")
        rc, out, err = quiet(order.main, ["positions"])
        self.assertIn("closed before the rollover", out)
        self.assertEqual(T.paper_state()["positions"], [])
        self.assertEqual(T.paper_state()["pending"], [])
        self.assertEqual(T.journal()["trades"][0]["why"], "closed before rollover (no overnight)")

    def test_flat_command(self):
        self.cfg(no_overnight=True)
        st = T.paper_state()
        st["positions"].append({"id": 3, "symbol": "XAUUSD", "side": "sell", "volume": 0.05, "entry": 2655.0,
                                "sl": 2660.0, "tp": 2645.0, "mpp": 100, "opened": at(10, 10).isoformat()})
        T.save_paper(st)
        rc, out, err = quiet(order.main, ["flat"])
        self.assertIn("#3 sell 0.05 XAUUSD", out)
        rc, out, err = quiet(order.main, ["flat", "--close", "--confirm", "نعم"])
        self.assertEqual(rc, 0, err)
        self.assertIn("closed #3", out)
        self.assertEqual(T.paper_state()["positions"], [])
        self.assertAlmostEqual(T.paper_state()["balance"], 10000 + 5 * 0.05 * 100, places=2)

    def test_alert_rings_before_flat(self):
        c = dict(T.load_config(), no_overnight=True)
        st = {}
        pos = [{"symbol": "EURUSD"}]
        self.assertEqual(alerts.overnight_rings(c, st, at(10, 15, 0), pos), [])
        r = alerts.overnight_rings(c, st, at(10, 16, 20), pos)
        self.assertIn("25 minutes left", r[0]["msg"])
        self.assertEqual(alerts.overnight_rings(c, st, at(10, 16, 21), pos), [])     # once per mark
        self.assertIn("CLOSE ALL TRADES NOW", alerts.overnight_rings(c, st, at(10, 16, 46), pos)[-1]["msg"])
        self.assertEqual(alerts.overnight_rings(c, {}, at(10, 16, 46), []), [])     # nothing open


class TestPlanScalp(TempTrader):
    def test_scalp_init_and_per_pair_stops(self):
        rc, out, err = quiet(plan.main, ["init", "--style", "scalp", "--pairs", "EURUSD", "XAUUSD",
                                         "--sessions", "london", "newyork", "--no-overnight"])
        self.assertEqual(rc, 0, err)
        p = plan.load_plan()
        self.assertEqual(p["pairs"], ["EURUSD", "XAUUSD"])
        self.assertTrue(T.load_config()["no_overnight"])
        self.assertIn("XAUUSD 20-60", out)
        clock = dict(OPEN_CLOCK, now_iso=at(10, 9).isoformat())
        st = {"trades_today": 0, "losses_in_a_row": 0, "week_pnl": 0.0}
        gold = {"symbol": "XAUUSD", "side": "buy", "type": "limit", "volume": 0.05, "entry": 2650.0, "sl": 2647.0,
                "tp": 2655.0, "setup": "ema-pullback"}
        self.assertEqual(plan.evaluate(gold, p, clock, st, 10000, [], [True] * 5)[0], "IN PLAN")     # 30 pips = $3
        eu = {"symbol": "EURUSD", "side": "buy", "type": "limit", "volume": 0.3, "entry": 1.0850, "sl": 1.0820,
              "tp": 1.0900, "setup": "ema-pullback"}
        v, R, _ = plan.evaluate(eu, p, clock, st, 10000, [], [True] * 5)
        self.assertEqual(v, "OUT OF PLAN")
        self.assertIn("5-15 for EURUSD", R[0])
        late = T.overnight_status(dict(T.load_config()), at(10, 16, 30))
        v, R, _ = plan.evaluate(dict(eu, sl=1.0840), p, clock, st, 10000, [], [True] * 5, late)
        self.assertTrue(any("no overnight" in r for r in R))


class TestBacktestFlat(unittest.TestCase):
    def test_no_trade_spans_rollover(self):
        rnd = random.Random(4)
        px, cl = 1.08, []
        for _ in range(24 * 60):
            px *= math.exp(0.0015 * rnd.gauss(0, 1))
            cl.append(px)
        b = synth.from_closes(cl, symbol="EURUSD", tf="H1", spread=0.0005, step=3600)
        rules = bt.STRATEGIES["ema_cross"]({"fast": 10, "slow": 30})
        base = {"capital": 10000, "risk_pct": 1, "sl_atr": 2, "spread_pips": 1}

        def spans(o):
            n = 0
            for t in o["trades"]:
                a = dt.datetime.fromisoformat(t["entry_date"]).replace(tzinfo=dt.timezone.utc)
                c = dt.datetime.fromisoformat(t["exit_date"]).replace(tzinfo=dt.timezone.utc)
                n += T.last_rollover(c) > a
            return n
        self.assertGreater(spans(bt.backtest(b, rules, "x", base)), 0)
        o = bt.backtest(b, rules, "x", dict(base, no_overnight=True, last_entry_ny="16:00", flat_ny="16:45"))
        self.assertEqual(spans(o), 0)
        self.assertIn("flat before rollover", collections.Counter(t["why"] for t in o["trades"]))
        for t in o["trades"]:
            d = dt.datetime.fromisoformat(t["entry_date"]).replace(tzinfo=dt.timezone.utc).astimezone(NY)
            self.assertFalse(16 * 60 <= d.hour * 60 + d.minute < 17 * 60, t)


class TestCoachOvernight(TempTrader):
    def test_flags_overnight_holds(self):
        cfg = T.load_config()
        tr = [{"id": "a", "symbol": "EURUSD", "side": "buy", "volume": 0.1, "entry": 1.08, "sl": 1.07, "pnl": 20, "r": 0.5,
               "opened_at": at(10, 15).isoformat(), "closed_at": at(11, 3).isoformat(), "status": "closed"},
              {"id": "b", "symbol": "EURUSD", "side": "buy", "volume": 0.1, "entry": 1.08, "sl": 1.07, "pnl": 20, "r": 0.5,
               "opened_at": at(10, 9).isoformat(), "closed_at": at(10, 11).isoformat(), "status": "closed"}]
        f = {x["kind"]: x for x in coach.detect([coach.enrich(t, cfg) for t in tr], {"no_overnight": True}, [])}
        self.assertEqual(f["overnight"]["ids"], ["a"])
        self.assertEqual(f["overnight"]["severity"], 3)


if __name__ == "__main__":
    unittest.main()
