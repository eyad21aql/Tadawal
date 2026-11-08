---
name: currency-strength
description: >-
  Currency Strength meter and correlation matrix for forex. Ranks the 8 major
  currencies (USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD) from all 28 pairs on a
  short and a long window (moves measured in ATRs), shows whether each is
  rising or falling, picks the pairs that line up the strongest against the
  weakest (buy or sell, aligned or not), and measures how pairs move together
  so the user does not stack the same trade twice (EURUSD + GBPUSD,
  long gold + short USDCHF...). Use when the user asks "which currency is
  strongest", "what pair should I trade today", "strength meter", "are these
  trades correlated", "أقوى عملة", "أضعف عملة", "وش أتداول اليوم", "قوة
  العملات", "الارتباط بين الأزواج", "صفقاتي مرتبطة؟".
---

# Currency Strength

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/strength.py rank                        # H1: last 24 bars and last 96 bars
python3 SKILL/strength.py --tf H4 --bars 30 rank      # swing view
python3 SKILL/strength.py --tf D1 --bars 5 rank       # last week vs last month
python3 SKILL/strength.py pairs --top 5               # strongest vs weakest pairs
python3 SKILL/strength.py corr EURUSD GBPUSD AUDUSD XAUUSD USDJPY
python3 SKILL/strength.py corr --positions            # check the open trades
python3 SKILL/strength.py --csv-dir bars/ rank        # offline: bars/EURUSD.csv, bars/GBPJPY.csv ...
```

Global options (`--tf`, `--bars`, `--csv-dir`, `--source`) go before the
command. Loading 28 pairs from Yahoo takes ~10-20 seconds; MT5 desktop is
instant.

## Reading it

- short = momentum now; long = the underlying trend (4x the window).
  The best candidates have both pointing the same way for both currencies
  ("aligned"). `[short and long disagree: wait]` = probably a pullback.
- rising / falling = is the currency gaining or losing strength compared
  with one window ago. Strong and still rising beats strong but fading.
- Strength chooses WHAT to trade. The chart chooses WHEN: hand the pair to
  `chart-scanner`, `market-structure` or `entry-timer`. Confirm with
  `economic-analyst bias` and `cot-tracker`.
- Correlation: +0.8 and above = the two pairs are almost the same trade
  (long both = double risk); -0.8 and below = they move opposite (long one,
  short the other = double risk; long both = a hedge). Tell the user to
  count correlated trades as one when sizing, and `risk-manager` warns about
  the same currency appearing in several trades.

## Answer format

In the user's language. A ranking line ("الأقوى: EUR ثم GBP … الأضعف: JPY"),
the top 2-3 pairs with direction, and one sentence on correlation if the
user has open trades. Example: "اليورو الأقوى والين الأضعف على الساعة وعلى
الأربع ساعات، فـ EURJPY شراء هو المرشح الأول. انتظر ارتداد على الشارت قبل
الدخول."
