---
name: earnings-analyst
description: >-
  Earnings Analyst. Reads a US company's own reported numbers from SEC
  filings (XBRL company facts from its 10-Qs and 10-Ks) and lays out the last
  quarters: revenue, gross/operating/net margins, net income, diluted EPS,
  operating cash flow, each against the same quarter a year earlier and the
  previous quarter, trailing twelve months, with Q4 derived from the annual
  report, and red flags (growth slowing, margins shrinking, losses, cash flow
  below profit). Finds the latest earnings press release (8-K Item 2.02), and
  looks up analyst expectations and the next report date on the web. Use when
  the user says "how were Apple's earnings", "analyze the results", "is
  revenue growing", "did they beat expectations", "compare margins", "when
  are earnings", "نتائج الشركة", "أرباح الربع", "هل الإيرادات تنمو", "هل
  تجاوزت التوقعات", "متى النتائج".
---

# Earnings Analyst

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/earnings.py AAPL                 # 8 quarters, TTM, latest release
python3 SKILL/earnings.py NVDA --quarters 12
python3 SKILL/earnings.py MSFT GOOGL META --json
```

Needs `SEC_USER_AGENT="Name email@example.com"` (or `"sec_user_agent"` in
`trader/config.json`); SEC rejects anonymous requests. Results are cached
(12 h) and written to `trader/metrics/<TICKER>.json`.

## What the script can and cannot tell

- It shows what the company **reported**. Values marked `~` are derived
  (Q4 = full year minus Q1-Q3; EPS this way is approximate).
- Fiscal years differ (Apple's ends in September, NVIDIA's in January): use
  the `end` date column, and say "fiscal" when giving quarter names.
- **Expectations / consensus, guidance and the next report date are not in
  SEC data.** For "did they beat", search the web for the consensus EPS and
  revenue for that quarter (cite the source), and read the press release
  link (WebFetch) for guidance and management comments.

## Analysis checklist

1. Revenue trend: YoY growth for the last 4 quarters; accelerating or
   slowing?
2. Profitability: gross, operating, net margin vs a year ago.
3. EPS growth vs net income growth (buybacks make EPS grow faster).
4. Cash: operating cash flow vs net income (quality of earnings).
5. Flags printed by the script.
6. Beat or miss vs consensus (web), guidance up or down (press release).
7. Bottom line: improving, stable or deteriorating, in one sentence.

## Answer format

In the user's language. A compact table of the last 4 quarters (revenue,
YoY, EPS, net margin), then the checklist points that matter, then the
one-line bottom line. Not financial advice.
