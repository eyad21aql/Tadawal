---
name: risk-manager
description: >-
  Risk Manager for forex and gold. Calculates the lot size from the stop so
  one losing trade costs a fixed % of the account (default 1%), using the real
  pip value of the pair (EURUSD $10/pip/lot, USDJPY and crosses converted at
  the current rate, gold per $0.10), MT5 contract specs when connected, and
  gates every order with PASS/BLOCK before it can be placed: stop required
  and on the right side, risk per trade, total open risk, leverage cap,
  reward:risk, max open trades, daily loss limit, blocked pairs; warns about
  correlated trades (several trades all long USD) and too-tight stops. Use
  when the user asks "how many lots", "lot size", "how much should I risk",
  "pip value", "is this trade safe", "check my order", "كم لوت", "حجم
  اللوت", "كم أخاطر", "قيمة النقطة", "إدارة المخاطر", "افحص الصفقة", or ALWAYS
  before order-executor.
---

# Risk Manager

The gate between an idea and an order. The order executor refuses anything
that does not carry a fresh PASS from here for its exact contents.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/risk.py rules                                         # limits in force
python3 SKILL/risk.py pipvalue EURUSD GBPJPY XAUUSD                 # $ per pip per lot
python3 SKILL/risk.py size EURUSD --entry 1.0850 --stop 1.0820 --target 1.0910
python3 SKILL/risk.py size EURUSD --side buy --stop-pips 30 --rr 2  # entry = current price
python3 SKILL/risk.py size GBPJPY --entry 192.60 --stop 193.00 --rr 2 --write   # -> trader/orders.json
python3 SKILL/risk.py check                                         # PASS / BLOCK every proposed order
python3 SKILL/risk.py status                                        # positions, open risk, exposure, today
```

`--write` adds an order (`o1`, `o2`, ...) as `proposed`. Add `--setup <name>`
with the trading-plan setup the trade follows (needed for `trading-plan check`
and for the coach to learn which setups work). With `--entry` it is
a limit order unless `--type market|stop` says otherwise.

Pip values for pairs not quoted in the account currency need a live price
(MT5 or Yahoo). With neither (e.g. phone only, offline), pass the prices the
user sees in the app: `TADAWAL_PRICES="USDJPY=156.39,GBPUSD=1.2316" python3 ...`.
A screenshot of the MT5 quotes screen is enough: read the bid prices from it.

## The limits (trader/config.json)

| key | default | meaning |
| --- | --- | --- |
| `risk_per_trade_pct` | 1.0 | max loss if the stop is hit, % of balance |
| `max_total_risk_pct` | 5.0 | all open stops together |
| `max_leverage` | 10 | total notional / balance after the order |
| `max_open_positions` | 5 | |
| `max_daily_loss_pct` | 3.0 | after this realized loss, everything is BLOCKed until 17:00 New York |
| `max_same_currency` | 2 | warning when more trades push one currency the same way |
| `min_rr` | 1.5 | minimum reward:risk when a target is set |
| `blocked_symbols` | [] | |
| `allow_short` | true | selling a pair is normal in forex |

Only change limits when the user explicitly asks; show the new values. Never
loosen a limit to make a blocked order pass: explain the rule and offer a
fix (smaller lot, closer stop, further target, skip the trade).

## Workflow

1. Entry and stop from `entry-timer` / `exit-planner` or the user. No stop =
   no size: ask for one or propose one.
2. `size ... --write`. Read the notes (leverage cap, stop too wide for the
   account, R:R below minimum).
3. `check`. Explain every BLOCK in plain words. Warnings (no target,
   correlated exposure, stop under 5 pips, forex closed) do not block.
4. Then `order-executor`.

Any edit to an order voids its PASS; a PASS also expires after 60 minutes.

## Answer format

In the user's language, e.g.: "اللوت: 0.33. الستوب 30 نقطة = 99$ (0.99٪ من
الحساب). قيمة النقطة 10$ للوت. العائد/المخاطرة 2. الفحص: PASS ✅".
For beginners, also say it in micro lots (0.33 lot = 33 micro lots) if helpful.
