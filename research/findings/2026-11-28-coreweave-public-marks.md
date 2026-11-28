# CoreWeave's upfront sign: dated public CDS marks decide it, ratings do not

**Agent:** Claude (Opus 5.5), run by @gagattzo · **Data:** 288 DTCC SEC credit end-of-day files, 22 Oct 2025 to 27 Nov 2026,
and ten dated public CoreWeave 5-year CDS levels from news reports · **Code:** `research/tools/coreweave_marks_check.py`,
`harness/dtcc.py` `attested_side` (decoder `dtcc-attested-v3`)

## Question
DTCC publishes upfront payments without a sign. Every CoreWeave upfront-only trade since October 2025 has two feasible
readings, one on each side of its 5% coupon, so the strict decoder (`dtcc-strict-v2`) priced CoreWeave on one day in
thirteen months and the headline was withheld. The reviewer of `2026-11-28-decoder-choices-evidence.md` concluded that
"CoreWeave's two feasible readings still require independent information". Which independent information can decide
the side without deciding the level?

## 1. Rejected: a rating band
Rule tried: rule out a reading that implies a rating 4 or more notches better than the issuer's S&P rating, on the
Groundbreaker grid the site uses. It resolves CoreWeave's 24 Nov trade (12 bp ruled out, 1,373 bp kept), but it fails
where it can actually fire. On model-matched trades that report both a quote and an upfront, so the true reading is
known (new reports read file by file, as in `sign_ground_truth.py`), it throws out the true reading again and again:

| Name | S&P | Trades | Rule right | Rule **wrong** | Example |
| --- | --- | --- | --- | --- | --- |
| AutoZone | BBB | 17 | 0 | 17 | 6 May 2026: true 27 bp, rule picks 185 bp |
| RTX | BBB+ | 21 | 0 | 21 | 23 Nov 2026: true 23 bp, rule picks 191 bp |
| Caterpillar | A | 31 | 0 | 31 | 19 Aug 2026: true 18 bp, rule picks 197 bp |
| Honeywell | A | 19 | 0 | 19 | 2 Jun 2026: true 20 bp, rule picks 196 bp |
| Allstate | A- | 36 | 0 | 29 | 3 Sep 2026: true 27 bp, rule picks 189 bp |
| Motorola Solutions | BBB | 36 | 0 | 36 | 20 Apr 2026: true 30 bp, rule picks 178 bp |
| Boeing | BBB- | 38 | 0 | 20 | 7 Aug 2026: true 54 bp, rule picks 155 bp |
| Oracle | BBB- | 155 | 155 | 0 | |
| Meta | AA- | 139 | 0 | 0 | cannot fire: nothing is 4+ notches better than AA- |

Investment-grade names routinely trade 5 to 7 notches tighter than this grid implies for their rating. AutoZone, RTX
and Motorola Solutions fail even if each were rated two notches higher. An earlier draft of this result counted
Meta's 139 trades as passes; they test nothing. On CoreWeave's typical November day the rating is roughly equidistant
from both readings (23 Nov: 107 bp is about BBB-, 1,139 bp about CCC; S&P has B+), so the band decides only by
assuming markets are never far better than agencies, which the table refutes. Ratings are not used.

## 2. CoreWeave's two readings against its own public marks
Ten dated 5-year levels for CoreWeave were found in news reports citing a data provider (Bloomberg, ICE, Markit,
Deutsche Bank, S&P Global, Reuters). Nine have an exact date and are in `harness/sign_attestations.json`; the early-June
low has no exact date and is used here only as a check. Median readings of the DTCC upfront-only trades on the mark's
day, or the nearest earlier trading day within two days. Only trades the decoder can price are used: eligible on-the-run
upfront-only reports after the cross-file lifecycle ledger and the basket-leg screen. Every on-the-run CoreWeave report
executed on 5 and 6 November was later cancelled (EROR), so those two marks are compared with 4 November.

| Mark | bp | DTCC day | High | Low | Mark sides with | Rule using earlier marks only |
| --- | --- | --- | --- | --- | --- | --- |
| 6 Oct 2025 (Bloomberg) | 368 | none within 2 days | | | | |
| 5 Dec 2025 (Bloomberg) | 643 | 4 Dec | 651 | 417 | high | abstains (no recent mark) |
| early Jun 2026 (approximate) | 452 | 4 Jun | 597 | 465 | **low** | abstains (no recent mark) |
| 28 Jul 2026 | 855 | 28 Jul | 886 | 224 | high | abstains (no recent mark) |
| 15 Oct 2026 (ICE) | 1,195 | 15 Oct | 1,078 | 116 | high | abstains (no recent mark) |
| 21 Oct 2026 (CNBC) | 1,067 | 20 Oct | 1,142 | 89 | high | 1,142 bp, +7% |
| 27 Oct 2026 (Markit) | 1,041 | 27 Oct | 1,035 | 143 | high | 1,035 bp, -1% |
| 5 Nov 2026 (Deutsche Bank, intraday spike) | 1,289 | 4 Nov | 1,140 | 97 | high | 1,140 bp, -12% |
| 6 Nov 2026 (Deutsche Bank) | 1,155 | 4 Nov | 1,140 | 97 | high | 1,140 bp, -1% |
| 25 Nov 2026 (Reuters) | 1,314 | 24 Nov | 1,373 | 12 | high | 1,373 bp, +4% |

On eight of nine dates the high reading sits within -12% to +7% of the public mark while the low reading is off by up
to 100 times. On the ninth, early June, CoreWeave was below its coupon and the **low** reading was right: "always take
the high reading" would have overstated it by about 30%. Leave-one-out, the rule decided five marks (four distinct
DTCC days), all on the right side, level differences -12% to +7%, and abstained on the other four. It made no
wrong-side decision.

The same comparison is also the first independent check of the decoder's CoreWeave *level*: where it decides, it lands
within 12% of a dealer or data-provider mark, and the largest gap is against an intraday spike on the next day.

## 3. The rule (`dtcc-attested-v3`)
For a trade whose upfront has two distinct feasible readings, `attested_side` takes the name's latest mark in
`harness/sign_attestations.json` dated on or before the execution day and keeps the reading on the mark's side of the
coupon only if:
- the mark is at most **21** calendar days old;
- it sits at least **1.5x** away from the coupon (at least 750 bp or at most 333 bp on a 5% coupon), so the side is
  clear when it was reported;
- the two readings are at least **2x** apart, so the trade itself is not near the coupon, where the side can flip
  between marks.

Otherwise the trade stays ambiguous and prices nothing, with reason `no_public_mark`, `public_mark_stale`,
`public_mark_near_coupon` or `readings_near_coupon`. The DTCC trade sets the level; the mark only says which side of the
coupon. Quotes, previous levels, ratings and payment size still never decide. A one-root upfront, a quote and every other
name are unchanged: today only CoreWeave has marks.

With the nine marks, CoreWeave prices on 36 days instead of one (February 1 of 11 trading days, from a one-root upfront;
July 4 of 16; August 7 of 17; October 10 of 17; November 14 of 14). The website's headline, replayed on the same nine files its production endpoint read on 28 November,
returns **+67.8** (credit 83.1, GPU 15.3; CoreWeave 1,373 bp on 24 Nov, side from the 6 Nov mark; Oracle 373 bp on
25 Nov), labelled research.

## Limits
- **A person maintains the marks.** If no new mark is added within 21 days, CoreWeave stops pricing, and the headline
  goes dark after its 10-day CDS carry. Each mark must cite a dated report that states the value.
- **A jump across the coupon between marks is misread until the next mark.** A rescue or acquisition could move
  CoreWeave from 1,300 bp to 100 bp overnight; the mirror reading (about 1,050 bp) would look continuous. The 2x and
  21-day limits bound the window, not the possibility.
- **Small sample.** Five out-of-sample decisions, four of them in one five-week stretch with consecutive marks. This is
  corroboration, not a validated error rate. A forward test against new public marks is the next check.
- **Press values, not a licensed feed.** Each mark is one value from one public report, with its link. The series is
  sparse by design. If a data provider objects, the side can instead come from licensed marks under their terms.
- **Near the coupon CoreWeave abstains**, as in February to June 2026 (readings within 2x).

## How to reproduce
```
python harness/dtcc.py --days 400             # raw files into data/raw/dtcc; canonical tables with the marks
python research/tools/coreweave_marks_check.py
python -m pytest tests/test_dtcc_attested_side.py tests/test_dtcc_parity_corpus.py
```
On the website: `node --test tests/cds-attested-side.test.js tests/headline-gate.test.js`, and the full-history
comparison `ACSI_LAB_DIR=<lab> node --test tests/cds.test.js`.

## What would prove it wrong
- A dated public mark that sides with the reading the rule did not pick, on a day the rule decided.
- A public mark for a decided day that differs from the decided level by much more than the -12% to +7% seen here,
  which would point at the model or at the trades rather than the side.
- A crossing of the coupon inside a 21-day window with both readings at least 2x apart.
