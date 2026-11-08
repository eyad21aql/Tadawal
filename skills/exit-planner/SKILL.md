---
name: exit-planner
description: >-
  Exit Planner for forex and gold. Plans how a trade ends before it starts and
  manages it while it runs: stop-loss beyond the last swing or by ATR (kept
  between 1 and 3 ATR), a take-profit just in front of the next level (or at
  2R), 1R/2R/3R targets in price and pips, a scale-out plan (part at 1R and
  stop to breakeven, part at 2R, trail the rest with an ATR chandelier stop),
  a time stop, and warnings when a level blocks the move. `trail` and `review`
  say when to tighten the stop on open trades; stops only move in the trade's
  favour. Use when the user asks "where do I put my stop", "take profit",
  "when should I close", "exit strategy", "trail my stop", "move to
  breakeven", "وين أحط الستوب", "وقف الخسارة", "التيك بروفت", "الهدف", "متى
  أسكر الصفقة", "وقف متحرك", "أنقل الستوب للدخول".
---

# Exit Planner

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/exit.py plan EURUSD --entry 1.0850                    # stop + targets from the chart
python3 SKILL/exit.py plan EURUSD --entry 1.0850 --stop 1.0820      # targets for a given stop
python3 SKILL/exit.py plan XAUUSD --entry 2650 --side sell --tf H4
python3 SKILL/exit.py plan EURUSD --entry 1.0850 --order o1         # write SL/TP into order o1
python3 SKILL/exit.py trail EURUSD --entry 1.0850 --stop 1.0820 --since 2026-11-05
python3 SKILL/exit.py review                                        # every open trade
```

Use the same timeframe the entry was taken on (`--tf M15|H1|H4|D1`). `--csv`
for exported bars, `--json` for machine-readable output. Plans are saved in
`trader/exits.json`.

## Rules the plan follows

- Stop: 0.5 ATR beyond the last swing (within 60 bars); closer than 1 ATR ->
  1 ATR; further than 3 ATR -> 3 ATR; no swing -> 2 ATR. A user-given stop is
  used as is. Remind the user the stop must also clear the spread (on exotic
  or gold, add a few pips).
- One order carries one take-profit: `tp_single` sits just in front of the
  first level at least `min_rr` R away, otherwise 2R.
- `trail`: breakeven after 1R, chandelier (best price minus 3 ATR) after 1.5R,
  lock 1R after 2R. The best candidate wins; a stop is never loosened.
- Time stop: no 1R within N bars (default 10) -> reassess or close.
- Before the weekend and before high-impact news, suggest tightening or
  taking partial profit on open trades.

## After planning

- With `--order`, the order changed: run `risk-manager check` again (and
  re-size with `risk.py size` if the stop moved).
- To tighten a stop on an open trade: `order-executor modify <id> --sl <new>`
  after the user says yes (in manual mode they change it in the app).

## Answer format

In the user's language: stop (price, pips, why), take-profit (price, pips,
why), R:R, the scale-out steps, any level in the way. Example:
"الستوب 1.0817 (٣٣ نقطة، تحت آخر قاع). الهدف 1.0899 (٤٩ نقطة) قبل مقاومة 1.0900. العائد/المخاطرة 1.5."
