---
name: news-monitor
description: >-
  News Monitor for forex and gold. Watches headlines for the user's currency
  pairs (Yahoo Finance, EURUSD=X...), press releases of the Federal Reserve,
  ECB and Bank of England, and lists the next high-impact events for the
  pairs' currencies; shows only what is new since the last check, flags rate
  decisions and intervention talk, tags each item (rates, inflation, jobs,
  growth, central bank, risk) and then explains in plain words what it means
  for the currency and the pair. Keeps a watchlist. Use when the user says
  "any news on EURUSD", "what's moving the dollar", "why did gold drop",
  "what did the Fed say", "morning briefing", "أخبار اليورو دولار", "وش صار
  على الدولار", "ليش طاح الذهب", "أخبار الفوركس", "ملخص الصباح".
---

# News Monitor

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/news.py --pairs EURUSD GBPJPY            # new items only
python3 SKILL/news.py --pairs XAUUSD --hours 24 --all
python3 SKILL/news.py                                  # watchlist + open trades
python3 SKILL/news.py --no-banks                       # skip Fed / ECB / BoE releases
python3 SKILL/news.py watch add EURUSD USDJPY XAUUSD | watch list | watch remove USDJPY
```

Run `economic-analyst` `econ.py calendar` first to also see the upcoming
high-impact events for the pairs' currencies.

## Output

`!!` severe (rate decision, intervention), `!` central-bank release, then
time (user's zone), pair or currency, `(+/-/?)` tone hint, title,
`[OPEN TRADE]` when the user has a position in it.

The `+/-` hint only counts words like "rises/falls", "hawkish/dovish". It is
not a direction call. **You** judge: read title and summary (open the link
with WebFetch when it matters) and say for each important item what
happened, which currency it helps or hurts and why (hawkish central bank or
hot data = usually stronger currency; dovish or weak data = weaker; risk-off
= usually stronger USD, JPY, CHF and gold), and whether it changes the plan
for an open trade (e.g. NFP in 30 minutes -> consider tightening the stop or
taking partial profit).

For "why did X move today", also search the web: RSS can lag.

## Answer format

In the user's language. Group by currency, most important first. 1-2 lines
per item. End with "what to watch next" (the next red-flag release, a level,
a central-bank speaker). For a morning briefing combine with
`economic-analyst today`, `price-alerts clock` and `order-executor positions`.

News moves fast and headlines can be wrong. Not financial advice.
