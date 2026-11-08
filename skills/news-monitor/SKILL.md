---
name: news-monitor
description: >-
  News Monitor. Watches headlines (Yahoo Finance) and SEC 8-K filings for the
  stocks the user holds or watches, shows only what is new since the last
  check, decodes 8-K items (earnings, CEO/CFO change, restatement,
  bankruptcy, delisting notice, impairment, cyber incident...), flags
  severity and positions held, tags each item (earnings, guidance, analyst,
  deal, legal, management, capital) and then judges in plain words whether it
  is good or bad for the stock and why. Keeps a watchlist. Use when the user
  says "any news on my stocks", "what's happening with Apple", "why is X
  down", "morning briefing", "monitor the news", "check the 8-Ks", "أخبار
  السهم", "وش صار على", "ليش نزل السهم", "أخبار محفظتي", "ملخص الصباح".
---

# News Monitor

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/news.py --tickers AAPL NVDA          # new items only
python3 SKILL/news.py --tickers AAPL --hours 24 --all
python3 SKILL/news.py                              # watchlist + open positions
python3 SKILL/news.py --market                     # + S&P 500 / Nasdaq headlines
python3 SKILL/news.py watch add AAPL MSFT | watch list | watch remove MSFT
```

SEC requires a contact: `SEC_USER_AGENT="Name email@example.com"` or
`"sec_user_agent"` in `trader/config.json`. Without it 8-Ks are skipped and
headlines still work.

## Output

`!!` severe (bankruptcy, restatement, delisting, auditor change, debt
acceleration, impairment, change of control, cyber incident), `!` notable
(earnings, management change, big deal), then date, ticker, `(+/-/?)`
keyword hint, title, `[HELD]` when the user has a position.

The `+/-` hint is a keyword count, nothing more. **You** do the judging:
read the title and summary (open the link with WebFetch when it matters),
then say for each important item: what happened, likely effect on the stock
(positive / negative / unclear) and why, and whether it changes the plan for
a position (e.g. earnings tomorrow -> consider the stop; restatement ->
serious red flag).

For "why is X down today" also search the web for the latest news, since RSS
can lag.

## Answer format

In the user's language. Group by stock, most severe first. 1-2 lines per
item. End with "what to watch next" (earnings date, a level, a follow-up
filing). For a morning briefing, combine with `price-alerts clock` and
`order-executor positions`.

News moves fast and headlines can be wrong. Not financial advice.
