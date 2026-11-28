# Three decoder choices, tested on the tape's own answer key

**Agent:** Claude (Opus 5.5), run by @gagattzo · **Code under test:** acsi-lab `09990a6` (dtcc-strict-v1) and
Ai-credit-stress-index- `022c39b` · **Data:** 286 DTCC SEC credit end-of-day files, 22 Oct 2025 to 25 Nov 2026

## Question
The strict-decoder release (`2026-11-28-strict-decoder-validation.md`, `docs/reliability-release-2026-11-28.md`) made
three choices that, together, take the live headline down. Should each
be kept, reversed or narrowed? Judged by evidence the rules never used, not by argument: how often a rule is wrong where
the answer is known, how much it can price, and what one trade can do to it.

## 1. The coupon floor for an upfront's sign: keep the strict choice
The tape carries its own answer key: eligible on-the-run 5-year records, across every name, that report both a quoted
spread and an upfront. The quote shows which of the upfront's two readings is right. 4,063 such records in the year.

| | 1% coupon | 5% coupon |
| --- | --- | --- |
| Records | 3,785 | 278 |
| Floor decides: right / wrong | 182 / 0 | **0 / 121** |
| Strict rule accepts: right / wrong | 155 / 0 | 0 / 0 |
| Strict rule excludes | 3,630 (96%) | 278 (100%) |

General Motors, Block, T-Mobile and Royal Caribbean trade a 5% coupon at 65–86 bp; the floor reads them near 1,200 bp.
Upfront-only 5%-coupon trades of AMD, Royal Caribbean, Delta, Lennar and Netflix, whose quotes sit at 34–99 bp, are
read at 1,146–1,340 bp. The 100 bp floor on 5% coupons (introduced in `2026-11-28-cds-contract-checks.md`) is wrong and
stays out. The strict rule was never wrong, but it is nearly blind: the sign for CoreWeave has to come from outside the
tape (for example the issuer's own bond spreads), and any such rule should pass this same test before it ships.

## 2. The quoted-spread sanity range: restore the lower bound, keep no ceiling
34 eligible on-the-run quotes in the year fall outside 10–5,000 bp. All 34 are between 0.81 and 9.0 bp, on high-yield
or distressed names (DISH DBS, Lumen, Macy's, Teva, Organon, Gap, Post, Aramark, American Airlines, Nova Chemicals,
Standard Building, CoreWeave). dtcc-strict-v1 accepts them, which is how `python harness/dtcc.py` now writes CoreWeave
levels of 4.5 bp (24 Oct 2025) and 1.2 bp (14 Apr 2026). None was above 5,000 bp, and the strict finding's point that
a ceiling can hide a real repricing (its 4,000 bp test) stands. So: a 10 bp lower bound back, no upper bound.

## 3. The lifecycle gate on the headline: narrow it
Any quarantined lineage on one of our three names, or any missing requested file, marks the whole source partial and
withholds the headline.
- The site's 12-day window, replayed from every 5th file day (56 windows) through the lab's own `resolve_lifecycle`:
  a quarantine blocks 52; in 16 the quarantined lineage holds a trade that could price CoreWeave or Oracle.
- The site's own code agrees: the lifecycle check passes in 4 of 56 windows, CoreWeave is priced in 4, both in 0.
- On a weekday before that day's file is published (clock fixed at 25 Nov 18:00 UTC, that day's file answering 404),
  the source is `partial` and `calculation_eligible` is false, with reason `requested_file_unavailable`.

Narrow the gate to records that could price a headline input; otherwise show the reading with its quality flags.

## Limits
- The answer key is mostly investment-grade names that quote spreads. CoreWeave never quotes a real spread, so the test
  judges each rule in general, not CoreWeave's own sign.
- Blank coupons are resolved to whichever standard coupon reproduces the quote within max(2 bp, 2%); 573 records match
  neither or both and are left out. Explicit 5%-coupon quotes at 34–99 bp exist too (appendix).
- Records are read file by file; a record cancelled later still counts. That does not change its quote-to-upfront
  relation.
- The window replays sample every 5th start day and assume every file is present, so they understate blocking.
- The weekday check assumes DTCC answers 404 before publication; the site code blocks on other errors too.
- When both readings clear the floor, the old rule chose by quotes or the previous level. That is not scored here;
  `2026-11-28-stress-scenarios.md` showed one trade can attack it.

## How to reproduce
```
python harness/dtcc.py --days 400        # raw DTCC files into data/raw/dtcc (a later run covers a later window)
python research/tools/sign_ground_truth.py
python research/tools/lifecycle_gate_replay.py
node research/tools/site_window_replay.js /path/to/Ai-credit-stress-index-
node research/tools/weekday_block_check.js /path/to/Ai-credit-stress-index-
```
The site checkout must be at `022c39b` or later; the JavaScript scripts call its `api/cds.js`.

## What would prove it wrong
- Floor: evidence that the 5%-coupon answer-key records misstate their coupon. The explicit 5%-coupon quotes at
  34–99 bp would still stand against it.
- Lower bound: one genuine trade on any name below 10 bp.
- Gate: a quarantined record whose exclusion moves a headline input in a window the narrowed gate would let through.

## Appendix: script outputs (28 Nov 2026)
### research/tools/sign_ground_truth.py
286 raw DTCC files, 2025-10-22 to 2026-11-25; lab code at asci-lab

Answer-key records: 4,063 (left out: 573)

| | 1% coupon | 5% coupon |
| --- | --- | --- |
| records | 3,785 | 278 |
| true reading hi | 556 | 7 |
| true reading lo | 3,229 | 271 |
| floor decides, right | 182 | 0 |
| floor decides, WRONG | 0 | 121 |
| floor excludes (both readings clear it) | 3,603 | 157 |
| floor excludes (neither clears it) | 0 | 0 |
| strict accepts, right | 155 | 0 |
| strict accepts, WRONG | 0 | 0 |
| strict excludes | 3,630 | 278 |

Floor errors (first 10):
- 2026-09-10 BLOCK FINANCIAL LLC quoted 80.0 bp, floor reads 1,212 bp
- 2026-09-10 GENERAL MOTORS COMPANY quoted 86.0 bp, floor reads 1,198 bp
- 2026-09-10 BLOCK FINANCIAL LLC quoted 82.0 bp, floor reads 1,207 bp
- 2026-09-10 GENERAL MOTORS COMPANY quoted 86.0 bp, floor reads 1,198 bp
- 2026-09-10 GENERAL MOTORS COMPANY quoted 86.0 bp, floor reads 1,198 bp
- 2026-09-11 GENERAL MOTORS COMPANY quoted 85.5 bp, floor reads 1,198 bp
- 2026-09-11 GENERAL MOTORS COMPANY quoted 85.5 bp, floor reads 1,198 bp
- 2026-09-14 T-MOBILE USA;INC. quoted 65.0 bp, floor reads 1,247 bp
- 2026-09-14 T-MOBILE USA;INC. quoted 65.0 bp, floor reads 1,247 bp
- 2026-09-15 ROYAL CARIBBEAN CRUISES  quoted 79.0 bp, floor reads 1,215 bp

Names quoted under 100 bp on a 5% coupon, and how the floor reads their upfront-only trades within 5 days:
- ADVANCED MICRO DEVICES;I: 9 quotes, median 68 bp; floor reads 24 nearby upfront trades as 1,160-1,226 bp
- ROYAL CARIBBEAN CRUISES : 6 quotes, median 72 bp; floor reads 7 nearby upfront trades as 1,189-1,233 bp
- ORGANON & CO.: 6 quotes, median 2 bp; no nearby upfront trade the floor decides
- COREWEAVE;INC.: 5 quotes, median 4 bp; no nearby upfront trade the floor decides
- DELTA AIR LINES;INC.: 3 quotes, median 99 bp; floor reads 11 nearby upfront trades as 1,146-1,210 bp
- STANDARD BUILDING SOLUTI: 3 quotes, median 1 bp; no nearby upfront trade the floor decides
- LENNAR CORPORATION: 3 quotes, median 72 bp; floor reads 5 nearby upfront trades as 1,204-1,226 bp
- NETFLIX;INC.: 2 quotes, median 34 bp; floor reads 6 nearby upfront trades as 1,309-1,340 bp
- GENERAL MOTORS COMPANY: 2 quotes, median 96 bp; no nearby upfront trade the floor decides
- NOVA CHEMICALS CORPORATI: 1 quotes, median 2 bp; no nearby upfront trade the floor decides
- TEVA PHARMACEUTICAL INDU: 1 quotes, median 1 bp; floor reads 2 nearby upfront trades as 1,201-1,201 bp
- POST HOLDINGS;INC.: 1 quotes, median 1 bp; no nearby upfront trade the floor decides
- AMERICAN AIRLINES GROUP : 1 quotes, median 5 bp; no nearby upfront trade the floor decides
- MGIC INVESTMENT CORPORAT: 1 quotes, median 75 bp; no nearby upfront trade the floor decides
- HCA INC.: 1 quotes, median 50 bp; floor reads 1 nearby upfront trades as 1,232-1,232 bp
- PULTEGROUP;INC.: 1 quotes, median 72 bp; no nearby upfront trade the floor decides
- ARAMARK SERVICES;INC.: 1 quotes, median 1 bp; floor reads 2 nearby upfront trades as 1,157-1,175 bp
- T-MOBILE USA;INC.: 1 quotes, median 87 bp; floor reads 1 nearby upfront trades as 1,174-1,174 bp

Quoted spreads outside 10-5,000 bp: 34, from 0.81 to 9.0 bp
- AMERICAN AIRLINES GROUP : 1 (5.0 bp)
- ARAMARK SERVICES;INC.: 1 (0.81 bp)
- COREWEAVE INC: 3 (1.25, 1.25, 1.25 bp)
- COREWEAVE;INC.: 5 (3.59, 3.75, 3.75, 4.5, 4.5 bp)
- DISH DBS CORP: 7 (1.75, 2.25, 9.0, 9.0, 9.0, 9.0 bp)
- LUMEN TECHNOLOGIES INC: 1 (7.5 bp)
- LUMEN TECHNOLOGIES;INC.: 1 (8.75 bp)
- MACY'S;INC.: 2 (3.75, 3.75 bp)
- NOVA CHEMICALS CORPORATI: 1 (1.67 bp)
- ORGANON & CO.: 6 (1.55, 1.55, 2.01, 2.35, 2.56, 3.96 bp)
- POST HOLDINGS;INC.: 1 (1.3 bp)
- STANDARD BUILDING SOLUTI: 3 (1.0, 1.0, 1.12 bp)
- TEVA PHARMACEUTICAL INDU: 1 (1.12 bp)
- THE GAP;INC.: 1 (1.38 bp)

### research/tools/lifecycle_gate_replay.py
286 raw DTCC files, 2025-10-22 to 2026-11-25; lab code at asci-lab

Whole year in one ledger: 61 quarantined lineages on our three names ({'orphan_lineage': 50, 'cyclic_lineage': 27, 'ambiguous_correction_order': 12}); 10 hold a CoreWeave or Oracle trade that could price an input:
- ambiguous_correction_order: CORR ORCL 2025-10-28, CORR ORCL 2025-10-28, NEWT ORCL 2025-10-28
- ambiguous_correction_order: CORR ORCL 2025-10-28, CORR ORCL 2025-10-28, NEWT ORCL 2025-10-28
- ambiguous_correction_order: CORR ORCL 2025-11-05, CORR ORCL 2025-11-05, NEWT ORCL 2025-11-05
- ambiguous_correction_order: CORR ORCL 2025-12-02, CORR ORCL 2025-12-02, NEWT ORCL 2025-12-02
- ambiguous_correction_order: CORR CRWV 2026-02-24, CORR CRWV 2026-02-24, NEWT CRWV 2026-02-24
- orphan_lineage: CORR ORCL 2026-03-26
- ambiguous_correction_order: CORR ORCL 2026-04-16, CORR ORCL 2026-04-16, NEWT ORCL 2026-04-16
- ambiguous_correction_order: CORR CRWV 2026-10-15, NEWT CRWV 2026-10-15
- ambiguous_correction_order: CORR CRWV 2026-10-16, NEWT CRWV 2026-10-16
- ambiguous_correction_order: CORR CRWV 2026-11-18, NEWT CRWV 2026-11-18

12-day windows (every 5th start day): 56; blocked by a quarantine: 52; a quarantined trade that could price CoreWeave or Oracle: 16

### research/tools/site_window_replay.js
286 raw DTCC files, 2025-10-22 to 2026-11-25; site code at ai-credit-stress-index-

12-day windows (every 5th start day): 56
- lifecycle check complete: 4
- CoreWeave priced at least once: 4
- both, so the headline could show: 0
- windows with each lifecycle issue: {"orphan_lineage":51,"ambiguous_correction_order":13,"cyclic_lineage":20,"unknown_action":11}

### research/tools/weekday_block_check.js
now 2026-11-25T18:00:00Z; 2026-11-25 end-of-day file not yet published; site code at ai-credit-stress-index-

source_quality.status: partial
calculation_eligible: false
reasons: ["requested_file_unavailable","lifecycle_quarantine"]
missing_dates: ["2026-11-25"]
lifecycle issues: {"orphan_lineage":19,"ambiguous_correction_order":1}
