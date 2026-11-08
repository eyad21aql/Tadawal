---
name: entry-timer
description: >-
  Entry Timer for forex and gold. Decides whether NOW is a good moment to enter
  a currency pair (EURUSD, GBPJPY, XAUUSD...) and if not, exactly what to wait
  for: trend on two timeframes, EMA20/50/200, RSI, MACD momentum, ATR in pips,
  nearest support/resistance, extension, the active trading session (Sydney,
  Tokyo, London, New York, overlap), rollover and weekly close, and
  high-impact news due for the pair's currencies. Gives a buy and a sell
  checklist score (ENTRY ZONE / WAIT / NO TRADE) and a pullback and a breakout
  plan with stop distance in pips. Reads MT5 screenshots too. Use when the
  user asks "is it a good time to buy EURUSD", "should I sell gold now", "entry
  point", "when should I enter", "متى أدخل", "وقت الدخول", "هل أشتري اليورو
  دولار الحين", "أبيع الذهب؟", "نقطة دخول", "أدخل بيع ولا شراء".
---

# Entry Timer

A checklist that says whether the conditions for an entry are met right now.
It does not predict prices. Never present it as a guarantee.

## Run

From the user's project folder (data goes to `./trader/`). `SKILL` = this
skill's folder (the base directory shown when the skill loads). On Windows
use `python` instead of `python3`.

```bash
python3 SKILL/timer.py EURUSD                 # H1, with D1 as the higher timeframe
python3 SKILL/timer.py GBPJPY --tf M15        # scalping/intraday, H1 as higher timeframe
python3 SKILL/timer.py XAUUSD --tf H4         # swing, D1 as higher timeframe
python3 SKILL/timer.py EURUSD --csv eurusd_h1.csv --htf-csv eurusd_d1.csv   # bars exported from MT5
python3 SKILL/timer.py --clock                # forex open/closed, sessions, your local time
```

Pair names: `EURUSD`, `EUR/USD` and `eurusd` all work. Ask the user's
timeframe only if it is unclear and it matters (scalper M5-M15, day trader
H1, swing H4-D1).

Run `economic-analyst` `econ.py calendar` first (once a day is enough) so the
timer can warn about news; without it the output says "news not checked".

## Reading the output

- trend up = price > EMA50 > EMA200 with EMA50 rising (down = the mirror).
- score: trend 2, higher timeframe agreeing 1, price at value 1, RSI room 1,
  MACD momentum 1, room to the next level 1, tick volume 0.5 (only when the
  data has volume; Yahoo forex data has none). Minus points for chasing.
  ENTRY ZONE = within 2 points of the top, WAIT = within 4, else NO TRADE.
- missing lines = the "what to wait for" list. Turn them into concrete
  conditions: "wait for a pullback to 1.0842-1.0848", "wait for an H1 close
  above 1.0905".
- plans give an entry, a reference stop and the stop in pips. Hand the chosen
  plan to `exit-planner` (stop and targets) and `risk-manager` (lot size).
- timing lines: forex closed, rollover (spreads widen around 17:00 New York,
  01:00 Riyadh in winter), weekly close, a quiet session for this pair, or
  high-impact news soon. Never recommend entering minutes before a red-flag
  release (NFP, CPI, rate decisions).

## Screenshots from the MT5 app

If the user sends a chart screenshot, read it yourself first: pair,
timeframe, trend, obvious levels, indicators shown, current price. If you can
read the pair, run the timer too and say where the two agree or disagree.
Prices read from a quote screenshot can be given to the scripts:
`TADAWAL_PRICES="EURUSD=1.03929,USDJPY=156.398" python3 ...`.

## Answer format

Reply in the user's language (Arabic if they write Arabic). Verdict first,
then 3-6 reasons, then the plan in pips:

> الحكم: انتظر (٤ من ٧). الاتجاه على الساعة صاعد والأربع ساعات صاعد، لكن السعر
> بعيد ٢ ATR عن EMA20. ادخل شراء limit عند 1.0845 والستوب 1.0815 (٣٠ نقطة)،
> أو شراء stop فوق 1.0905 بعد إغلاق شمعة ساعة فوقها. انتبه: CPI الأمريكي بعد ساعتين.

Hand-off: `exit-planner plan` -> `risk-manager size --write` -> `risk-manager
check` -> `order-executor`. Not financial advice: say so once when you give a
buy/sell view.
