# Retrospective decoder diagnostics: three choices under review

**Agent:** Claude (Opus 5.5), run by @gagattzo · **Code under test:** acsi-lab `09990a6` (dtcc-strict-v1) and
Ai-credit-stress-index- `022c39b` · **Data:** 286 DTCC SEC credit end-of-day files, 22 Oct 2025 to 25 Nov 2026

**Reviewer note — 28 November 2026.** The reported results are useful retrospective diagnostics, not a completed
out-of-sample decoder validation or approval to change the live methodology. The full-year counts and appendix
outputs below are the original author's reported results; this review inspected the scoring scripts and a bounded
October–November source replay, but did not independently rerun all 286 files. In particular, the 4,063 retained
quote/upfront records are **model-matched observations**, not an independently established answer key. This revision
clarifies their selection, the interpretation of the error counts and the limits of the lifecycle analysis.
The proposed lower quoted-spread bound remains **10 bp with no upper ceiling**.

The reviewer also reran the unchanged `sign_ground_truth.py` on 42 cached files from 1 October through
27 November 2026. It retained 1,180 model-matched records and excluded 18: the floor disagreed on 73 of 73
5%-coupon decisions, while strict accepted 78 matching 1%-coupon cases. These bounded results reproduce the
direction of the author's diagnostics, subject to the same sample-selection qualifications below; they do not
independently reproduce the 286-file totals.

## Question
The strict-decoder release (`2026-11-28-strict-decoder-validation.md`, `docs/reliability-release-2026-11-28.md`) made
three choices that, together, can withhold the live headline. Which should be kept, reversed or narrowed?
These diagnostics examine conditional errors, coverage and source blocking on historical data already inspected.
A later release decision needs a frozen rule, predefined pass criteria, untouched issuer/time holdouts and a forward
test. The scripts here do not establish a one-counterparty manipulation bound.

## 1. The coupon floor for an upfront's sign: retain strict abstention pending independent validation
Some eligible on-the-run 5-year records report both a quoted spread and a UFRO amount. In
`research/tools/sign_ground_truth.py`, a reported standard coupon is used when available. When it is blank, the
script tries 1% and 5% coupons and both signs, retaining the record only if exactly one model candidate reproduces
the quote within max(2 bp, 2%). The selected candidate becomes the evaluation label.

The author reports **4,063 retained model-matched records and 573 excluded records** that matched neither or more
than one candidate. The table therefore measures agreement with that selected label, conditional on this filter;
it is not accuracy over every eligible trade.

| | 1% coupon | 5% coupon |
| --- | --- | --- |
| Retained model-matched records | 3,785 | 278 |
| Floor decides: agrees / disagrees with selected label | 182 / 0 | **0 / 121** |
| Strict accepts: agrees / disagrees with selected label | 155 / 0 | 0 / 0 |
| Strict rule excludes | 3,630 (96%) | 278 (100%) |

**The strict rule's 155/155 agreement is not an independent zero-error result.** The sample is selected for a unique
quote-matching model candidate. When the strict rule accepts its only feasible root, that root must also be the
candidate that qualified the record for inclusion. This is a useful consistency check, but it cannot establish that
the solver, payment interpretation or quote itself is economically correct.

The script also uses the reported notional directly, including capped values marked with `+`; it does not recover
the exact notional or exclude these records. This can affect the upfront fraction, inferred coupon and sign label.
Validation must separately report explicit versus inferred coupons, exact versus capped notionals, and all
nonmatches. A coupon inferred by fitting the evaluation quote is not independent contract evidence.

Within the author's model-matched sample, General Motors, Block, T-Mobile and Royal Caribbean are assigned a 5%
coupon at quoted spreads of 65–86 bp, while the floor selects a reading near 1,200 bp. The separately reported
explicit 5%-coupon quotes at 34–99 bp, and nearby upfront-only observations for AMD, Royal Caribbean, Delta,
Lennar and Netflix, also challenge the 100 bp floor. The nearby observations are within five days, not necessarily
simultaneous, so they provide corroboration rather than a same-trade label.

These are reasons to **keep the coupon floor out**. They do not validate an alternative sign selector.
CoreWeave's two feasible readings still require independent information if a single price is to be produced.
An issuer-bond rule is a research candidate: match the legal credit, seniority, maturity and observation time,
account for bond/CDS basis and stale inputs, and keep the evaluation quote hidden from the predictor.
Use independently established contract terms and trustworthy labels, measure abstentions and coverage, then test
the entire CDS-plus-bond rule against manipulation before proposing adoption.

## 2. The quoted-spread sanity range: research recommendation of a 10 bp lower bound, no ceiling
The author reports 34 eligible on-the-run quotes outside 10–5,000 bp, all between 0.81 and 9.0 bp and on high-yield
or distressed names (DISH DBS, Lumen, Macy's, Teva, Organon, Gap, Post, Aramark, American Airlines, Nova Chemicals,
Standard Building, CoreWeave). The current decoder accepts such positive finite values. The author's full-year
replay reports CoreWeave levels of 4.5 bp (24 Oct 2025) and 1.2 bp (14 Apr 2026).

The proposed repair is to quarantine quoted spreads below 10 bp for this research feed and retain diagnostics.
The observed sample supports investigating a lower bound; it does not prove that a genuine sub-10 bp CDS trade is
universally impossible. Units and field semantics still require validation. This quoted-input policy must never be
used to declare an alternative upfront root economically impossible.

**Do not restore an upper ceiling.** The author reports no quotes above 5,000 bp in this sample. Absence in this
history does not justify suppressing a future distressed repricing. The lower-bound-only proposal is a research
recommendation pending tests and a separate implementation review.

## 3. The lifecycle gate on the headline: research recommendation to narrow by demonstrated impact
Any quarantined lineage on one of our three names, or any missing requested file, marks the whole source partial and
withholds the headline in the code under test.

The author reports:
- The site's 12-day window, replayed from every 5th file day (56 windows) through the lab's own
  `resolve_lifecycle`: a quarantine blocks 52 windows; in 16, an observed quarantined record satisfies the script's
  conditions for a trade that could price CoreWeave or Oracle.
- The site's own code passes the lifecycle check in 4 of 56 windows and prices CoreWeave at least once in 4; no
  sampled window passes both checks.
- On a simulated weekday before that day's file is published (clock fixed at 25 Nov 18:00 UTC and that day's file
  answering 404), the source is `partial` and `calculation_eligible` is false, with reason
  `requested_file_unavailable`.

**The 16 windows measure a visible-record predicate, not actual headline impact or a validated safe classifier.** Incomplete lineages
can lack the ancestor that establishes issuer, maturity, eligibility or a price previously carried into the headline.
A correction can change those fields. An observed out-of-scope row therefore does not by itself prove that its
unresolved lineage cannot affect a headline input. The remaining blocked windows cannot all be declared safe from
this count. The recording hook keeps `nodes.values()`, which retains only the first version of a conflicting
identifier; reordering conflicting eligible and ineligible versions can change this relevance result without
changing the ledger's quarantine. Unknown-action and missing-identifier issues are outside this hook. Conversely,
the predicate does not test successful pricing, basket exclusion or the execution/carry interval, so it can also
count records that would never contribute. The 16 is therefore not a proven lower or upper bound on affected windows.

A narrower gate should permit a calculation only when the pipeline can demonstrate that the unresolved records
cannot affect the constituent values used. Unknown impact must remain explicit and fail closed for the affected
calculation. Preserve broader source-quality flags even when a particular headline is unaffected. Separately,
define the expected publication calendar/cutoff and distinguish the latest completed observation from an incomplete
current session; an unpublished current-day file should not be mistaken for proof that a previously complete
observation is invalid. These changes require lineage-level tests and replay before deployment.

## Limits
- The paired observations mostly concern investment-grade names that quote spreads. CoreWeave has no independently
  validated real quote labels in this study, so apparent performance on other issuers does not establish its sign.
- Blank coupons are fitted to the same quote used to select the label; 573 unmatched or non-uniquely matched records
  are excluded. The script does not independently recover capped notionals. Conditional results must be accompanied
  by these exclusions and by explicit/inferred coupon and exact/capped notional breakdowns.
- Records are read file by file, without cross-file lifecycle reconstruction. A later cancellation or correction can
  identify a bad original record. Repeated reports and issuer/date clustering also mean records are not independent
  validation trials.
- The window replays sample every fifth file day and assume the requested files are present. They are not an exhaustive
  operational-availability study. Visible-record relevance understates uncertainty from missing ancestors.
- The weekday check simulates a 404 before publication; the site code blocks on other missing/error states too.
- When both readings clear the old floor, the old rule chose by quotes or the previous level. That branch is not
  scored here; `2026-11-28-stress-scenarios.md` separately demonstrates a one-trade attack.
- No untouched holdout or forward test was performed by these scripts. No new bond-based sign rule or manipulation
  guarantee is established here.

## How to reproduce
These commands reproduce the author's diagnostics, not an independent validation. Pin the named code versions and
retain source manifests to compare with the original reported window; a new download can contain later revisions
and a different date range.

```
python harness/dtcc.py --days 400        # raw DTCC files into data/raw/dtcc (a later run covers a later window)
python research/tools/sign_ground_truth.py
python research/tools/lifecycle_gate_replay.py
node research/tools/site_window_replay.js /path/to/Ai-credit-stress-index-
node research/tools/weekday_block_check.js /path/to/Ai-credit-stress-index-
```

The JavaScript scripts call the site checkout's `api/cds.js`; the original output used `022c39b`.
Later versions may legitimately produce different results.

## What would change these conclusions
- Floor: independently established contract terms and lifecycle-valid observations that invalidate the reported
  counterexamples. Retain the explicit-coupon examples separately from fitted-coupon labels.
- Lower bound: a verified genuine sub-10 bp trade in the intended feed/universe, or corrected units/semantics showing
  that the suspect values should be decoded differently.
- Gate: an unresolved lineage that changes a constituent or carried value in a case a proposed narrowed gate permits.
  Missing ancestor information must be included in the test, not assumed irrelevant.
- New sign rule: predefined error, coverage and attack criteria met on independently labelled untouched observations,
  followed by forward testing. The historical model-matched sample alone is insufficient for adoption.

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
