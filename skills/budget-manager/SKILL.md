---
name: budget-manager
description: >-
  Budget Manager for forex and gold. Plans how a trading account is used:
  risk per trade in money, how many trades at once, worst case if all stops
  hit, daily loss limit, what a 10-trade losing streak would cost, and a lot
  size table by pair and stop distance. Shows where the account is now
  (equity, notional, leverage used, margin, loss room left today), keeps a
  trade journal (paper/manual fills automatically, MT5 history via sync,
  manual entries from the app) and reports real performance: net P&L, win
  rate, payoff, profit factor, expectancy, average R, pips, max drawdown,
  losing streaks, by pair or month. Use when the user says "I have $1000, how
  do I trade it", "my budget", "how am I doing", "my P&L", "win rate",
  "trading journal", "ميزانيتي", "كيف أوزع حسابي", "أرباحي وخسائري", "سجل
  الصفقات", "أداء التداول", "كم أفتح لوت بحسابي".
---

# Budget Manager

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/budget.py plan --capital 1000 --positions 3 --pairs EURUSD GBPJPY XAUUSD
python3 SKILL/budget.py plan --capital 5000 --risk-pct 0.5 --stops 10 20 30 50
python3 SKILL/budget.py status
python3 SKILL/budget.py report
python3 SKILL/budget.py report --from 2026-11-01 --by symbol
python3 SKILL/budget.py report --by month
python3 SKILL/budget.py journal --last 20
python3 SKILL/budget.py journal-add EURUSD buy 0.3 1.0850 --exit 1.0910 --sl 1.0820 --closed 2026-11-03
python3 SKILL/budget.py journal-add XAUUSD sell 0.05 2650 --exit 2662 --pnl -60   # P&L as shown in the app
python3 SKILL/budget.py sync --days 90          # MT5 desktop: closed trades -> journal
python3 SKILL/budget.py deposit 500 | withdraw 200   # paper/manual ledger
```

The lot table needs prices for pairs not quoted in the account currency
(MT5, Yahoo, or `TADAWAL_PRICES="USDJPY=156.39"` from a quotes screenshot).

## Setting up a plan with the user

Ask only what is missing: account size, account currency, how many trades
at once, how much they can accept losing per trade. Run `plan`, then save
their choices in `trader/config.json` (`account_balance`, `currency`,
`risk_per_trade_pct`, `max_open_positions`, `max_daily_loss_pct`,
`max_leverage`) after showing the values and getting an OK.

Sensible defaults for beginners: 0.5-1% risk per trade, max 3 trades, 3%
daily loss limit, leverage used under 10x. Small accounts: explain micro
lots (0.01 = $0.10 per pip on EURUSD) and that 0.01 may already be more than
1% risk with a wide stop.

## Journal from the phone

Phone-only users can send a screenshot of the MT5 History tab (التاريخ):
read each closed trade (pair, buy/sell, lots, open/close price, profit) and
add it with `journal-add ... --pnl <profit shown>`. Check with the user
before adding many rows.

## Reading the report

- Profit factor < 1 or negative expectancy: losing method so far.
- Payoff (avg win / avg loss) and win rate go together.
- Fewer than 20 closed trades: too early to judge.
- `--by symbol` finds the pair that causes most losses.

## Answer format

In the user's language, numbers with currency, short. For a plan, a small
table. For a report, 3-4 lines and one concrete suggestion.
