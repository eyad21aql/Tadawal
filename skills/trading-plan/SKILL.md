---
name: trading-plan
description: >-
  Trading Plan and discipline guard for forex and gold. Builds the user's
  written plan (style scalp/day/swing/SMC, pairs, sessions, timeframes, named
  setups, stop range in pips, minimum reward:risk, max trades a day, stop
  after N losses in a row, weekly loss limit, news blackout, Friday cutoff,
  a pre-trade checklist) and then checks every order against it: IN PLAN /
  OUT OF PLAN / NEEDS CHECKLIST. The order executor refuses out-of-plan
  orders unless the user gives a reason, which is logged as a rule break.
  Also answers "can I trade now?". Use when the user says "make me a trading
  plan", "my rules", "should I take this trade", "can I trade now", "I keep
  overtrading", "خطة تداول", "قواعدي", "هل الصفقة من خطتي", "أقدر أتداول
  الحين؟", "ساعدني ألتزم", "أكثر من الصفقات".
---

# Trading Plan

Most losing traders do not lack analysis; they break their own rules. This
skill makes the rules explicit and checks them before every trade.

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/plan.py init --style day                # scalp | day | swing | smc
python3 SKILL/plan.py show
python3 SKILL/plan.py set pairs EURUSD GBPUSD XAUUSD
python3 SKILL/plan.py set sessions london newyork     # or: any
python3 SKILL/plan.py set stop_pips 15 40
python3 SKILL/plan.py set min_rr 2
python3 SKILL/plan.py set max_trades_per_day 3
python3 SKILL/plan.py set max_losses_in_a_row 2
python3 SKILL/plan.py set news_blackout_minutes 30 15
python3 SKILL/plan.py setup add "asian-breakout" "London breaks the Asian range, retest, M15 close"
python3 SKILL/plan.py today                           # can I trade now?
python3 SKILL/plan.py check o1                        # after risk.py size ... --setup NAME --write
python3 SKILL/plan.py check o1 --checklist yes,yes,yes,yes,yes
```

## Building the plan with the user

Do it as a short interview, one or two questions at a time, in the user's
language: style and timeframes, pairs (fewer is better: 2-4), sessions they
can actually watch (their local time), the setups they really trade (make
each one concrete: what must be true, where the stop goes), limits. Start
from the closest template, then `set` what differs. Read the final plan back
and get their OK. Suggest printing it or keeping a screenshot on the phone.

## Before every trade

1. The order must name its setup: `risk.py size ... --setup trend-pullback --write`.
2. `plan.py check o1`. If NEEDS CHECKLIST, ask the checklist questions one by
   one (translate them) and pass the answers in order. Do not answer for the
   user. Any "no" = OUT OF PLAN.
3. OUT OF PLAN: explain the broken rule and recommend skipping. If the user
   insists, they must give a reason; the executor then needs
   `--override-plan "<their reason>"` and the trade is marked as a rule break
   for the coach. Never add an override on your own.
4. Verdicts expire after 60 minutes and any change to the order voids them.

`plan.py today` before a session: trades left, losing streak, weekly loss,
session, news blackouts. If it says STOP, say so clearly and kindly: the
best trade after two losses is often no trade.

## Answer format

In the user's language. Verdict first (✅ ضمن الخطة / ❌ خارج الخطة), then the
reasons in one line each, then what would make it valid (wait for London,
wait 15 minutes after CPI, tighter stop...).
