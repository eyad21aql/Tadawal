---
name: economic-analyst
description: >-
  Economic Analyst for forex and gold. Shows the economic calendar for the
  week or today in the user's time zone (NFP, CPI, GDP, PMI, retail sales,
  central-bank rate decisions and speeches) by currency and impact, compares
  released numbers with the forecast (beat/miss and whether that is positive
  or negative for the currency, with unemployment and claims inverted), and
  turns recent surprises into a fundamental tilt for a pair (e.g. "bearish
  EURUSD: USD data beating, EUR data missing") plus the next risk events.
  Feeds entry-timer and news-monitor with upcoming high-impact news. Use when
  the user asks "what news is today", "economic calendar", "when is NFP",
  "CPI result", "did the data beat", "fundamental analysis of EURUSD", "الأخبار
  الاقتصادية اليوم", "الأجندة الاقتصادية", "متى الوظائف الأمريكية", "نتيجة
  التضخم", "التحليل الأساسي".
---

# Economic Analyst

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/econ.py calendar                                  # this week, high + medium, your time
python3 SKILL/econ.py calendar --pairs EURUSD XAUUSD --impact high
python3 SKILL/econ.py calendar --past --impact all              # include released ones
python3 SKILL/econ.py today
python3 SKILL/econ.py surprises --days 14
python3 SKILL/econ.py actual "Non-Farm Employment Change" USD 254K   # record a result
python3 SKILL/econ.py bias EURUSD GBPUSD --days 14
```

Times are shown in `timezone` from `trader/config.json` (default
Asia/Riyadh). The calendar is cached for 30 minutes in
`trader/calendar.json`; every fetch is also merged into
`trader/calendar_history.json`.

## Actual results

The free weekly feed always has forecast and previous, but not always the
actual. When a release the user cares about is out and `surprises` lists it
under "no actual stored", look the number up on the web (reliable sources:
the agency itself, e.g. BLS for NFP/CPI, or a major news wire), tell the
user the source, and record it with `econ.py actual "<title words>" <CCY>
<value>`. Then run `bias`.

## How to explain it

- Beat = actual better than forecast for the economy. For unemployment rate
  and jobless claims, higher is worse.
- Stronger data -> markets expect higher rates -> usually a stronger
  currency. Rate decisions and the central bank's tone matter more than
  any single number.
- Gold (XAU) usually rises when US data is weak / rates are expected lower,
  and on risk-off.
- The first spike after a release often reverses; spreads widen. Do not
  suggest entering in the first minutes after red-flag news.
- `bias` is a tilt from recent surprises, not a signal. Combine it with
  `entry-timer` / `chart-scanner` and `cot-tracker`.

## Answer format

In the user's language, times in the user's zone. For a calendar: a short
list of red-flag events (time, currency, event, forecast). For a result:
actual vs forecast, beat/miss, likely effect on the pairs the user trades.
Example: "الوظائف الأمريكية 254 ألف مقابل توقع 150 ألف: أقوى بكثير من المتوقع،
إيجابي للدولار. المتوقع ضغط على EURUSD والذهب."
