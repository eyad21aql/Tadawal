---
name: exit-planner
description: >-
  Exit Planner. Plans how a trade ends before it starts and manages it while
  it runs: stop-loss from the last swing low/high or ATR (kept between 1 and 3
  ATR), a single take-profit placed just in front of the next resistance (or
  at 2R), 1R/2R/3R targets, a scale-out plan (take a third at 1R and move to
  breakeven, a third at 2R, trail the rest with a chandelier ATR stop), a time
  stop, and warnings when a level blocks the move. `trail` and `review` say
  when to tighten the stop on open positions; stops only ever move in the
  trade's favour. Use when the user asks "where do I put my stop", "take
  profit", "when should I sell", "exit strategy", "trail my stop", "move stop
  to breakeven", "وين أحط الستوب", "وقف الخسارة", "جني الأرباح", "متى أطلع من
  الصفقة", "الهدف", "وقف متحرك".
---

# Exit Planner

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/exit.py plan AAPL --entry 190                 # stop + targets from the chart
python3 SKILL/exit.py plan AAPL --entry 190 --stop 184      # targets for a given stop
python3 SKILL/exit.py plan TSLA --entry 250 --side sell     # short
python3 SKILL/exit.py plan AAPL --entry 190 --order o1      # write sl/tp into order o1
python3 SKILL/exit.py trail AAPL --entry 190 --stop 184 --since 2026-10-01
python3 SKILL/exit.py review                                # every open position
```

Add `--tf H1` etc. for intraday trades, `--csv` for exported bars, `--json`
for machine-readable output. Plans are saved in `trader/exits.json`.

## Rules the plan follows

- Stop: 0.5 ATR beyond the last swing (within 60 bars); if that is closer
  than 1 ATR it uses 1 ATR, if further than 3 ATR it caps at 3 ATR; no swing
  -> 2 ATR. A user-given stop is used as is.
- One order can carry one take-profit: `tp_single` is just in front of the
  first level at least `min_rr` R away, otherwise 2R.
- Trailing (`trail`): breakeven after 1R, chandelier (highest high minus
  3 ATR) after 1.5R, lock 1R after 2R. The highest candidate wins; a stop is
  never loosened.
- Time stop: no 1R within N bars (default 10) -> reassess.

## After planning

- If `--order` was used, the order changed: run `risk-manager check` again
  (the volume may need re-sizing with `risk.py size` for the new stop).
- For an open position, a tighter stop is applied with
  `order-executor modify <position> --sl <new>` after the user says yes.

## Answer format

In the user's language: stop (and why), take-profit (and why), R:R, the
scale-out steps as a short list, and any level in the way. Example:
"الستوب 184.1 (تحت آخر قاع بنصف ATR). الهدف 199.6 قبل مقاومة 200. العائد/المخاطرة 1.6."
