---
name: chart-scanner
description: >-
  Chart Scanner. Reads a US stock chart and lists what is on it: trend
  structure (higher highs / lower lows), support and resistance with touch
  counts, candlestick patterns (doji, hammer, shooting star, engulfing,
  morning/evening star, three soldiers/crows, inside bar), chart patterns
  (double top/bottom, head and shoulders and inverse, ascending/descending/
  symmetrical triangles, wedges, channels, ranges) with neckline and measured
  target, level breakouts with volume, RSI divergences and gaps. Can draw an
  SVG chart with the levels and patterns marked. Also reads chart screenshots
  the user uploads. Use when the user says "scan the chart", "what pattern is
  this", "support and resistance for X", "is this a head and shoulders",
  "analyze this chart", "اقرأ الشارت", "حلل الشارت", "وش النموذج", "الدعم
  والمقاومة", "نماذج الشموع".
---

# Chart Scanner

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/scan.py AAPL                          # daily, last 250 bars
python3 SKILL/scan.py NVDA --tf H4                  # 4-hour
python3 SKILL/scan.py TSLA --svg trader/charts/tsla.svg   # also draw the chart
python3 SKILL/scan.py AAPL --csv bars.csv --json    # MT5-exported bars, JSON
python3 SKILL/scan.py AAPL --swing-atr 1.5          # smaller swings = more, noisier patterns
```

Data comes from MetaTrader 5 when connected, otherwise Yahoo, or `--csv`.

## Reading the result

- `structure`: uptrend / downtrend / expanding / contracting, from the last
  two swing highs and lows (zigzag of at least `--swing-atr` ATRs).
- `levels`: price (xN touches). More touches = stronger level.
- each finding: `[BULL|BEAR|NEUT] date name | status | level | target | conf`.
  - chart patterns are **forming** until price closes through the neckline or
    trend line; only then **confirmed**. Say this clearly: a forming pattern
    is not a signal yet.
  - `target` is the classic measured move (pattern height projected from the
    break). It is a reference, not a promise.
  - breakouts on less than 1.5x average volume are marked low confidence.
- `tally` counts bullish vs bearish findings: context, not a vote.

## Screenshots

If the user uploads a chart image, read it yourself first: timeframe, trend,
obvious levels, patterns, indicators shown. If you can identify the symbol,
run the scanner too and compare. Say what you can and cannot see (e.g. no
volume panel, unclear prices on the axis).

## Answer format

In the user's language. Start with the one-line picture ("uptrend, pulling
back to support 182"), then the 2-4 findings that matter most, each with the
level that confirms or cancels it. If you made an SVG, give its path.
Hand levels to `entry-timer` / `exit-planner` when the user wants to trade.

Pattern recognition is mechanical and can be wrong. Not financial advice.
