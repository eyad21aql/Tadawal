---
name: market-structure
description: >-
  Market Structure and Smart Money Concepts (SMC/ICT) for forex and gold.
  Finds swing highs and lows (HH HL LH LL), BOS and CHoCH on candle closes,
  order blocks (mitigated / unmitigated), fair value gaps (FVG, imbalances),
  liquidity (equal highs/lows, buy-side and sell-side) and liquidity sweeps
  (stop hunts), premium/discount and OTE (62-79%) of the dealing range, with
  higher-timeframe bias, then proposes an SMC setup (entry at the order block
  or FVG, stop beyond it, target at the opposite liquidity, R:R, quality A/B).
  Reads chart screenshots too. Use when the user says "SMC", "order block",
  "FVG", "liquidity", "BOS", "CHoCH", "market structure", "smart money",
  "اسمارت موني", "الأوردر بلوك", "الفجوة السعرية", "السيولة", "كسر الهيكل",
  "تغيير الاتجاه", "منطقة الخصم والعلاوة", "هيكل السوق".
---

# Market Structure (SMC)

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/smc.py EURUSD                     # M15 entries, H4 bias
python3 SKILL/smc.py XAUUSD --tf H1 --htf D1
python3 SKILL/smc.py GBPUSD --tf M5 --htf H1    # scalping
python3 SKILL/smc.py EURUSD --swing 5           # only bigger swings
python3 SKILL/smc.py EURUSD --tf M15 --csv eu_m15.csv --htf-csv eu_h4.csv
```

Definitions used (state them if the user's school differs):
- swing = highest/lowest candle with `--swing` candles on each side (default 3).
- BOS = candle CLOSE beyond the last swing in the trend direction; CHoCH =
  the first close beyond a swing against the trend. Wicks do not count.
- order block = the last opposite-colour candle before the move that broke
  structure (its full high-low range). Mitigated = price came back into it;
  invalidated = price closed through it.
- FVG = 3-candle gap between candle 1's high and candle 3's low (bullish) or
  the reverse; at least 0.1 ATR. Open / partly filled shown; filled dropped.
- liquidity = 2+ swing highs (or lows) within 0.1 ATR, not yet taken.
  Sweep = wick through a level and close back inside, within the last 10 candles.
- premium/discount = above/below 50% of the latest swing low to swing high.

## The setup logic

Buy only when the bias (higher timeframe, or entry timeframe if none) is up,
the entry timeframe agrees, price is in discount, and there is a fresh
bullish order block or FVG below price; entry at its top, stop below it,
target the nearest buy-side liquidity/range high giving at least `--min-rr`.
Quality A = sweep + CHoCH + zone (the classic), B+ = sweep + zone, B = zone in
trend. Mirror for sells. Otherwise it says why there is no setup: that is a
valid answer.

## With screenshots

If the user sends an MT5 chart, mark the structure you see (last BOS/CHoCH,
obvious OB/FVG, equal highs/lows) and compare with the script on the same
pair and timeframe. Be honest when the screenshot is too zoomed out to tell.

## Answer format

In the user's language with SMC terms they use (أوردر بلوك، FVG، سيولة،
BOS/CHoCH). Bias first, then the zone and the liquidity target, then the
setup (or "no setup: wait for…"). Then hand the setup to `risk-manager size
... --setup <plan setup> --write` and `trading-plan check`.
Example: "الهيكل على 4 ساعات صاعد، وعلى 15 دقيقة صار CHoCH صاعد بعد سحب سيولة تحت
1.0840. السعر في منطقة الخصم. الدخول شراء limit عند أوردر بلوك 1.0852، والستوب 1.0838
(14 نقطة)، والهدف سيولة القمم المتساوية 1.0905 (العائد للمخاطرة 3.8، جودة A)."
