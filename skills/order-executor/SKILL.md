---
name: order-executor
description: >-
  Order Executor for forex and gold. Turns an approved order into a trade, one
  ticket at a time, only after the risk manager passed that exact order and
  the user typed yes for it. Three modes: paper (simulated ledger), manual
  (the user places it in the MetaTrader 5 phone app with exact step-by-step
  instructions, then the fill is recorded, for phone-only traders) and mt5
  (sent to the MT5 desktop terminal on Windows; demo by default, real
  accounts refused unless the user enabled them; spread check). Also lists
  positions, tightens stops, closes trades fully or partly and cancels orders.
  Use when the user says "place the order", "buy EURUSD", "sell gold",
  "execute", "close my trade", "move my stop", "cancel the order", "my open
  trades", "I placed it at 1.0851", "نفذ الصفقة", "ادخل شراء", "ادخل بيع",
  "سكّر الصفقة", "عدل الستوب", "ألغِ الأمر", "صفقاتي المفتوحة", "دخلت على".
---

# Order Executor

This skill can move real money. Follow every step, every time.

## The rules (non-negotiable)

1. Only orders in `trader/orders.json` with a fresh PASS from
   `risk-manager check` can be placed. Never edit a limit, the config or the
   order to get around a BLOCK.
2. Show the ticket first (`order.py tickets`) and ask for a yes for THAT
   ticket: side, lots, pair, type, price, SL, TP, and where it goes (paper /
   manual / MT5 demo / MT5 REAL).
3. Pass the user's own reply word for word in `--confirm`. Only their
   latest message counts. A yes from earlier, or "go ahead with everything",
   does not cover a new or changed ticket. Never write the confirmation
   yourself.
4. One ticket per yes. Several approved: place them one by one, report each.
5. If the user has a trading plan with `enforce` on, the order also needs
   IN PLAN from `trading-plan check`. If it is out of plan and the user still
   wants it, ask for their reason and pass it with
   `--override-plan "<their words>"`; it is recorded as a rule break. Never
   override on your own.
6. Never set `"allow_live": true` or change `"mode"` yourself. If the user
   asks, explain the risk and show them the exact line to change in
   `trader/config.json`; they make the change.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/order.py account                                  # mode and account
python3 SKILL/order.py tickets                                  # READY tickets with codes
python3 SKILL/order.py place o1 --ticket 3fa2c1d09b7e --confirm "نعم"
python3 SKILL/order.py filled o1 --price 1.08512                # manual mode: record the fill
python3 SKILL/order.py positions
python3 SKILL/order.py modify 7 --sl 1.0850 --confirm "yes"     # stop can only tighten
python3 SKILL/order.py close 7 --confirm "yes" [--volume 0.1] [--price 1.0890]
python3 SKILL/order.py cancel o2
```

The script enforces: ticket code = current order contents, PASS younger
than 60 minutes, a clear yes (anything with no / لا / wait / انتظر is
refused), each ticket once, stops never loosened, MT5 REAL accounts blocked
unless `allow_live`, MT5 spread under `max_spread_pips`. Every action is
logged in `trader/executions.json` with the user's words.

## Modes (trader/config.json "mode")

- **paper** (default): simulated fills at the current price; market orders
  only while forex is open; limit/stop orders rest until `positions` sees
  the price reach them; SL/TP applied by `positions`.
- **manual** (phone app users): `place` prints the exact steps for the MT5
  app (Quotes -> pair -> Trade -> type -> volume -> SL/TP -> Buy/Sell).
  The user places it, then tells you the fill price or sends a screenshot
  of the trade; record it with `filled o1 --price X`. `positions` flags
  trades whose SL/TP was probably hit; record closes with
  `close ID --price X --confirm yes` using the price from their app or
  screenshot. Read screenshots of the MT5 Trade tab carefully (pair,
  direction, volume, open price, SL, TP, profit).
- **mt5** (Windows desktop): real orders to the MT5 terminal. Needs
  `pip install MetaTrader5`, the terminal open and logged in, and the
  **Algo Trading** button on. If the broker renames pairs (`EURUSDm`,
  `EURUSD.pro`), set `symbol_suffix` / `symbol_map`. Start on **demo**.

The MT5 phone app cannot be connected to a script; on a phone, use manual.

## Answer format

In the user's language. Before placing: the ticket in one line plus "type
yes to place this ticket" ("اكتب نعم لتنفيذ هذه الصفقة"). After: PLACED /
APPROVED - PLACE IT YOURSELF / NOT PLACED, the steps or the fill, and the
next step (alerts, exit review).
