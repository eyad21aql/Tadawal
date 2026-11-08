"""Tests for trading-plan, currency-strength, market-structure and trade-coach."""
import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_skills import (T, TempTrader, OPEN_CLOCK, quiet, load, synth, risk, order)  # noqa: E402

plan = load("trading-plan", "plan")
strength = load("currency-strength", "strength")
smc = load("market-structure", "smc")
coach = load("trade-coach", "coach")
NY = T.NY


def iso(day, h, m=0):
    return dt.datetime(2026, 11, day, h, m, tzinfo=NY).isoformat()


# ---------------------------------------------------------------- trading plan

class TestPlan(TempTrader):
    def base(self):
        p = dict(plan.COMMON, **plan.TEMPLATES["day"], style="day")
        good = {"symbol": "EURUSD", "side": "buy", "type": "limit", "volume": 0.3, "entry": 1.0850, "sl": 1.0820,
                "tp": 1.0910, "setup": "trend-pullback"}
        clock = dict(OPEN_CLOCK, now_iso=iso(10, 9))   # Tuesday 09:00 New York, London/NY overlap
        stats = {"trades_today": 0, "losses_in_a_row": 0, "week_pnl": 0.0}
        return p, good, clock, stats

    def test_in_plan_needs_checklist_then_passes(self):
        p, o, clock, st = self.base()
        v, R, _ = plan.evaluate(o, p, clock, st, 10000, [])
        self.assertEqual(v, "NEEDS CHECKLIST")
        v, R, _ = plan.evaluate(o, p, clock, st, 10000, [], [True] * 5)
        self.assertEqual(v, "IN PLAN")
        v, R, _ = plan.evaluate(o, p, clock, st, 10000, [], [True, True, False, True, True])
        self.assertEqual(v, "OUT OF PLAN")
        self.assertIn("trigger", R[0])

    def test_rules(self):
        p, o, clock, st = self.base()
        cases = [
            (dict(o, symbol="GBPJPY"), {}, {}, None, "not one of your plan pairs"),
            (dict(o, setup=None), {}, {}, None, "no setup named"),
            (dict(o, setup="hunch"), {}, {}, None, "not in your plan"),
            (dict(o, sl=1.0845), {}, {}, None, "stop is 5.0 pips"),
            (dict(o, tp=1.0880), {}, {}, None, "reward:risk 1.00"),
            (o, {"trades_today": 3}, {}, None, "already 3 trades"),
            (o, {"losses_in_a_row": 2}, {}, None, "losses in a row"),
            (o, {"week_pnl": -700.0}, {}, None, "weekly loss"),
            (dict(o, type="market"), {}, {"sessions": ["Tokyo"]}, None, "outside your sessions"),
            (o, {}, {"now_iso": iso(13, 13)}, None, "Friday after 12:00"),
            (o, {}, {}, ["USD CPI m/m in 20 min"], "news blackout"),
        ]
        for order_, stp, clk, news, why in cases:
            v, R, _ = plan.evaluate(order_, p, dict(clock, **clk), dict(st, **stp), 10000, news or [], [True] * 5)
            self.assertEqual(v, "OUT OF PLAN", why)
            self.assertTrue(any(why in r for r in R), (why, R))

    def test_day_stats_streak(self):
        now = dt.datetime(2026, 11, 10, 12, 0, tzinfo=NY)
        j = [{"opened_at": iso(10, 3), "closed_at": iso(10, 4), "status": "closed", "pnl": 50},
             {"opened_at": iso(10, 5), "closed_at": iso(10, 6), "status": "closed", "pnl": -40},
             {"opened_at": iso(10, 7), "closed_at": iso(10, 8), "status": "closed", "pnl": -40},
             {"opened_at": iso(9, 10), "closed_at": iso(9, 11), "status": "closed", "pnl": -100}]
        s = plan.day_stats(j, [], now)
        self.assertEqual(s["trades_today"], 3)       # the 9th 10:00 is before Monday 17:00
        self.assertEqual(s["losses_in_a_row"], 2)
        self.assertEqual(s["week_pnl"], -130.0)

    def test_executor_enforces_plan_and_logs_override(self):
        self.cfg()
        T.market_clock = lambda now=None, cfg=None: dict(OPEN_CLOCK)
        quiet(plan.main, ["init", "--style", "day"])
        rc, out, err = quiet(risk.main, ["size", "EURUSD", "--side", "buy", "--stop-pips", "30", "--rr", "2",
                                         "--setup", "trend-pullback", "--write"])
        self.assertIn("plan.py check", out)
        quiet(risk.main, ["check"])
        o = T.load_orders()["orders"][0]
        h = T.order_hash(o)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "yes"])
        self.assertIn("outside your trading plan", err)
        rc, out, err = quiet(plan.main, ["check", "o1"])
        self.assertIn("NEEDS CHECKLIST", out)
        rc, out, err = quiet(plan.main, ["check", "o1", "--checklist", "yes,yes,yes,yes,yes"])
        self.assertIn("IN PLAN", out, out + err)
        rc, out, err = quiet(order.main, ["place", "o1", "--ticket", h, "--confirm", "نعم"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(T.journal()["trades"][0]["setup"], "trend-pullback")
        # second order outside the plan, overridden with a reason
        quiet(risk.main, ["size", "GBPJPY", "--side", "sell", "--stop-pips", "40", "--rr", "2", "--write"])
        quiet(risk.main, ["check", "--id", "o2"])
        o2 = [x for x in T.load_orders()["orders"] if x["id"] == "o2"][0]
        quiet(plan.main, ["check", "o2", "--checklist", "yes,yes,yes,yes,yes"])
        rc, out, err = quiet(order.main, ["place", "o2", "--ticket", T.order_hash(o2), "--confirm", "yes"])
        self.assertIn("outside your trading plan", err)
        rc, out, err = quiet(order.main, ["place", "o2", "--ticket", T.order_hash(o2), "--confirm", "yes",
                                          "--override-plan", "strong conviction"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(T.journal()["trades"][1]["rule_break"], "strong conviction")


# ---------------------------------------------------------------- currency strength

TRENDS = {"USD": 0, "EUR": 0.0006, "GBP": 0.0003, "JPY": -0.0006, "CHF": 0.0, "CAD": -0.0002, "AUD": 0.0001,
          "NZD": -0.0003}


class TestStrength(unittest.TestCase):
    def test_rank_and_pairs(self):
        bars = synth.fx_universe(TRENDS)
        self.assertEqual(len(bars), 28)
        rows = strength.rank(bars, 24)
        order_ = [r["currency"] for r in rows]
        self.assertEqual(order_[0], "EUR")
        self.assertEqual(order_[-1], "JPY")
        best = strength.best_pairs(rows, 3)
        self.assertEqual((best[0]["pair"], best[0]["side"]), ("EURJPY", "buy"))
        self.assertTrue(best[0]["aligned"])
        usdjpy = [b for b in strength.best_pairs(rows, 28) if b["pair"] == "USDJPY"][0]
        self.assertEqual(usdjpy["side"], "buy")

    def test_corr_and_overlap(self):
        bars = synth.fx_universe(TRENDS, n=200, noise=0.002)
        names, m = strength.corr({k: bars[k] for k in ("EURUSD", "EURJPY", "USDJPY", "GBPUSD")})
        self.assertEqual(m[("EURUSD", "EURUSD")], 1.0)
        self.assertGreater(m[("EURUSD", "GBPUSD")], 0)
        self.assertLess(m[("EURUSD", "USDJPY")], 0)
        w = strength.overlap_warnings([{"symbol": "EURUSD", "side": "buy"}, {"symbol": "GBPUSD", "side": "buy"}],
                                      {("EURUSD", "GBPUSD"): 0.85, ("GBPUSD", "EURUSD"): 0.85})
        self.assertIn("one bigger trade", w[0])

    def test_csv_dir(self):
        bars = synth.fx_universe({"USD": 0, "EUR": 0.0005, "JPY": -0.0005}, n=150)
        d = tempfile.mkdtemp()
        for p, b in bars.items():
            with open(os.path.join(d, p + ".csv"), "w") as f:
                f.write("Date,Open,High,Low,Close\n")
                for i in range(len(b)):
                    f.write("%d,%f,%f,%f,%f\n" % (b.t[i], b.o[i], b.h[i], b.l[i], b.c[i]))
        rc, out, err = quiet(strength.main, ["--csv-dir", d, "--bars", "20", "rank", "--currencies", "USD", "EUR", "JPY"])
        self.assertEqual(rc, 0, err)
        self.assertIn("strongest EUR, weakest JPY -> look at EURJPY", out)


# ---------------------------------------------------------------- market structure

def bars_from(rows, symbol="EURUSD"):
    t = [1704205800 + 900 * i for i in range(len(rows))]
    o, h, l, c = zip(*rows)
    return T.Bars(symbol, "M15", t, list(o), list(h), list(l), list(c), [0.0] * len(rows), "test")


class TestSMC(unittest.TestCase):
    def test_bos_and_choch(self):
        b = synth.path([1.0800, 1.0850, 1.0820, 1.0880, 1.0845, 1.0910, 1.0870, 1.0800], steps=12, symbol="EURUSD", tf="M15")
        swings, events, trend = smc.structure(b, 3)
        kinds = [(e["type"], e["dir"]) for e in events]
        self.assertIn(("BOS", "bull"), kinds)
        self.assertIn(("CHoCH", "bear"), kinds)
        self.assertEqual(trend, "down")
        labels = [s["label"] for s in swings if s["label"]]
        self.assertIn("HH", labels)
        self.assertIn("HL", labels)

    def test_fvg(self):
        rows = [(1.0800, 1.0805, 1.0795, 1.0802)] * 20
        rows += [(1.0802, 1.0806, 1.0800, 1.0804), (1.0804, 1.0840, 1.0803, 1.0838), (1.0838, 1.0850, 1.0820, 1.0846)]
        rows += [(1.0846, 1.0852, 1.0835, 1.0848)] * 5
        b = bars_from(rows)
        g = smc.fvgs(b, 0.0010)
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["dir"], g[0]["bottom"], g[0]["top"]), ("bull", 1.0806, 1.0820))
        self.assertEqual(g[0]["state"], "open")
        b.l[-1] = 1.0800
        self.assertEqual(smc.fvgs(b, 0.0010), [])   # filled

    def test_equal_highs_and_sweep(self):
        rows = []
        for k in range(4):   # two equal highs at 1.0900 separated by dips
            rows += [(1.0850, 1.0860, 1.0845, 1.0855)] * 4 + [(1.0880, 1.0900, 1.0875, 1.0890)] + [(1.0860, 1.0865, 1.0840, 1.0850)] * 4
        rows[-9] = (1.0880, 1.0900, 1.0875, 1.0890)
        b = bars_from(rows)
        swings, _, _ = smc.structure(b, 3)
        pools, _ = smc.liquidity(b, swings, 0.0010)
        self.assertTrue(any(p["side"] == "buy-side" and abs(p["level"] - 1.0900) < 1e-9 for p in pools))
        b.o.append(1.0890); b.h.append(1.0912); b.l.append(1.0880); b.c.append(1.0885); b.v.append(0); b.t.append(b.t[-1] + 900)
        swings, _, _ = smc.structure(b, 3)
        pools, sweeps = smc.liquidity(b, swings, 0.0010)
        self.assertTrue(any(s["side"] == "buy-side" and s["bias"] == "bear" for s in sweeps))

    def test_dealing_range_and_analyze(self):
        r = smc.dealing_range([{"type": "L", "price": 1.08}, {"type": "H", "price": 1.10}], 1.085)
        self.assertEqual(r["zone"], "discount")
        self.assertAlmostEqual(r["ote_buy"][0], 1.10 - 0.79 * 0.02)
        b = synth.path([1.0800, 1.0850, 1.0820, 1.0880, 1.0845, 1.0910, 1.0870, 1.0940, 1.0880], steps=12,
                       symbol="EURUSD", tf="M15")
        o = smc.analyze(b, None, 3)
        self.assertIn("MARKET STRUCTURE", o["text"])
        self.assertTrue(o["setup"] or o["no_setup"])
        if o["setup"]:
            s = o["setup"]
            self.assertEqual(s["side"], "buy")
            self.assertLess(s["stop"], s["entry"])
            self.assertGreater(s["target"], s["entry"])
            self.assertGreaterEqual(s["rr"], 2.0)


# ---------------------------------------------------------------- trade coach

def journal():
    tr = []

    def add(i, day, h, mins, sym, side, vol, r, setup, sl=True, emo=None, rb=None):
        o = dt.datetime(2026, 11, day, h, 0, tzinfo=NY)
        tr.append({"id": "p%d" % i, "symbol": sym, "side": side, "volume": vol, "entry": 1.08, "sl": 1.077 if sl else None,
                   "exit": 1.08, "pnl": round(r * 100, 2), "r": r, "pips": round(r * 30, 1), "opened_at": o.isoformat(),
                   "closed_at": (o + dt.timedelta(minutes=mins)).isoformat(), "status": "closed", "setup": setup,
                   "emotion": emo, "rule_break": rb})
    add(1, 2, 4, 120, "EURUSD", "buy", 0.3, 2.1, "trend-pullback", emo="calm")
    add(2, 2, 9, 300, "GBPUSD", "sell", 0.3, -1.0, "breakout-retest")
    add(3, 2, 14, 30, "GBPUSD", "sell", 0.6, -1.5, "breakout-retest", emo="revenge")
    add(4, 3, 4, 90, "EURUSD", "buy", 0.3, 1.8, "trend-pullback", emo="calm")
    add(5, 3, 10, 40, "XAUUSD", "buy", 0.3, 0.4, None, emo="fomo")
    add(6, 4, 4, 100, "EURUSD", "buy", 0.3, 2.4, "trend-pullback")
    add(7, 4, 9, 400, "GBPUSD", "buy", 0.3, -1.0, "breakout-retest", emo="fomo")
    add(8, 4, 10, 500, "USDJPY", "sell", 0.5, -1.3, None, sl=False, rb="felt sure")
    add(9, 4, 11, 30, "EURUSD", "sell", 0.3, 0.5, "range-reversal")
    add(10, 4, 12, 20, "EURUSD", "buy", 0.3, 0.3, "range-reversal")
    add(11, 5, 8, 25, "EURUSD", "buy", 0.3, 0.6, "trend-pullback", emo="fomo")
    return tr


class TestCoach(TempTrader):
    def test_detectors(self):
        cfg = T.load_config()
        trades = [coach.enrich(t, cfg) for t in journal()]
        kinds = {f["kind"]: f for f in coach.detect(trades, {"max_trades_per_day": 3}, [])}
        for k in ("revenge", "upsize", "overtrading", "cut_winners", "big_losses", "no_stop", "hold_losers", "rule_breaks"):
            self.assertIn(k, kinds, k)
        self.assertEqual(kinds["revenge"]["ids"], ["p3"])
        self.assertEqual(kinds["rule_breaks"]["net"], -130.0)
        self.assertEqual(kinds["overtrading"]["count"], 2)

    def test_news_detector_and_review(self):
        cfg = T.load_config()
        trades = [coach.enrich(t, cfg) for t in journal()]
        cal = [{"currency": "USD", "impact": "High", "title": "NFP", "time": dt.datetime(2026, 11, 4, 10, 5, tzinfo=NY).isoformat()}]
        kinds = {f["kind"]: f for f in coach.detect(trades, None, cal)}
        self.assertEqual(sorted(kinds["news"]["ids"]), ["p8"])
        out = coach.review(trades, None, cal, "test")
        self.assertIn("KEEP DOING", out["text"])
        self.assertIn("setup trend-pullback", out["text"])
        self.assertIn("WHERE YOU LOSE", out["text"])
        self.assertIn("breakout-retest", out["text"])

    def test_cli_tag_and_review(self):
        T.save_journal({"trades": journal()})
        rc, out, err = quiet(coach.main, ["tag", "p5", "--setup", "trend-pullback", "--emotion", "bored", "--mistake", "no plan"])
        self.assertEqual(rc, 0, err)
        t = [x for x in T.journal()["trades"] if x["id"] == "p5"][0]
        self.assertEqual((t["setup"], t["emotion"], t["mistakes"]), ("trend-pullback", "bored", ["no plan"]))
        rc, out, err = quiet(coach.main, ["review"])
        self.assertEqual(rc, 0, err)
        self.assertIn("saved:", out)
        self.assertTrue(os.listdir(T.tpath("reviews")))


if __name__ == "__main__":
    unittest.main()
