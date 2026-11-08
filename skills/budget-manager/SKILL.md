---
name: budget-manager
description: >-
  Budget Manager. Plans how trading capital is split (cash reserve, number of
  positions, budget per position, risk per trade, worst case if every stop
  hits, daily loss limit), shows where the money is now (cash, invested,
  share of each position, loss room left today), keeps a trade journal (paper
  fills automatically, MetaTrader 5 history via sync, manual entries) and
  reports real performance: net P&L, win rate, payoff, profit factor,
  expectancy, average R, max drawdown, losing streaks, by symbol or month. Use
  when the user says "I have $10,000, how do I split it", "my budget", "how am
  I doing", "my P&L", "win rate", "trading journal", "how much cash do I have",
  "ميزانيتي", "كيف أوزع رأس المال", "أرباحي وخسائري", "سجل الصفقات", "أداء
  التداول", "كم باقي كاش".
---

# Budget Manager

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/budget.py plan --capital 10000 --positions 5 --reserve 20 --prices AAPL=190 MSFT=410
python3 SKILL/budget.py status
python3 SKILL/budget.py report                     # all time
python3 SKILL/budget.py report --from 2026-10-01 --by symbol
python3 SKILL/budget.py report --by month
python3 SKILL/budget.py journal --last 20
python3 SKILL/budget.py journal-add AAPL buy 10 190 --exit 198 --sl 185 --opened 2026-10-01 --closed 2026-10-08
python3 SKILL/budget.py sync --days 90             # MT5 closed trades -> journal
python3 SKILL/budget.py deposit 500 | withdraw 200 # paper account only
```

## Setting up a plan with the user

Ask (only what is missing): total capital, how many positions at once,
cash to keep aside. Then run `plan` and save their choices in
`trader/config.json` (`account_balance` for paper, `risk_per_trade_pct`,
`max_open_positions`, `max_position_pct`, `max_daily_loss_pct`), after showing
the values and getting their OK. Sensible defaults for beginners: 1% risk per
trade, 5 positions, 20% cash reserve, 3% daily loss limit.

## Reading the report

- Profit factor < 1 or negative expectancy: the method loses money so far.
- Payoff (avg win / avg loss) and win rate go together: a 35% win rate is
  fine with a payoff above 2.
- Fewer than 20 closed trades: too early to judge.
- Look at `by symbol` for one stock that causes most of the losses.

## Answer format

In the user's language, numbers with currency, short. For a plan, a small
table (reserve / per position / risk per trade / worst case). For a report,
3-4 lines and one concrete suggestion.
