---
name: time-machine
description: >-
  Time Machine (backtesting). Replays a trading strategy on years of past bars
  for a US stock and reports what would have happened: return, CAGR, max
  drawdown, Sharpe, win rate, profit factor, expectancy in R, exposure, every
  trade, a comparison with buy-and-hold and a walk-forward split (in-sample vs
  out-of-sample) to catch curve fitting. Built-in strategies (EMA cross, RSI
  mean reversion, Donchian breakout, MACD trend, pullback) or the user's own
  rules as JSON; ATR stops, R targets, trailing stops, slippage, commission,
  parameter grids. No look-ahead: signals on the close, fills on the next
  open. Use when the user says "backtest", "would this strategy have worked",
  "test my strategy", "what if I had bought every time", "optimize the
  settings", "اختبار رجعي", "باك تست", "جرب الاستراتيجية على الماضي", "لو
  طبقتها وش كان صار".
---

# Time Machine

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/backtest.py AAPL --strategy ema_cross --fast 20 --slow 50
python3 SKILL/backtest.py SPY --strategy rsi_revert --rsi-low 30 --rsi-high 60 --start 2018-01-01
python3 SKILL/backtest.py NVDA --strategy breakout --lookback 55 --sl-atr 2 --trail-atr 3
python3 SKILL/backtest.py AAPL --strategy macd_trend --tp-r 2
python3 SKILL/backtest.py AAPL --rules trader/my_rules.json
python3 SKILL/backtest.py AAPL --strategy ema_cross --grid fast=10,20,30 slow=50,100,200
python3 SKILL/backtest.py AAPL --tf H1 --csv aapl_h1.csv      # MT5-exported bars
```

Options: `--capital 10000 --risk-pct 1 --sl-atr 2 (0 = no stop) --tp-r R
--trail-atr N --max-bars N --slippage-bps 2 --commission 0.0 --allow-short
--fractional --start --end --split 0.7`. Results are saved under
`trader/backtests/`.

Yahoo gives about 10 years of daily bars, 2 years of hourly and 60 days of
5-15 minute bars. For longer intraday history, export bars from MT5
(History Center / right-click chart -> Save as CSV) and pass `--csv`.

## Turning the user's idea into rules

Write a JSON file when the user describes their own strategy:

```json
{"entry_long": [["close", ">", "sma200"], ["rsi14", "crosses_above", 30]],
 "exit_long":  [["rsi14", ">", 65]]}
```

Series: `open high low close volume`, `smaN emaN rsiN atrN`, `macd
macd_signal macd_hist`, `bb_upperN bb_lowerN bb_midN`, `highestN lowestN`
(previous N bars, excluding the current one), `vol_smaN`, or a number.
Operators: `> < >= <= crosses_above crosses_below`. All conditions in a list
must be true. Add `entry_short`/`exit_short` with `--allow-short`.
Repeat the rules back to the user in plain words before running.

## Reading the result honestly

- Compare with buy & hold on BOTH return and drawdown.
- Fewer than 30 trades: statistics are not reliable; say so.
- If in-sample is good and out-of-sample is bad, it is probably curve-fitted.
- With `--grid`, recommend a setting from a broad area of good results,
  never just the single best row.
- Past results do not predict future results. Fills, gaps and costs in real
  trading are worse than in a test.

## Answer format

In the user's language: a 4-line summary (return vs buy & hold, max
drawdown, win rate + profit factor, number of trades), then the warnings,
then 2-3 concrete suggestions (e.g. add a trend filter, wider stop).
