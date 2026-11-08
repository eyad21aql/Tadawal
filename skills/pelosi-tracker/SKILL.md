---
name: pelosi-tracker
description: >-
  Pelosi Tracker. Follows the stock trades that members of the US Congress
  must disclose under the STOCK Act. Reads the House Clerk's public index of
  Periodic Transaction Reports (PTRs), shows new filings since the last check
  (Nancy Pelosi or any member), downloads each PTR and turns it into rows: who
  (self/spouse/joint), stock and ticker, buy/sell, options with strike and
  expiry, trade date, amount range, days taken to disclose; flags trades in
  stocks the user holds or watches. Senate trades via a manual route. Use when
  the user says "what is Pelosi buying", "Pelosi trades", "Congress stock
  trades", "did any congressman buy NVDA", "alert me when Pelosi files",
  "صفقات بيلوسي", "وش اشترت بيلوسي", "صفقات الكونغرس", "تداولات أعضاء
  الكونغرس".
---

# Pelosi Tracker

## Run

From the user's project folder (`SKILL` = this skill's folder; `python` on Windows):

```bash
python3 SKILL/ptr.py index --member pelosi                 # her PTRs this year
python3 SKILL/ptr.py index --member pelosi --fetch --limit 3   # with the trades inside
python3 SKILL/ptr.py index --year 2025 --member pelosi
python3 SKILL/ptr.py new                                   # any member, since last run
python3 SKILL/ptr.py new --member pelosi --member khanna --fetch
python3 SKILL/ptr.py fetch 20033725                        # one filing by DocID
python3 SKILL/ptr.py parse report.pdf                      # a PDF/text you already have
```

Turning PDFs into rows needs `pdftotext` (poppler). Windows:
`winget install oschwartz10612.Poppler` or `conda install poppler`;
macOS: `brew install poppler`. Without it the filing list and PDF links
still work, and you can read a PDF the user downloads.

The first `new` run shows the latest filings and remembers all of them;
later runs show only new ones. For an ongoing alert, run `new --member
pelosi --fetch` on a schedule (`/loop 6h ...`, Windows Task Scheduler).

## Senate

Senate PTRs are at efdsearch.senate.gov behind a usage agreement page and are
not fetched automatically. If the user asks about a senator, search the web
for recent reports, or guide them to the site (Search -> Periodic
Transactions -> senator's name) and read the report they open or paste.

## Say these things every time

- Filings come up to **45 days** after the trade (the script shows the lag;
  over 45 days is late). This is not a live feed: price may have moved a lot.
- Amounts are ranges, not exact figures.
- Many trades belong to the spouse (Paul Pelosi for Nancy Pelosi), and
  options (calls) are a large part of them: copying calls is much riskier
  than buying shares.
- It is public information. Copying it is a strategy with delays and no
  guarantee; offer `time-machine` to test it, and `risk-manager` to size it.

## Answer format

In the user's language. Per filing: date filed, member, then a short table
(buy/sell, ticker, amount range, trade date, option details). Highlight
trades in stocks the user holds. Note: Nancy Pelosi did not run for
re-election in 2026; she keeps filing PTRs while she is a member (her term
ends in January 2027). The tracker works for any House member.
