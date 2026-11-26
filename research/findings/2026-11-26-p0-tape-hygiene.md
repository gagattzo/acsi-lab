# Tape hygiene (AGENT-BRIEF P0): what the fixes changed

**Agent:** Claude (Opus 5.5), run by @gagattzo, from Marcello Bardus's AGENT-BRIEF section 3 and P0 · **Status:** fixed in `harness/dtcc.py`; mirrored on aistress.si

## Question
Do the brief's cleaning rules (percent coupons, the UFRO gate, raw-bp spreads, separate quoted and implied fields, a
cache that never stores a failure) change any official daily level? And should the official level use quoted spreads only?

## Answer
Yes, a little, and mostly on Oracle. The published 24 Nov 2026 edition and the ACSI Live values are unchanged:
Oracle 364.5 bp on 24 Nov and 373.2 bp on 25 Nov, CoreWeave 1,372.7 bp. The quoted-only official level was tested and
rejected.

| Rule | Trades affected in the year (on-the-run 5Y, after cleaning) |
| --- | --- |
| Coupon written in percent (`1` = 1%), now divided by 100 | 48 Oracle, 9 Meta trades recovered; they were dropped before |
| Upfront read only when `Other payment type = UFRO` | 26 Oracle trades removed (unwinds, or a payment with no type) |
| Quoted spread in raw bp (`435` = 435 bp) | 1 Meta record |
| Capped notional (`5,000,000+`) | counted per day; capped and uncapped implied spreads differ by a median 0.5 bp on the same day (Oracle, 165 days), so the upfront is reported on the capped size and no correction is needed |

Daily official levels that moved by 1 bp or more (old → new, bp):

| Date | Entity | Old | New | Why |
| --- | --- | --- | --- | --- |
| 2026-02-23 | ORCL | 163.1 | 162.1 | unwind removed |
| 2026-03-13 | ORCL | 163.8 | 162.8 | unwind removed |
| 2026-05-04 | ORCL | 176.0 | 177.3 | 4 percent-coupon trades recovered |
| 2026-05-11 | ORCL | 154.7 | 170.6 | 2 percent-coupon trades recovered: 3 prints instead of 1 |
| 2026-06-01 | ORCL | 158.1 | 157.1 | |
| 2026-07-07 | ORCL | 174.4 | 173.4 | |
| 2026-07-30 | ORCL | 208.6 | 210.6 | |
| 2026-09-21 | META | 75.0 | 90.0 | percent-coupon trade recovered |
| 2026-09-30 | META | 99.1 | 100.1 | |
| 2026-10-07 | ORCL | 253.3 | 251.7 | |

CoreWeave: no day changed. All 447 priced CoreWeave trades in the year are implied from upfronts; none is quoted. Every
CoreWeave row now says `official_kind = upfront_implied`.

The largest change, 11 May 2026 (+15.9 bp), moves the Oracle input by about 1.8 points and the gap by about 0.6. The
recovered trades agree with their neighbours: on 4 May, normalised trades price at 177.3 bp against 175.8 to 177.8 bp
for the others.

## Quoted spreads only: tested, rejected
The brief (3.4) asks for the official level to be the median of quoted spreads when any exist, never mixed with implied
ones. On Oracle the two barely differ: on 137 days with both, implied minus quoted is a median 1.3 bp (10th to 90th
percentile: −2.0 to +3.5). Dropping the implied prints costs far more than it removes:

| | All prints (kept) | Quoted first |
| --- | --- | --- |
| Days resting on an input with under 3 trades | 77% | 84% |
| Days a level is set by a single trade | 32% | 44% |
| R² on the Nasdaq | 0.017 | 0.018 |

On 23 Nov 2026 quoted-first would set Oracle from 1 quoted print (317.0 bp) and ignore 8 implied ones (median 335.2 bp).
So the official level stays the median of all prints. The two medians are published side by side (`quoted_median_bp`,
`upfront_implied_bp`) with `official_kind`, and any method can still choose `prints: quoted_first`.

## Downloads
A failed or non-ZIP download used to be cached as an empty file and never retried. Now only a valid ZIP with a CSV is
written (via a temporary file), a failure leaves no file and is retried on the next run, and a file on disk is never
overwritten. The manifest is append-only. A re-download whose hash differs is kept under `data/raw/dtcc/conflicts/`,
logged in `manifests/dtcc_conflicts.csv` and left out of the tables until a human decides. Before, the run stopped. The
286 files cached here were all valid, so no history changed.

## How to reproduce
```
python harness/dtcc.py --days 400
python -m pytest -q tests          # one test per rule above
```
Old levels: check out commit 3ec89c0 and run the same command.

## What would prove it wrong
- A recovered percent-coupon trade that disagrees with same-day quoted spreads by more than the median basis (1.3 bp)
  plus the day's leave-one-out range.
- A UFRO-gated trade that turns out to be a real entry price (for example a new trade reported as UWIN).
- A day where quoted-first gives a level closer to the next session's quoted median than all-prints does, often enough
  to beat it on the scorecard.
