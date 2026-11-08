---
name: time-machine
description: >-
  Time Machine (backtesting) for forex and gold. Replays a trading strategy on
  past bars of a currency pair and reports what would have happened: return,
  CAGR, max drawdown, Sharpe, win rate, profit factor, expectancy in R, pips
  per trade, every trade in lots, a buy-and-hold comparison and a
  walk-forward split (in-sample vs out-of-sample) to catch curve fitting.
  Built-in strategies (EMA cross, RSI mean reversion, Donchian breakout, MACD
  trend, pullback) or the user's own rules as JSON; ATR stops, R targets,
  trailing stops, spread in pips, commission per lot, parameter grids, buy
  and sell. No look-ahead: signals on the close, fills on the next open. Use
  when the user says "backtest", "would this strategy have worked", "test my
  strategy on EURUSD", "optimize the settings", "اختبار رجعي", "باك تست",
  "جرب الاستراتيجية على الماضي", "لو طبقتها وش كان صار".
---

# Time Machine

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/backtest.py EURUSD --tf H1 --strategy ema_cross --fast 20 --slow 50
python3 SKILL/backtest.py GBPUSD --tf H4 --strategy rsi_revert --rsi-low 30 --rsi-high 60
python3 SKILL/backtest.py USDJPY --tf D1 --strategy breakout --lookback 55 --sl-atr 2 --trail-atr 3
python3 SKILL/backtest.py XAUUSD --tf H1 --strategy macd_trend --tp-r 2 --spread-pips 3
python3 SKILL/backtest.py EURUSD --rules trader/my_rules.json --spread-pips 1.2
python3 SKILL/backtest.py EURUSD --strategy ema_cross --grid fast=10,20,30 slow=50,100,200
python3 SKILL/backtest.py EURUSD --tf M15 --csv eurusd_m15.csv        # MT5-exported bars
```

Options: `--capital 10000 --risk-pct 1 --sl-atr 2 (0 = none) --tp-r R
--trail-atr N --max-bars N --spread-pips 1.0 --commission 0 (per lot per side)
--leverage 10 --long-only --start --end --split 0.7`. Results are saved under
`trader/backtests/`.

Set `--spread-pips` to the user's real spread (EURUSD ~0.1-1.5, GBPJPY ~2-4,
gold ~2-4 "pips" of $0.10). Ignoring spread makes scalping systems look far
better than they are.

Yahoo gives ~10 years of daily, 2 years of hourly and 60 days of 5-15 minute
forex bars. For longer intraday history, export bars from MT5 desktop and use
`--csv`.

## Turning the user's idea into rules

```json
{"entry_long":  [["close", ">", "ema200"], ["rsi14", "crosses_above", 30]],
 "exit_long":   [["rsi14", ">", 65]],
 "entry_short": [["close", "<", "ema200"], ["rsi14", "crosses_below", 70]],
 "exit_short":  [["rsi14", "<", 35]]}
```

Series: `open high low close volume`, `smaN emaN rsiN atrN`, `macd
macd_signal macd_hist`, `bb_upperN bb_lowerN bb_midN`, `highestN lowestN`
(previous N bars), `vol_smaN`, or a number. Operators: `> < >= <=
crosses_above crosses_below`. All conditions in a list must be true. Repeat
the rules back in plain words before running.

## Reading the result honestly

- Fewer than 30 trades: not reliable. Say so.
- Good in-sample but bad out-of-sample = probably curve-fitted.
- With `--grid`, recommend a setting from a broad area of good results,
  never the single best row.
- Cross pairs (EURGBP, GBPJPY...) convert P&L at today's rate: approximate.
- Past results do not predict the future; live spreads, slippage and news
  spikes are worse than in a test.

## Answer format

In the user's language: return and max drawdown, win rate and profit factor,
average pips per trade, number of trades; then warnings; then 2-3 concrete
suggestions (add a trend filter, avoid the Asian session, wider stop).
