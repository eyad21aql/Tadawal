---
name: cot-tracker
description: >-
  COT Tracker ("follow the whales") for forex and gold. Reads the CFTC
  Commitments of Traders report (free, weekly) and shows what the large
  speculators (hedge funds, big funds) hold in each currency future (EUR, GBP,
  JPY, CHF, CAD, AUD, NZD, MXN, the US Dollar Index) and gold: net long/short,
  weekly and 4-week change, % long, the COT index (position in its 52-week
  range; crowded long above 80, crowded short below 20), flips and buying or
  selling streaks; compares base vs quote for a pair (EURUSD, USDJPY, XAUUSD).
  Use when the user asks "what are the big players doing", "COT report",
  "are funds long the euro", "smart money positioning", "تقرير COT", "وش
  يسوون الحيتان", "مراكز صناديق التحوط", "هل الصناديق شارية اليورو", "تتبع
  الأموال الذكية".
---

# COT Tracker

The forex version of "copy the big players": who is positioned long or short
in each currency, from the official CFTC report.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/cot.py report                               # all majors + USD index + gold
python3 SKILL/cot.py report --currencies EUR JPY XAU
python3 SKILL/cot.py pair EURUSD USDJPY XAUUSD            # base vs quote for each pair
python3 SKILL/cot.py history EUR --last 12                # week by week
```

Data: publicreporting.cftc.gov (Legacy futures-only report), cached 12 hours.
For USD the ICE US Dollar Index futures are used; for gold, COMEX gold.

## Facts to say every time

- Published Friday 15:30 New York (Friday night Riyadh time) for positions
  as of the previous **Tuesday**. It is 3 days old when it comes out:
  a weekly background bias, not an entry signal.
- Futures positions mirror spot: speculators long EUR futures = long EURUSD.
  For USDJPY, speculators long JPY futures = bearish USDJPY.
- Extremes (index above 80 or below 20) are crowded trades: the trend can
  continue, but when news turns the unwind is fast (e.g. a crowded JPY short
  squeezing).
- A flip from net short to net long (or the reverse), or several weeks of
  buying in a row, is more useful than the level alone.

## Answer format

In the user's language. A short table for the currencies the user trades
(net, weekly change, index), then the pair view in one sentence each, then
how to use it ("supports buying dips in EURUSD, but the trade is crowded:
keep stops tight"). Combine with `economic-analyst bias` and the chart.
Example: "الصناديق الكبيرة شارية اليورو بقوة (المؤشر ٩٢ من ١٠٠)، والدولار
عليه بيع. هذا يدعم الشراء في EURUSD، بس الصفقة مزدحمة، فخل الستوب قريب."
