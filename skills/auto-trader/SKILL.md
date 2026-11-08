---
name: auto-trader
description: >-
  Auto Trader: a rules-based trading robot for MetaTrader 5 (forex and gold)
  that Claude sets up, tests and supervises. The robot runs on a Windows VPS
  or PC next to the MT5 terminal, enters only on closed-candle signals of a
  tested strategy, in the allowed sessions, outside news, in the direction
  Claude set for the day; sizes each trade from its stop, passes the risk
  check, moves the stop to breakeven, never holds a trade through the swap
  rollover (no overnight), stops after daily/weekly loss limits, refuses real
  accounts until the user enables them, and reports to Telegram with
  /status /stop /start /flat. Use when the user says "trade for me",
  "automate my trading", "run the robot", "robot status", "stop the robot",
  "set today's bias", "خل Claude يتداول عني", "الروبوت", "شغل الروبوت",
  "وقف الروبوت", "حالة الروبوت", "تداول آلي", "اكسبرت".
---

# Auto Trader

The robot executes; Claude decides what it is allowed to do and checks its
work. Nothing about it guarantees profit: its job is to follow tested rules
and never break the risk limits.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python SKILL/robot.py setup --pairs EURUSD XAUUSD --strategy trend_pullback --tf M15 --risk 0.5
python SKILL/robot.py show
python SKILL/robot.py backtest --days 60          # the exact rules, sessions, costs, no overnight
python SKILL/robot.py once --dry-run              # one cycle, shows what it would do
python SKILL/robot.py run --verbose               # the loop (keep it running on the VPS)
python SKILL/robot.py status                      # alive? today's trades, P&L, why entries are blocked
python SKILL/robot.py bias XAUUSD sell --note "strong USD after NFP"
python SKILL/robot.py pause | resume | flat
python SKILL/robot.py set risk_pct 0.5 | set max_trades_per_day 3 | set strategy breakout | set tf M15
python SKILL/robot.py telegram-chats | telegram-test
```

Strategies (`set strategy`): `trend_pullback` (EMA200 trend, RSI turns back
from a pullback), `breakout` (close beyond the 20-candle high/low with the
trend), `ema_cross`. Or custom rules: `set rules trader/my_rules.json` (same
format as time-machine). Stop = `sl_atr` x ATR, target = `tp_r` x the stop.

## The order of work (do not skip steps)

1. **Plan and limits**: risk 0.5% per trade to start, max 3 trades a day, stop
   after 2 losses, weekly limit 4%. `no_overnight` is always on for the robot.
2. **Backtest** with `robot.py backtest` on as much history as possible
   (export M15 history from MT5 desktop for months, not days). Do not go live
   with fewer than ~100 trades tested or a profit factor under ~1.2 after
   spread. Say so plainly if the result is weak; try another strategy or
   timeframe with the user rather than forcing it.
3. **Demo** for at least 4 weeks: `"mode": "mt5"` on a DEMO account.
   Review weekly with `trade-coach review --days 7` and `budget-manager report`.
4. **Real money** only if the demo matches the backtest and the user decides
   it: they set `"allow_live": true` in trader/config.json AND
   `robot.py set live true` themselves. Never do either for them.

## Daily supervision (before London, ~10:30 Riyadh)

1. `robot.py status`: is it running (heartbeat), what happened yesterday.
2. `economic-analyst today` (the robot also refreshes the calendar every
   hour and pauses 30 min before / 15 after high-impact news by itself).
3. Direction: `economic-analyst bias`, `cot-tracker pair`, `currency-strength
   pairs`, `market-structure` on H4. Then set `robot.py bias PAIR buy|sell|both|none`
   with a short note. A bias expires after 24 hours (back to both). Use
   `none` on days you do not trust (central-bank day, war headlines).
4. Tell the user in 3-4 lines what the robot may do today and why.

## Safety facts to keep straight

- Phone-only users cannot run the robot: it needs MT5 **desktop** on Windows
  that stays on (a VPS; many brokers give one free). The user follows trades
  in their normal MT5 phone app, because it is the same account.
- The robot only trades in `mode` paper or mt5, never manual.
- Kill switches: `robot.py pause` (no new trades), `robot.py flat` (close
  everything and pause), Telegram `/stop` `/flat` from the owner's chat only.
- It refuses REAL accounts unless both `allow_live` (config) and `live`
  (robot.json) are true. It refuses entries after `last_entry_ny`, and closes
  everything by `flat_by_ny`, every day, before the swap rollover.
- If it is not running (`status` shows NOT RUNNING), say so first: trades
  already open still have their stop and target at the broker, but nothing
  closes them before the rollover. Tell the user to close them in the app or
  restart the robot.

## Telegram setup (optional, recommended)

1. In Telegram, talk to @BotFather -> /newbot -> copy the token.
2. On the VPS set the environment variable `TELEGRAM_BOT_TOKEN` to it
   (never paste it in the chat or a file in the repo).
3. Send any message to the new bot, run `robot.py telegram-chats`, then
   `robot.py set telegram_chat_id <id>` and `robot.py telegram-test`.
Commands: /status /stop /start /flat (Arabic: الحالة، وقف، تشغيل، سكر الكل).

## Answer format

In the user's language. Status first (running or not, trades today, P&L),
then anything that needs their action. Example: "الروبوت شغال ✅. اليوم صفقتين:
ربح 24$ وخسارة 12$. الاتجاه المسموح للذهب بيع بس بعد قوة الدولار. ما فيه صفقات مفتوحة قبل التبييت."
