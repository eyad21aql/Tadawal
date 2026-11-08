---
name: risk-manager
description: >-
  Risk Manager. Sizes every trade from the stop (risk a fixed % of the account,
  default 1%), using the real contract specs from MetaTrader 5 (tick value,
  lot step, min/max volume) or shares on the paper account, and gates every
  order with PASS/BLOCK before it can reach the broker: stop required and on
  the right side, risk per trade, total open risk, max position size, minimum
  reward:risk, max open positions, daily loss limit, blocked symbols, no
  shorting unless allowed. Also shows open risk and today's P&L. Use when the
  user says "how many shares", "position size", "how much should I risk",
  "is this trade safe", "check my order", "risk check", "كم سهم أشتري", "حجم
  الصفقة", "كم أخاطر", "إدارة المخاطر", "افحص الأمر", or ALWAYS before
  order-executor.
---

# Risk Manager

The gate between an idea and an order. The order executor refuses anything
that does not carry a fresh PASS from here for its exact contents.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/risk.py rules                                  # the limits in force
python3 SKILL/risk.py size AAPL --entry 190 --stop 185 --target 200
python3 SKILL/risk.py size AAPL --entry 190 --stop 185 --target 200 --write   # -> trader/orders.json
python3 SKILL/risk.py size AAPL --stop 185 --target 200 --write --type market  # entry = current price
python3 SKILL/risk.py check                                  # PASS / BLOCK every proposed order
python3 SKILL/risk.py status                                 # positions, open risk, today's P&L
```

`--write` adds an order (`o1`, `o2`, ...) with status `proposed`. With an
`--entry` the order is a limit order unless `--type` says otherwise.

## The limits (trader/config.json)

| key | default | meaning |
| --- | --- | --- |
| `risk_per_trade_pct` | 1.0 | max loss if the stop is hit, % of balance |
| `max_total_risk_pct` | 6.0 | all open stops together |
| `max_position_pct` | 25 | notional of one position, % of balance |
| `min_rr` | 1.5 | minimum reward:risk when a target is set |
| `max_open_positions` | 5 | |
| `max_daily_loss_pct` | 3.0 | after this realized loss today, everything is BLOCKed |
| `blocked_symbols` | [] | |
| `allow_short` | false | sells (shorts) are blocked unless true |

Only change limits when the user explicitly asks, and show them the new
values. Never loosen a limit to make a blocked order pass: tell the user
which rule blocked it and offer to resize (smaller volume, closer stop,
further target) instead.

## Workflow

1. Get entry and stop (from `entry-timer` / `exit-planner` or the user).
   A trade without a stop is not sized: ask for one or propose one.
2. `size ... --write`. Read the notes: capped by max position, stop too
   wide for the account, R:R below minimum.
3. `check`. Explain every BLOCK reason in plain words. Warnings (no target,
   already holding the symbol, market closed) are shown but do not block.
4. Only then hand over to `order-executor`.

Any edit to an order (volume, prices, side, type) voids its PASS: run
`check` again. A PASS also expires after 60 minutes.

## Answer format

In the user's language. Example in Arabic:
"الحجم: 13 سهم. المخاطرة 65$ (0.65٪ من الحساب). الحجم انخفض بسبب حد 25٪ للمركز.
العائد إلى المخاطرة 2.0. الفحص: PASS ✅".
