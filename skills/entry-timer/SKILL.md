---
name: entry-timer
description: >-
  Entry Timer. Decides whether NOW is a good moment to enter a US stock, and if
  not, exactly what to wait for: trend on two timeframes, EMA20/50/200, RSI,
  MACD momentum, ATR, nearest support/resistance, volume, extension, plus NYSE
  session timing. Gives a long and a short checklist score (ENTRY ZONE / WAIT /
  NO TRADE), a pullback plan and a breakout plan with a reference stop. Data
  from MetaTrader 5 when connected, otherwise Yahoo, or an exported CSV. Use
  when the user asks "is it a good time to buy X", "when should I enter",
  "entry point for NVDA", "should I buy now or wait", "متى أدخل", "وقت الدخول",
  "هل أشتري الحين", "نقطة دخول".
---

# Entry Timer

A checklist that tells the user whether the conditions for an entry are met
right now. It does not predict prices. Never present it as a guarantee.

## Run

Commands run from the user's project folder (data goes to `./trader/`).
`SKILL` below means this skill's folder (the base directory shown when the
skill loads). Use `python` instead of `python3` on Windows.

```bash
python3 SKILL/timer.py AAPL                 # daily bars, weekly as higher timeframe
python3 SKILL/timer.py AAPL --tf H1         # hourly, daily as higher timeframe
python3 SKILL/timer.py AAPL --tf H4 --json  # machine-readable
python3 SKILL/timer.py AAPL --csv bars.csv  # bars exported from MT5 (no internet needed)
python3 SKILL/timer.py --clock              # NYSE open/closed, next open
```

Timeframes: M1 M5 M15 M30 H1 H4 D1 W1. If the user trades daily swings use D1;
for intraday use H1 or M15. Ask only if it is unclear and it matters.

## How to read the output

- **trend / higher TF trend**: up = price > EMA50 > EMA200 with EMA50 rising.
- **score**: points for trend (2), higher timeframe agreeing (1), price at
  value (1), RSI room (1), MACD momentum (1), room to the next level (1), volume
  (0.5); minus points for chasing (RSI > 75, more than 3 ATR above EMA20).
  ENTRY ZONE = within 2 points of the maximum, WAIT = within 4, else NO TRADE.
- **missing** lines are the "what to wait for" list. Turn them into plain
  conditions: "wait for a pullback to 182-184", "wait for a daily close above
  195 on volume".
- **pullback plan / breakout plan**: two ways in, each with a reference stop.
  Hand the chosen plan to `exit-planner` (precise stop and targets) and
  `risk-manager` (position size).
- **timing**: market closed, first/last 15 minutes of the session, or that a
  daily signal should be acted on near the next open.

## Answer format

Reply in the user's language (Arabic if they write Arabic). Lead with the
verdict in one line, then 3-6 bullets of the reasons, then the plan:

> الحكم: انتظر (٤٫٥ من ٧٫٥). الاتجاه اليومي صاعد والأسبوعي صاعد، لكن السعر
> بعيد ١٫٩ ATR عن EMA20. ادخل بأمر limit عند 182.4 مع وقف مرجعي 178.9، أو
> اختراق فوق 195.2 بإغلاق وحجم أعلى من المتوسط.

If the user also sent a chart screenshot, read it yourself and say where it
agrees or disagrees with the numbers.

## Hand-off

Entry decided -> `exit-planner plan` -> `risk-manager size --write` ->
`risk-manager check` -> `order-executor`. Never skip the risk check.

Not financial advice: say so once when you give a buy/sell view.
