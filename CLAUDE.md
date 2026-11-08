# CLAUDE.md: project memory for Tadawal (read this first)

This file is the hand-off between conversations. The previous conversation got
too long and was cleared. Everything that was built, decided, noticed and
planned is below. Keep this file up to date when you change something
important (append to "Log" and fix "Next steps").

---

## 1. The user and how to talk to them

- Writes **Arabic** (Yemeni / Gulf dialect: "اشتي", "ايش", "ياغالي"). **Always answer
  in Arabic**, plain and friendly, short paragraphs, tables when comparing.
  Code, commands, file names stay in English.
- Not a programmer and new to systematic trading. Explain terms simply.
  The user once asked "what do you mean by scalping?": don't assume jargon.
- Trades **forex and gold**: **EURUSD and XAUUSD** (gold), sessions **London and New York**.
- Uses **MetaTrader 5 on an iPhone only** for now (sent a screenshot of the
  Arabic MT5 quotes screen). No Windows PC confirmed yet.
- Local time zone assumed **Asia/Riyadh** (config default). Confirm if relevant.
- **Religious hard requirement: no overnight positions** ("التبييت حرام"): no
  trade may be held through the 17:00 New York rollover (swap/riba). This is
  implemented as a hard, non-overridable rule (see §5). Never weaken it.
- Holding time: scalping or intraday is fine. "كل الي يهمنا انه مافيش تبييت".
- **Wants Claude to do everything**: analysis, risk, entries/exits, and to
  judge what is right or wrong. It does not want to take screenshots or place
  trades by hand. That is why the **auto-trader robot** was built (§4).
- Brokers the user has: **FBS** and **Inzo**. Recommendation given: **FBS over
  Inzo** (Inzo: offshore Seychelles/Comoros licences, unlicensed SVG entity,
  2026 withdrawal complaints and scam warnings, WikiFX ~2.3). FBS: since 2009,
  group has CySEC/ASIC entities, but regional clients are likely under
  **FBS Markets Inc. (Belize FSC)**, offshore with weaker protection; has MT5 and
  a swap-free Islamic account; free VPS not confirmed. Suggested **Exness** as
  a comparison (popular in the region, MT5, swap-free often automatic,
  free VPS claimed for active traders, not verified). Advice: open MT5 **demo**,
  compare gold/EURUSD spreads at the London/NY overlap, test a small deposit and
  withdrawal before funding. The user has **not decided yet**.

## 2. Repository and git

- Repo: `eyad21aql/Tadawal` (GitHub). Work branch: **`claude/trader-skill-pack-9gms29`**.
  Develop and push only there (`git push -u origin claude/trader-skill-pack-9gms29`).
- **No pull request exists; nothing is merged to `main`.** Do not open a PR unless asked.
  The plugin install command in README (`/plugin marketplace add eyad21aql/Tadawal`)
  only works after merging to main; until then use
  `/plugin marketplace add eyad21aql/Tadawal@claude/trader-skill-pack-9gms29`.
- Commit messages end with the Co-Authored-By / Claude-Session lines given by
  the system at the time. Never put a model name in commits or files.
- Commit history (newest first):
  - (latest) CLAUDE.md + fix Sunday-wait bug + clock-independent tests
  - `54cb1f5` auto-trader robot
  - `fe353bd` hard no-overnight rule + per-pair scalping stops
  - `ccaa02b` trading-plan, currency-strength, market-structure, trade-coach
  - `5ab8b87` retarget from US stocks to forex + gold
  - `a64460c` first version (US stocks) — superseded
  - `8f462b4` / `b7ae456` installed and then removed a third-party pack (see Log)

## 3. What exists (16 skills, standard-library Python only)

```
.claude-plugin/plugin.json, marketplace.json   Claude Code plugin "tadawal" v1.2.0
skills/_lib/tadawal.py     shared library (data, pips, sessions, specs, ledger, no-overnight)
skills/<skill>/SKILL.md    instructions (frontmatter: name + description with Arabic triggers)
skills/<skill>/<tool>.py   the script for the skill
tests/                     offline unittest suite (synth.py makes synthetic bars), fixtures/
tools/build_zips.py        one ZIP per skill (with _lib copied in) for claude.ai upload
tools/windows/             start_robot.bat (auto-restart loop) + SETUP-AR.md (Arabic VPS guide)
config.example.json        copy to trader/config.json (mode manual, no_overnight true)
README.md                  Arabic user guide
trader/                    USER DATA, git-ignored (config, orders, journal, plan, robot state…)
```

| # | skill folder | script | purpose |
|---|---|---|---|
| 1 | entry-timer | timer.py | is now a good entry? checklist score buy/sell, sessions, rollover, news warnings, pullback/breakout plans in pips |
| 2 | news-monitor | news.py | Yahoo RSS per pair (EURUSD=X) + Fed/ECB/BoE press feeds, dedupe, upcoming high-impact events |
| 3 | chart-scanner | scan.py | candles, double top/bottom, H&S, triangles/wedges/channels, breakouts, RSI divergence, gaps, SVG chart |
| 4 | risk-manager | risk.py | lot size from stop (pips, pip value incl. JPY/crosses/gold), PASS/BLOCK gate, leverage cap, correlation warning, `--setup` |
| 5 | time-machine | backtest.py | rules-engine backtester: next-open fills, stop-first, spread pips, lots, conv to USD, walk-forward, grid, `--no-overnight`, `--sessions` |
| 6 | exit-planner | exit.py | stop/TP from structure/ATR, scale-out, chandelier trail, review open trades |
| 7 | price-alerts | alerts.py | above/below/cross/pct/session alerts, auto rings 30/10 min before flat time |
| 8 | order-executor | order.py | modes paper / manual (phone app steps + `filled`) / mt5; ticket hash + fresh risk PASS + user's yes; plan gate; `flat` |
| 9 | budget-manager | budget.py | plan table of lots by stop, status (leverage/margin), journal, report, MT5 sync |
| 10 | cot-tracker | cot.py | CFTC legacy COT for EUR GBP JPY CHF CAD AUD NZD MXN, USD index, gold: net, change, COT index, pair view |
| 11 | economic-analyst | econ.py | weekly calendar (nfs.faireconomy.media ff_calendar_thisweek.json), surprises, `actual` recording, pair bias |
| 12 | trading-plan | plan.py | written rules (templates scalp/day/swing/smc), per-pair stop ranges, checklist, IN PLAN / OUT / NEEDS CHECKLIST, `today` |
| 13 | currency-strength | strength.py | 8 majors from 28 pairs (moves in ATRs), strong-vs-weak pairs, correlation matrix, overlap of open trades |
| 14 | market-structure | smc.py | SMC: swings, BOS/CHoCH on closes, order blocks, FVGs, equal highs/lows + sweeps, premium/discount/OTE, setup A/B |
| 15 | trade-coach | coach.py | review by setup/session/pair/emotion; detects revenge, upsizing, overtrading, cut winners, big losses, no stop, holding losers, news trades, plan overrides, overnight holds; `tag` |
| 16 | auto-trader | robot.py | **the robot** (§4) |

Replaced during the work (do not resurrect unless asked): earnings-analyst
(SEC) → economic-analyst; pelosi-tracker (House PTRs) → cot-tracker.

### Conventions
- Scripts run from the user's project folder; data in `./trader/` (override `TADAWAL_DIR`).
- Each script inserts `_HERE/_lib` and `_HERE/../_lib` on sys.path, so it works in
  the plugin layout and inside a standalone ZIP.
- `trader/config.json` keys (defaults in `_lib/tadawal.py DEFAULT_CONFIG`): mode
  (paper|manual|mt5), allow_live, data_source, timezone, symbol_suffix, symbol_map,
  account_balance, currency, contract_size, leverage, risk_per_trade_pct (1.0),
  max_total_risk_pct (5), max_open_positions, max_daily_loss_pct (3),
  max_leverage (10), max_same_currency (2), max_spread_pips, max_spread_by_symbol,
  **no_overnight**, last_entry_ny ("16:00"), flat_by_ny ("16:45"), min_rr, blocked_symbols,
  allow_short (true), mt5 {path, login, server} (password only via env MT5_PASSWORD), magic.
- Pips: 0.0001 normal, 0.01 JPY quote, **gold 0.1 (1 pip = $0.10, $10 per pip per lot)**, silver 0.01.
- Prices offline / from a screenshot: env `TADAWAL_PRICES="EURUSD=1.0393,USDJPY=156.4"`.
- Data sources: MT5 desktop (Windows only) → Yahoo chart JSON → `--csv` (MT5 export format supported).
- The forex trading day and daily-loss reset at 17:00 New York.

## 4. The auto-trader robot (skills/auto-trader/robot.py)

Design agreed with the user: **the robot executes, Claude supervises** (daily
bias + weekly review). Runs on a **Windows VPS/PC with MT5 desktop**, which
the phone app cannot replace; the user follows trades in the phone app and on
Telegram.
- Strategies: `trend_pullback` (default), `breakout`, `ema_cross`, or custom rules JSON
  (same format as time-machine). Signals on **closed** candles only (forming candle dropped).
  Each candle evaluated once (`state.last_bar`).
- Gates: enabled flag, forex open, **can_open (no overnight)**, sessions, max trades/day,
  max losses/day, weekly loss %, max open, Claude's bias per pair (`robot.py bias`,
  expires after `bias_hours`=24 → "both"), news pause (calendar.json, 30 min before / 15 after),
  one trade per pair.
- Sizing: stop = sl_atr×ATR, TP = tp_r×stop, risk_pct of balance (default 0.5%, must be ≤
  config risk_per_trade_pct), then `risk.check_orders` must PASS.
- Management: breakeven at +1R (entry + 1 pip), optional ATR trail; **closes everything at
  flat_by_ny and any position that crossed a rollover**; `flat_requested` closes all and pauses.
- REAL MT5 accounts refused unless config `allow_live` AND robot.json `live` are both true.
  **Never set these for the user.**
- Telegram: token only via env `TELEGRAM_BOT_TOKEN`; commands only from `telegram_chat_id`:
  /status /stop /start /flat (Arabic: الحالة، وقف، تشغيل، سكر الكل).
- Files: trader/robot.json, robot_state.json (heartbeat), robot.log, robot.lock.
- `robot.py backtest` replays the exact rules with sessions, spread and no-overnight.
  On random synthetic data the presets lose (expected: no edge). **They have NOT been
  validated on real EURUSD/XAUUSD data.** Do not let the user go live without a real
  backtest (~100+ trades, PF > ~1.2 after spread) and ≥4 weeks of demo.
- Claude on the VPS: install Claude Code there and run `claude remote-control` in
  `C:\Tadawal`, then talk to it from the Claude app on the phone. A cloud session
  (like the previous one) cannot reach the user's VPS/MT5.

## 5. Hard rules and safety design (do not weaken)

1. **No overnight** (`no_overnight: true`): risk BLOCKs and executor refuses new trades
   after last_entry_ny (16:00 NY), cannot be overridden; positions closed by flat_by_ny
   (16:45 NY); paper ledger auto-closes; manual mode shouts CLOSE NOW; MT5 pending
   orders get an expiry; alerts ring at 30/10/0 min; coach flags any rollover crossing;
   robot always applies it. Riyadh times: winter last entry 00:00, flat 00:45, rollover
   01:00; summer one hour earlier (handled automatically by zoneinfo).
2. Orders: placed only with a fresh (<60 min) risk PASS for the exact contents (order
   hash), matching ticket code, the user's own affirmative words in `--confirm`
   (Arabic and English; any "لا / no / wait / انتظر / لحظة" refuses). One ticket per yes.
   Never type the confirmation yourself.
3. Trading plan with `enforce`: out-of-plan orders need `--override-plan "<user's reason>"`,
   logged as a rule break. Never override on your own.
4. Stops only tighten. Daily loss limit blocks everything until 17:00 NY.
5. Never set `allow_live`, `live`, or change `mode` for the user; show the line, they edit.
6. Say "not financial advice" once when giving buy/sell views. Never promise profit.

## 6. Environment facts and lessons

- The previous cloud session **could not reach** SEC, Yahoo, House Clerk, CFTC
  (publicreporting.cftc.gov), nfs.faireconomy.media, Fed/ECB/BoE feeds (proxy 403).
  So **all data parsers were tested only on fixtures built from known formats**.
  Unverified live: COT Socrata field names (`noncomm_positions_long_all`, …), whether the
  FF weekly feed includes `actual`, Yahoo forex symbols (EURUSD=X; gold uses GC=F futures),
  Fed/ECB/BoE RSS URLs, Telegram API calls, MT5 real behaviour (filling modes, pending
  expiry via server-time offset, symbol names at FBS/Exness).
- Tests: `python3 -m unittest discover -s tests` (93 tests, ~0.3 s). Lint:
  `python3 -m pyflakes skills tests tools` (pip install pyflakes if missing).
- **Lesson: tests must not depend on the real clock.** A test failed on a Sunday because
  plan.py read `T.ny_now()`. Patch `T.ny_now`, `T.market_clock`, `T.overnight_status`
  in tests. A helper used during checking: run the suite with a faked `T.ny_now` at
  several times (weekday 09:00, 16:30, 16:55, Friday 18:00, Sunday 10:00/17:10, Dec 25).
- `T._MT5_READY` caches the MT5 module; tests set it to False or to a `FakeMT5`
  (tests/test_skills.py). `T._PRICE_OVERRIDE` holds offline prices.
- Bug fixed in this hand-off: plan.py "Sunday open wait" fired all day Sunday (now only
  17:00–18:00 NY Sunday).

## 7. Next steps (pending, in order)

Waiting on the user (ask in Arabic, a few questions at a time):
1. Broker decision (FBS recommended; optionally compare with Exness demo).
2. A screenshot of the MT5 quotes screen with gold and EURUSD → set `symbol_suffix` /
   `symbol_map` (gold may be XAUUSD, GOLD, XAUUSDm…).
3. Account balance and risk per trade (suggested 0.5–1%).
4. Entry style for the plan (if none: offer simple setups, test them first).
5. Whether they will rent a Windows VPS (or broker's free VPS) for the robot.

Then:
6. Help set up the VPS following `tools/windows/SETUP-AR.md`; MT5 demo; `pip install MetaTrader5`;
   `trader/config.json` with mode mt5; `order.py account` shows demo.
7. Export months of real M15 history from MT5 for EURUSD and XAUUSD, run
   `robot.py backtest` / time-machine with `--csv`; pick or adapt the strategy honestly.
8. Run the robot on demo ≥4 weeks; daily: `robot.py status`, econ calendar, set bias;
   weekly: `trade-coach review --days 7`.
9. Verify the unverified data sources on the VPS (they have internet there) and fix parsers.
10. Suggested (discussed, not agreed) for day trading: `last_entry_ny "14:00"` (22:00 Riyadh)
    so trades have time before the 16:45 flat. Currently 16:00 NY.
11. Ideas offered earlier, not built: daily briefing aggregator, ADR / Asian-range session
    analysis, risk sentiment (yields, DXY, VIX), prop-firm guard, swap calculator.
12. When the user is happy: offer to merge the branch to `main` (via PR only if asked).

## 8. Log (what happened, oldest first)

1. User asked what "Trader – A Claude skill pack" is; explained skills/skill packs.
2. User showed an image of 11 skill names (Entry Timer, News Monitor, Chart Scanner,
   Risk Manager, Time Machine, Exit Planner, Price Alerts, Order Executor, Budget Manager,
   Pelosi Tracker, Earnings Analyst); explained each.
3. User pasted `github.com/Jakeschincariol/trader-skill` "install skill": reviewed it (MIT,
   public data only, 175 tests passed), installed into `.claude/skills/`, pushed. Its skills
   differed from the image (copycat, screener, tax, rebalance; broker = Alpaca/IBKR, no MT5).
   User said "delete them" → removed (commit 8f462b4).
4. Built our own 11 skills for US stocks + MT5 (a64460c).
5. User clarified: **currency pairs, not stocks** → retargeted everything to forex/gold
   (5ab8b87); Earnings→economic-analyst, Pelosi→COT; added manual mode for the phone.
6. As "expert", listed missing skills; user asked to build the top 4 → trading-plan,
   currency-strength, market-structure, trade-coach (ccaa02b).
7. User: scalping on gold + EURUSD, London/NY, **no overnight** → hard rule + per-pair
   stops (fe353bd). Explained scalping vs day trading; recommended intraday with no overnight.
8. User wants full automation → explained VPS + MT5 desktop requirement, robot executes /
   Claude supervises, demo first; user said build it → auto-trader (54cb1f5).
9. User asked FBS vs Inzo → researched; recommended FBS, avoid Inzo, Exness as comparison.
10. User asked for this CLAUDE.md before `/clear`; also fixed the Sunday-wait bug and made
    tests clock-independent.
