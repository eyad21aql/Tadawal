"""Auto-trader robot: entries, limits, no-overnight flat, breakeven, kill switches, Telegram."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_skills import T, TempTrader, quiet, load, synth, FakeMT5  # noqa: E402

robot = load("auto-trader", "robot")
NY = T.NY
ALWAYS_BUY = {"entry_long": [["close", ">", 0]]}


def at(day, h, m=0):
    return dt.datetime(2026, 11, day, h, m, tzinfo=NY)


def m15_bars(symbol="EURUSD", n=300, start=1.0800, end_before=None):
    end_before = end_before or at(10, 9)
    b = synth.walk(n, start=start, drift=0.00005, sigma=0.0006, seed=3, symbol=symbol, tf="M15", spread=0.0003)
    first = int(end_before.timestamp()) - 900 * (n + 1)
    b.t = [first + 900 * i for i in range(n)]
    return b


class FakeTG:
    def __init__(self, chat="42", updates=None):
        self.token, self.chat_id, self.sent, self._u = "x", chat, [], updates or []

    @property
    def on(self):
        return True

    def send(self, text):
        self.sent.append(text)
        return True

    def updates(self, offset):
        u, self._u = self._u, []
        return u


class RobotCase(TempTrader):
    def setUp(self):
        super().setUp()
        self._ovn = T.overnight_status
        self.now = at(10, 9)                       # Tuesday 09:00 New York: London/New York overlap
        real = self._ovn
        T.overnight_status = lambda cfg=None, now=None: real(cfg, now or self.now)
        self._ny_now = T.ny_now
        T.ny_now = lambda: self.now                # one clock for the robot and the libraries
        T.save_json(T.tpath("config.json"), {"mode": "paper"})

    def tearDown(self):
        T.overnight_status = self._ovn
        T.ny_now = self._ny_now
        super().tearDown()

    def make(self, rules=ALWAYS_BUY, bars=None, tg=None, **rc):
        r = dict(robot.DEFAULTS, pairs=["EURUSD"], rules=rules, **rc)
        robot.save_rc(r)
        bars = bars or {"EURUSD": m15_bars()}
        return robot.Robot(rc=r, now_fn=lambda: self.now, bars_fn=lambda s, tf, n: bars[s], telegram=tg or FakeTG())

    def put_position(self, side="buy", entry=1.0800, sl=1.0780, opened=None):
        st = T.paper_state()
        st["positions"].append({"id": 7, "symbol": "EURUSD", "side": side, "volume": 0.2, "entry": entry, "sl": sl,
                                "tp": 1.0900, "mpp": 100000, "price": entry, "opened": (opened or at(10, 8)).isoformat()})
        T.save_paper(st)
        T.save_journal({"trades": [{"id": "r7", "position": 7, "mode": "robot", "symbol": "EURUSD", "side": side,
                                    "volume": 0.2, "entry": entry, "sl": sl, "status": "open",
                                    "opened_at": (opened or at(10, 8)).isoformat()}]})


class TestEntries(RobotCase):
    def test_opens_sized_trade_once_per_candle(self):
        r = self.make()
        out = r.cycle()
        self.assertTrue(any("OPENED BUY" in m for m in out["messages"]), out)
        pos = T.paper_state()["positions"]
        self.assertEqual(len(pos), 1)
        p = pos[0]
        a = T.atr(r.closed_bars("EURUSD"), 14)[-1]
        self.assertAlmostEqual(p["entry"] - p["sl"], 1.5 * a, delta=0.00002)
        self.assertAlmostEqual(p["tp"] - p["entry"], 2 * (p["entry"] - p["sl"]), delta=0.00002)
        risk_cash = (p["entry"] - p["sl"]) * p["volume"] * 100000
        self.assertLessEqual(risk_cash, 10000 * 0.005 * 1.001)
        self.assertGreater(risk_cash, 10000 * 0.005 * 0.8)
        t = T.journal()["trades"][0]
        self.assertEqual((t["mode"], t["setup"]), ("robot", "robot:custom"))
        out = r.cycle()                                    # same closed candle: nothing new
        self.assertEqual(len(T.paper_state()["positions"]), 1)
        self.assertTrue(T.load_json(T.tpath("robot_state.json"))["heartbeat"])

    def test_drops_forming_candle(self):
        b = m15_bars(end_before=at(10, 9, 10))
        b.t[-1] = int(at(10, 9).timestamp())              # candle 09:00-09:15 still forming at 09:00
        r = self.make(bars={"EURUSD": b})
        self.assertEqual(r.closed_bars("EURUSD").t[-1], b.t[-2])

    def test_dry_run_places_nothing(self):
        r = self.make()
        r.dry = True
        out = r.cycle()
        self.assertTrue(any("dry run" in m for m in out["messages"]))
        self.assertEqual(T.paper_state()["positions"], [])


class TestLimits(RobotCase):
    def blocked(self, r, text):
        out = r.cycle()
        self.assertEqual(T.paper_state()["positions"], [], out)
        joined = " ".join(out["blocks"] + out["messages"] + out["pairs"])
        self.assertIn(text, joined)

    def test_paused(self):
        self.blocked(self.make(enabled=False), "robot paused")

    def test_outside_sessions(self):
        self.now = at(10, 20)                              # Tokyo morning
        self.blocked(self.make(), "outside sessions")

    def test_no_new_trades_late(self):
        self.now = at(10, 16, 30)
        self.blocked(self.make(), "no overnight")

    def test_daily_losses(self):
        T.save_journal({"trades": [
            {"id": "r1", "mode": "robot", "symbol": "EURUSD", "status": "closed", "pnl": -50,
             "opened_at": at(10, 3).isoformat(), "closed_at": at(10, 4).isoformat()},
            {"id": "r2", "mode": "robot", "symbol": "EURUSD", "status": "closed", "pnl": -50,
             "opened_at": at(10, 5).isoformat(), "closed_at": at(10, 6).isoformat()}]})
        self.blocked(self.make(), "2 losses today")

    def test_bias_and_news(self):
        r = self.make(bias={"EURUSD": {"side": "sell", "set": self.now.isoformat()}})
        self.blocked(r, "bias is sell")
        T.save_json(T.tpath("calendar.json"), {"events": [
            {"title": "CPI m/m", "currency": "USD", "impact": "High",
             "time": (self.now + dt.timedelta(minutes=20)).astimezone(dt.timezone.utc).isoformat()}]})
        T.save_json(T.tpath("robot_state.json"), {})
        self.blocked(self.make(), "news: USD CPI")

    def test_stale_bias_expires(self):
        r = self.make(bias={"EURUSD": {"side": "none", "set": (self.now - dt.timedelta(hours=30)).isoformat()}})
        self.assertEqual(r.bias("EURUSD"), "both")

    def test_real_mt5_account_refused(self):
        T.save_json(T.tpath("config.json"), {"mode": "mt5", "allow_live": True})
        T._MT5_READY = FakeMT5(trade_mode=2)
        r = self.make()
        r.cfg = T.load_config()
        with self.assertRaises(SystemExit) as e:
            r.cycle()
        self.assertIn("live", str(e.exception))


class TestManagement(RobotCase):
    def test_flat_before_rollover(self):
        self.put_position()
        self.now = at(10, 16, 50)
        r = self.make()
        out = r.cycle()
        self.assertEqual(T.paper_state()["positions"], [])
        self.assertTrue(any("closed" in m for m in out["messages"]))
        self.assertIn("rollover", T.journal()["trades"][0]["why"])

    def test_position_held_over_rollover_is_closed(self):
        self.put_position(opened=at(9, 15))               # opened yesterday before 17:00
        self.now = at(10, 3)
        r = self.make(rules={"entry_long": [["close", "<", 0]]}, sessions=[])
        r.cycle()
        self.assertEqual(T.paper_state()["positions"], [])

    def test_flat_requested_closes_and_pauses(self):
        self.put_position()
        r = self.make(flat_requested=True)
        r.cycle()
        self.assertEqual(T.paper_state()["positions"], [])
        rc = robot.load_rc()
        self.assertFalse(rc["enabled"])
        self.assertFalse(rc["flat_requested"])

    def test_breakeven(self):
        self.put_position(entry=1.0800, sl=1.0780)
        T._PRICE_OVERRIDE["EURUSD"] = 1.0825             # +1.25R
        r = self.make(rules={"entry_long": [["close", "<", 0]]})
        out = r.cycle()
        self.assertEqual(T.paper_state()["positions"][0]["sl"], 1.0801)
        self.assertTrue(any("stop moved" in m for m in out["messages"]))


class TestTelegram(RobotCase):
    def test_commands_only_from_owner(self):
        tg = FakeTG(chat="42", updates=[
            {"update_id": 5, "message": {"chat": {"id": 999}, "text": "/flat"}},
            {"update_id": 6, "message": {"chat": {"id": 42}, "text": "/stop"}}])
        r = self.make(tg=tg)
        r.cycle()
        rc = robot.load_rc()
        self.assertFalse(rc["enabled"])
        self.assertFalse(rc["flat_requested"])            # the stranger's /flat was ignored
        self.assertEqual(r.state["tg_offset"], 7)
        self.assertTrue(any("paused" in s for s in tg.sent))
        self.assertEqual(T.paper_state()["positions"], [])

    def test_status_command(self):
        tg = FakeTG(chat="42", updates=[{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}}])
        self.make(tg=tg, enabled=False).cycle()
        self.assertTrue(any("Robot PAUSED" in s for s in tg.sent))


class TestCLI(RobotCase):
    def test_setup_show_bias_set(self):
        if os.path.exists(robot.rc_path()):
            os.remove(robot.rc_path())
        rc, out, err = quiet(robot.main, ["setup", "--pairs", "eurusd", "xauusd", "--tf", "m15", "--risk", "0.5"])
        self.assertEqual(rc, 0, err)
        self.assertIn("NO OVERNIGHT", out)
        self.assertTrue(T.load_config()["no_overnight"])
        rc, out, err = quiet(robot.main, ["bias", "XAUUSD", "sell", "--note", "strong USD"])
        self.assertIn("bias XAUUSD = sell", out)
        rc, out, err = quiet(robot.main, ["set", "risk_pct", "3"])
        self.assertIn("above your account limit", err)
        rc, out, err = quiet(robot.main, ["set", "strategy", "breakout"])
        self.assertIn("strategy breakout", out)
        rc, out, err = quiet(robot.main, ["pause"])
        self.assertFalse(robot.load_rc()["enabled"])

    def test_backtest_runs_with_session_filter(self):
        bars = m15_bars(n=1200)
        saved = T.get_bars
        T.get_bars = lambda *a, **k: bars
        try:
            quiet(robot.main, ["setup", "--pairs", "EURUSD", "--force"])
            rc, out, err = quiet(robot.main, ["backtest", "--days", "10"])
        finally:
            T.get_bars = saved
        self.assertEqual(rc, 0, err)
        self.assertIn("ROBOT BACKTEST", out)
        self.assertIn("EURUSD", out)


if __name__ == "__main__":
    unittest.main()
