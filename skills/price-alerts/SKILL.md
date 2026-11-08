---
name: price-alerts
description: >-
  Price Alerts for forex and gold. Rings once when a pair crosses a price
  (above, below or either way), moves a set percent from the previous daily
  close, or when a trading session opens (Sydney, Tokyo, London, New York,
  London/New York overlap); can re-arm after price comes back. Knows forex
  hours (Sunday 17:00 to Friday 17:00 New York) and shows times in the user's
  zone. Prices from MetaTrader 5 desktop (live) or Yahoo (delayed). Also
  explains how to set native alerts in the MT5 phone app. Use when the user
  says "alert me when EURUSD hits", "notify me", "watch this price", "tell me
  when London opens", "is the market open", "نبهني", "تنبيه سعر", "إذا وصل
  السعر", "متى تفتح جلسة لندن", "السوق مفتوح؟".
---

# Price Alerts

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/alerts.py add EURUSD above 1.0900 --note "breakout"
python3 SKILL/alerts.py add EURUSD below 1.0800
python3 SKILL/alerts.py add GBPJPY cross 193.00        # either direction
python3 SKILL/alerts.py add XAUUSD pct 1               # +/-1% from previous daily close
python3 SKILL/alerts.py add EURUSD session london      # london | newyork | tokyo | sydney | overlap
python3 SKILL/alerts.py add EURUSD above 1.0900 --repeat   # re-arms after price falls back
python3 SKILL/alerts.py list | remove 3 | rearm 3 | clear
python3 SKILL/alerts.py check                          # one pass
python3 SKILL/alerts.py watch --every 60               # loop while forex is open (Ctrl+C stops)
python3 SKILL/alerts.py price EURUSD USDJPY            # quick quote (spread too, with MT5)
python3 SKILL/alerts.py clock                          # open/closed, sessions, your time
```

## Keeping alerts running

An alert only rings while something checks. Tell the user which fits:

1. **MetaTrader 5 phone app (best for a phone-only trader).** Native alerts
   with push notifications, 24/5, no computer needed. Walk them through it:
   on iPhone/Android: Quotes (الأسعار) -> tap the pair -> the alarm-clock /
   Alerts option (on some versions: Settings (إعدادات) -> Alerts / تنبيهات)
   -> + -> condition "Bid >" or "Bid <" -> value -> Done. Menu names differ a
   little between app versions; if unsure, ask the user for a screenshot of
   their menu.
2. **This session**: run `watch` in the background and report when it rings.
3. **Claude Code `/loop`**: `/loop 5m python3 SKILL/alerts.py check`.
4. **Always on (Windows)**: a Task Scheduler job running `check` every few
   minutes; offer to write it.

Yahoo forex prices are delayed and are mid prices; MT5 alerts use the
broker's bid. Small differences of a pip or two are normal.

## Answer format

In the user's language. Confirm each alert in one line ("تم: تنبيه #1 إذا
تجاوز EURUSD مستوى 1.0900"). When one rings, lead with it, then the next step
(`entry-timer` for a breakout, `exit-planner trail` for an open trade).
