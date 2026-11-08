---
name: chart-scanner
description: >-
  Chart Scanner for forex and gold. Reads a currency-pair chart (from data or
  from an MT5 screenshot) and lists what is on it: trend structure (higher
  highs / lower lows), support and resistance with touch counts, candlestick
  patterns (doji, pin bar/hammer, shooting star, engulfing, morning/evening
  star, three soldiers/crows, inside bar), chart patterns (double top/bottom,
  head and shoulders and inverse, ascending/descending/symmetrical triangles,
  wedges, channels, ranges) with neckline and measured target in pips, level
  breakouts, RSI divergences and weekend gaps. Can draw an SVG chart with the
  levels and patterns marked. Use when the user says "scan the chart", "what
  pattern is this", "support and resistance on EURUSD", "analyze this chart",
  sends a chart screenshot, "اقرأ الشارت", "حلل الشارت", "وش النموذج", "الدعم
  والمقاومة", "نماذج الشموع", "هذا رأس وكتفين؟".
---

# Chart Scanner

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/scan.py EURUSD                          # H1, last 300 bars
python3 SKILL/scan.py GBPJPY --tf H4                  # 4-hour
python3 SKILL/scan.py XAUUSD --tf D1 --svg trader/charts/xauusd.svg   # also draw the chart
python3 SKILL/scan.py EURUSD --csv eurusd_h1.csv --json   # MT5-exported bars
python3 SKILL/scan.py EURUSD --swing-atr 1.5          # smaller swings = more, noisier patterns
```

Data: MetaTrader 5 desktop when connected, otherwise Yahoo (free, delayed, no
volume for forex), or `--csv`. To export from MT5 desktop: open the chart ->
File -> Save As (CSV), or View -> Symbols -> Bars -> Export.

## Reading the result

- `structure`: uptrend / downtrend / expanding / contracting, from the last
  two swing highs and lows.
- `levels`: price (xN touches). More touches = stronger level. Round numbers
  (1.0800, 150.00, 2700) often act as levels too: mention them.
- each finding: `[BULL|BEAR|NEUT] time name | status | level | target (pips) | conf`.
  - chart patterns are **forming** until a candle CLOSES through the neckline
    or trend line; only then **confirmed**. A forming pattern is not a signal.
  - `target` is the classic measured move. A reference, not a promise.
  - forex breakouts often fake out: suggest waiting for a retest.
- Times are UTC (bar open). Convert to the user's time when you mention them.

## Screenshots

When the user sends a chart from the MT5 app, read it yourself first:
pair, timeframe (shown top-left), trend, swing highs/lows, levels, candle
patterns at the right edge, indicators on screen. Say what you can and cannot
see (axis prices too small, no indicator panel). If the pair is readable, run
the scanner on the same timeframe and compare.

## Answer format

In the user's language. One line with the picture ("EURUSD H1 downtrend,
testing support 1.0385"), then the 2-4 findings that matter most, each with
the level that confirms or cancels it and the distance in pips. If you drew
an SVG, give its path. Hand levels to `entry-timer` / `exit-planner` when the
user wants to trade.

Pattern recognition is mechanical and can be wrong. Not financial advice.
