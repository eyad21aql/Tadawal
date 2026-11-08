---
name: order-executor
description: >-
  Order Executor. Sends approved orders to MetaTrader 5 (market, limit or stop,
  always with a stop-loss) or to a local paper account, one ticket at a time,
  and only after the risk manager passed that exact order and the user typed
  yes for it. Also shows open positions, tightens stops, closes positions
  (fully or partly) and cancels pending orders. Refuses MT5 REAL accounts
  unless the user enabled live trading themselves. Use when the user says
  "place the order", "buy it", "execute", "send it to MT5", "close my
  position", "move my stop", "cancel the order", "show my positions",
  "connect MetaTrader", "نفذ الأمر", "اشتر", "بيع", "أرسله للميتاتريدر", "سكّر
  الصفقة", "عدل الستوب", "ألغِ الأمر", "صفقاتي المفتوحة".
---

# Order Executor

This skill can move real money. Follow every step, every time.

## The rules (non-negotiable)

1. Only orders in `trader/orders.json` that have a fresh PASS from
   `risk-manager check` can be placed. Never edit a limit, the config, or
   the order to get around a BLOCK.
2. Show the ticket first (`order.py tickets`) and ask the user for a yes for
   THAT ticket: side, volume, symbol, type, price, SL, TP, and the account
   (paper / MT5 demo / MT5 REAL).
3. Pass the user's own reply word for word in `--confirm`. Only their
   latest message counts. A yes from earlier in the chat, or a general "go
   ahead with everything", does not cover a new or changed ticket. Never
   type the confirmation yourself.
4. One ticket per yes. If the user approves several, place them one by one
   and report each result.
5. Never set `"allow_live": true` or switch `"mode"` yourself. If the user
   asks, explain the risk, then show them the exact line to change in
   `trader/config.json` and let them do it.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/order.py account                         # which account/mode is active
python3 SKILL/order.py tickets                         # READY tickets with their codes
python3 SKILL/order.py place o1 --ticket 3fa2c1d09b7e --confirm "نعم"
python3 SKILL/order.py positions                       # (paper: also fills resting orders, stops, targets)
python3 SKILL/order.py modify 123456 --sl 188 --confirm "yes"    # stop can only tighten
python3 SKILL/order.py close 123456 --confirm "yes" [--volume 5]
python3 SKILL/order.py cancel o2
```

The script itself enforces: ticket code = current order contents, PASS less
than 60 minutes old, affirmative confirmation (rejects anything containing
no / لا / wait / انتظر ...), each order placed once, stops never loosened,
REAL accounts blocked unless `allow_live` is true. Every action is logged to
`trader/executions.json` with the user's words.

## Modes (trader/config.json)

- `"mode": "paper"` (default): simulated fills at the current price; market
  orders only while NYSE is open; limit/stop orders rest until
  `positions` sees the price reach them.
- `"mode": "mt5"`: real orders to the MetaTrader 5 terminal. Needs Windows,
  `pip install MetaTrader5`, the terminal open and logged in, and
  **Algo Trading** switched on in its toolbar. US stocks at many MT5 brokers
  are CFDs with a different symbol name (`AAPL.US`, `#AAPL`, `AAPL.NAS`):
  set `symbol_suffix` or `symbol_map`. Start on a **demo** account.

## Connecting MetaTrader 5 (walk the user through it)

1. Install MetaTrader 5 from the broker, log in (demo first).
2. `pip install MetaTrader5` (Windows Python 3.8+).
3. In MT5: Tools -> Options -> Expert Advisors -> allow algorithmic trading;
   press the **Algo Trading** button.
4. In `trader/config.json`: `"mode": "mt5"`, `"data_source": "auto"`, and the
   symbol suffix. Optional `"mt5": {"login": 123, "server": "Broker-Demo"}`
   with the password in the `MT5_PASSWORD` environment variable, never in
   the chat or a file.
5. `order.py account` should show the account and `demo`.

## Answer format

In the user's language. Before placing: the ticket in one line plus "type
yes to place this ticket" ("اكتب نعم لتنفيذ هذا الأمر"). After: PLACED or NOT
PLACED, fill price, position number, and the next step (alerts, exit review).
