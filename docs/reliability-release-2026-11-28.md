# Research reliability release — 28 November 2026

For the consolidated, code-checked reference, read the [current index manual](current-index-manual.md). This document provides the component-specific detail.

This release repairs data interpretation and publication controls. It does not adopt a new economic index method,
certify a benchmark, or authorize a perpetual contract. The v0.2 anchors and scoring weights remain unchanged;
historical weekly editions remain dated publications. The cash-bond candidate stays separate from the CDS headline.

## What the code changes

| Failure | New behavior | Remaining limit |
| --- | --- | --- |
| An assumed 10/100 bp floor chose an unsigned CDS upfront's sign | Both model roots are examined. Two admissible prices are ambiguous and excluded; a nearby quote or previous level cannot choose a sign | The flat-hazard, fixed-recovery/rate conversion remains a research approximation |
| An inversion outside its numerical range could return a boundary price | Bracket and residual checks reject unsupported inversions; a numerical limit is not economic infeasibility | Independent standard-model validation and distress treatment remain necessary |
| Corrections could be counted as separate trades or miss a later cancellation | Lineage resolution treats linked events as one logical trade and quarantines unresolved/conflicting chains | A bounded collection window cannot establish an indefinitely complete history |
| Missing or renamed fields looked like an empty valid tape | Required source fields and file integrity have explicit failure states | A valid schema does not establish that a provider supplied every market trade |
| A request-time number looked like a final close | Public research status distinguishes current calculations, dated editions and unavailable inputs; settlement eligibility is false | The live research series remains revisable |
| A later recomputation overwrote the only version of a value | The research-fixing writer stores hashed, append-only vintages with explicit cutoff, publication time and supersession | Local hashes are not signatures, independent attestations or storage with enforced retention |
| A failed refresh could obscure the preceding accepted observation | Collection attempts, source success and calculation status are distinct | A saved private replay does not restore the daily feed |
| An incomplete rescore replaced the accepted scoreboard | Publication requires complete source status and canonical hash matches; any method failure preserves the preceding scoreboard | Retrospective scores do not prove forward usefulness |

Matching upfront amounts across names close in time are **suspected** basket legs. Quarantining them is a
conservative research filter, not proof that every matching report belongs to a package. The filter requires
false-positive and false-negative evaluation before a benchmark claim.

## Research fixings and credit events

The [research-fixing contract](research-fixings-v1.md) defines the actual schema and commands. Each artifact binds
the declared method and fixed constituent weights to input hashes and a candidate. Missing constituents do not
redistribute weight to survivors. A declared credit event without an approved rule blocks the value and retains
the affected weight. No arbitrary recovery-to-spread value is introduced in this release.

Corrections create a later vintage linked to the previous artifact; they never silently rewrite the original.
An original calculated today from old snapshots is a retrospective artifact. It cannot be described as a fixing
published, or information available, on the historical observation date. Both publication authorization and
settlement eligibility remain false for this research implementation.

## Bond feed status

The configured FINRA portal initialization returned HTTP 404 during the repair. The private saved-snapshot path
allows the calculation experiment to continue, but automated daily bond collection remains paused until a complete
end-to-end collection succeeds. Raw source files and private archives are not committed to public repositories.

The inherited snapshots are bounded at 500 rows per issue. Their oldest session can be partial and is excluded.
They do not provide verified trade identity and correction chains. A complete candidate basket therefore means
the calculation's coverage rule was met, not that the source is complete, current or suitable for settlement.
Replacing the feed later requires a parallel comparison of identifiers, flags, yields, timestamps, volume caps,
corrections and late reports; a licence change alone does not prove equivalent data semantics.

## Acceptance gates before institutional reliance or a perp

These are required evidence and decisions, not claims that this release satisfies them:

1. **Source and rights:** an approved, supported collection path; explicit rights for the intended distribution and
   derivative use; source availability and completeness reports for every expected session; captured original
   vintages with trustworthy availability times. A historical backfill cannot fill a forward-test day.
2. **Instrument semantics:** independently checked issue-to-borrower/tenant/guarantor mapping, seniority, currencies,
   call/amortization terms and yield convention. CDS and bond spreads must not be treated as interchangeable without
   a tested bridge. Name-count diversification must be distinguished from common economic exposure.
3. **Pricing validation:** independent price/upfront conversion against an approved standard implementation and
   verified contract conventions; defined numerical tolerances fixed before the holdout; stress and boundary cases
   included. Cross-language agreement alone is insufficient.
4. **Market robustness:** preregistered influence, freshness and outage tolerances tied to the proposed use;
   leave-one-out and injected-trade tests; quote/sign attacks; changes in customer-side mix; comparison with genuine
   repricing speed. Three prints are not proof of three independent observations, and filtering extreme spreads
   merely because they are extreme would hide the risk being measured.
5. **Fixed economics:** approved constituent weights/reconstitution, default trigger and recovery observation,
   post-default treatment, rolls, staleness, corrections and cessation. Missingness must not improve the index by
   deleting the weakest borrower. A maximum score is not a substitute for these rules.
6. **Publication and oracle controls:** authenticated releases, access/key controls, sequence and replay checks,
   redundant delivery, incident ownership, challenge/correction procedures and venue behavior during outages.
   Freeze the specified tradable reference and economic payoff; do not settle a perp from a mutable webpage.
7. **Forward evidence:** register the candidate before observation and capture at least 20 trading days under the
   lab's existing forward-test rule, then evaluate the preregistered criteria without retuning to that sample.
   This minimum does not test rare defaults by itself; event and outage simulations remain necessary.

The evidence for a prospective buyer is the reproducible result, the exact source scope and the unresolved gates.
Correlation with broad markets is a descriptive result, not an automatic pass or fail. Synthetic input injection
establishes calculation sensitivity; it does not by itself establish the cost or feasibility of market manipulation.

## Validation and automation

`python -m pytest -q tests` runs offline regressions. Data-dependent tests report skipped when canonical data are
absent. Site validation additionally runs `node --test tests/*.test.js`. The lab's full-source integration is an
explicit workflow-dispatch option, separate from deterministic code checks.

`python harness/refresh_scoreboard.py --collect --days 400` collects and checks inputs before scoring. It preserves
the previous scoreboard on incomplete source data, hash mismatch or any scoring failure, and writes the reason to
`scoreboard/last_attempt.json` and the workflow summary. A handled blocked refresh is not a successful data update.
The historical scoreboard is labelled as pre-strict-decoder evidence until a complete accepted refresh replaces it.
