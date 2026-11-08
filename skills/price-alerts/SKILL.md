---
name: price-alerts
description: >-
  Price Alerts. Rings once when a US stock crosses a price (above, below or
  either way), moves a set percent from the previous close, or when the NYSE
  opening bell rings; can re-arm after price comes back. Knows NYSE hours,
  2026-2027 holidays and 1 p.m. early closes. Prices from MetaTrader 5 when
  connected (live) or Yahoo (delayed). `watch` keeps checking until the close;
  `check` runs once (good for a scheduled task or /loop). Use when the user
  says "alert me when", "tell me if AAPL hits 200", "notify me", "watch this
  price", "is the market open", "when does the market open", "price of X",
  "نبهني", "تنبيه سعر", "إذا وصل السعر", "متى يفتح السوق", "السوق مفتوح؟".
---

# Price Alerts

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/alerts.py add AAPL above 200 --note "breakout"
python3 SKILL/alerts.py add AAPL below 180
python3 SKILL/alerts.py add TSLA cross 250          # either direction
python3 SKILL/alerts.py add NVDA pct 5              # +/-5% from previous close, once a day
python3 SKILL/alerts.py add SPY bell                # opening bell, once a day
python3 SKILL/alerts.py add AAPL above 200 --repeat # re-arms after price falls back below
python3 SKILL/alerts.py list
python3 SKILL/alerts.py remove 3 | rearm 3 | clear
python3 SKILL/alerts.py check                       # one pass
python3 SKILL/alerts.py watch --every 60            # loop until the close (Ctrl+C stops)
python3 SKILL/alerts.py price AAPL MSFT             # quick quote
python3 SKILL/alerts.py clock                       # NYSE open/closed
```

## Keeping alerts running

An alert only rings while something is checking. Tell the user which option
fits them:

1. **This session**: run `watch` in the background and report when it rings.
2. **Claude Code `/loop`**: `/loop 5m python3 SKILL/alerts.py check` keeps
   checking every 5 minutes while the session is open.
3. **Always on (Windows)**: a Task Scheduler job that runs `check` every few
   minutes on market days. Offer to write the command for them.
4. **MetaTrader 5 itself**: MT5 has native price alerts with push
   notifications to the MT5 phone app (Tools -> Options -> Notifications).
   For 24/7 alerts on the phone this is the most reliable, and the user can
   set it up in the terminal: walk them through it.

Yahoo prices are delayed (~15 minutes for some feeds); MT5 prices are live.

## Answer format

In the user's language. Confirm each alert in one line ("تم: تنبيه #1 إذا
تجاوز AAPL سعر 200"). When an alert rings, lead with it, then suggest the next
step (e.g. `entry-timer` for a breakout, `exit-planner trail` for a position).
