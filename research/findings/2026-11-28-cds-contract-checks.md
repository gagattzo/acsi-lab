# One in ten "trades" on the tape is not a price, and 99 published levels rested on trades later cancelled

**Agent:** Claude (Opus 5.5), run by @gagattzo · **Status:** fixed in `harness/dtcc.py` and on aistress.si (`api/cds.js`), same rules, 28 Nov 2026

## Question
Which DTCC records should price a daily CDS level, and can one bad record flip the sign of an unsigned upfront?

## What was wrong
1. **Every record priced.** Novations (an old trade changing hands), legs of packages (priced against the other legs),
   post-priced and non-standard trades, physically settled and bond-referenced contracts, odd lots and a $1 trade all
   counted as prices. Of the 3,374 on-the-run 5-year records for our three names in the last year, 346 (10%) are not
   prices: 123 packages, 118 under $1M, 103 novations, 21 non-standard or post-priced, 4 not in USD, 2 not senior CDS.
2. **Cancellations were applied within a file only.** A trade cancelled in a later day's file stayed in the earlier
   day's level. 99 priced trades in the old history were later cancelled, among them all four CoreWeave trades behind
   the 29 Sep 2026 level (860 bp, against 701 bp the day before) and four of the five behind 16 Oct (1,018 bp; the
   surviving trades say 1,138).
3. **Late reports were dropped.** A trade reported a day late was ignored. 138 are now priced on their execution day.
4. **One $1 quote could flip CoreWeave.** DTCC upfronts are unsigned, so each upfront trade has two readings, and the
   day's quoted median chose between them. A single $1 quote at 50 bp made every CoreWeave reading take the wrong sign
   (1,373 bp read as 21 bp; headline about +10 instead of +68).

## The rules now (identical in the lab and on the site)
- Eligible records only: USD, UPI FISN "CDS Corp SN Sr", $1M notional or more, no package, non-standard or post-priced
  flag, no novation. A blank Event type (older files) is a trade: requiring `TRAD` dropped 94 of CoreWeave's 170 days.
- One ledger across files: every cancellation and correction applies whichever later file it arrives in; a late report
  counts on its execution day (weekdays only). A day's level can therefore change until the next file is in.
- Upfronts only on a 1% or 5% coupon. A reading under 10 bp (1% coupon) or 100 bp (5% coupon) is impossible, and then
  the trade decides its own sign. Otherwise the reading nearer the day's **eligible** quoted median, else the previous
  level, wins. `n_sign_from_reference` (daily) and `sign_by` (per trade) say which.

## What it changed
Levels behind the live headline are unchanged: CoreWeave 1,372.8 bp on 24 Nov (1,372.7 before; one odd lot and one
later-cancelled trade out, one late report in), Oracle 373.2 and Meta 124.2 on 25 Nov.

| | Oracle | CoreWeave | Meta |
| --- | --- | --- | --- |
| Days with a level, before → after | 261 → 259 | 170 → 162 | 176 → 173 |
| Days changed by more than 0.5 bp | 42 | 28 | 19 |
| ... by more than 10% | 3 | 1 | 5 |
| Largest change | 3 Sep 2026: 157 → 209 (a novation at 105 bp out) | 16 Oct 2026: 1,018 → 1,138 (four cancelled trades out) | 21 Sep 2026: 90 → 59 (three package trades out) |

Every lost day and every change over 10% was checked trade by trade: each comes from an odd lot, a package, a
novation, a post-priced trade, a later cancellation or a correction.

## How much still rests on the sign rule
Share of upfront trades whose two readings are both plausible, so the reference sets the sign:

| | Jan–Jun 2026 | Jul–Sep 2026 | Oct 2026 | Nov 2026 |
| --- | --- | --- | --- | --- |
| Oracle | 97–100% | 47–65% | 3% | 0% (118 of 118 read from the trade) |
| CoreWeave | 97–100% | 100% | 81% | 31% (22 of 32 read from the trade) |
| Meta | 95–100% | 100% | 100% | 100% |

At today's levels the headline does not depend on the rule: Oracle's upfronts and CoreWeave's latest ones can only be
read one way. If CoreWeave tightens below about 1,100 bp, or Oracle below about 200 bp, the reference decides again.
Meta (context only, not in the headline) always depends on it, and its first quarter of 2026 rests on the first
day's guess: Meta has no eligible quote from January to March, and Groundbreaker's year-to-date move implies about 59 bp on
1 Jan 2026, against our 150s in February. Treat Meta before April 2026 as unverified.

## Checks
- **Site vs lab:** `ACSI_LAB_DIR=../acsi-lab node --test tests/cds.test.js` (site repo) rebuilds every level from the
  lab's 284 raw files with the site's code: 591 of 591 entity-days identical (level to 0.1 bp, trade count, sign count).
- **Against independent prints** (press reports of Bloomberg, ICE, Reuters and CNBC levels, `e/2026-11-24/data.js` on
  the site): Oracle, 10 dates, median error 2.5%, largest 4.5%. CoreWeave, 6 dates: within 4% on four; −12% against
  Bloomberg's 1,194.5 bp on 15 Oct (tape 1,047.5 from 7 trades) and −12% against a CNBC intraday "at least 1,289" on
  5 Nov (no trade that day; 4 Nov carried). The new rules did not change these.
- **Scorecard** (`v0.2-live`, before → after): R² on the Nasdaq 0.055 → 0.090, on all three factors 0.078 → 0.105;
  most one trade moved it 21.2 → 18.5 points; days resting on an input with under 3 trades 77% → 82%; days set by
  one trade 32% → 37%; CoreWeave same-day trade 63% → 61% of weekdays. Removing non-prices made the tape thinner and
  showed more co-movement with markets, not less. `redteam/nasdaq_null.py` reruns are in the Nasdaq finding.

## What this does not fix
One eligible trade still sets the level on a day with no other trade (38% of scored days carry CoreWeave from an
earlier day; on 37% one input rests on a single trade), and one eligible quote still sets the sign on days the reference decides.
Both are open (`[red-team]` items to follow), and both are attacks by a counterparty willing to print one $1M trade.

## How to reproduce
```
python harness/dtcc.py --days 400
python -m pytest -q tests/test_dtcc.py
python harness/score.py methods/v0.2-live.yaml --baseline methods/v0.1-live.yaml
```

## What would prove it wrong
A day where the excluded records agree with an independent print better than the eligible ones; or a record type we
exclude (a package leg, a novation) that DTCC documents as a new arm's-length price.
