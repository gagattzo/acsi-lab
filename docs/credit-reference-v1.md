# Proposed AI credit reference v1

**Status:** design proposal, November 27, 2026. Agreed product direction; not an adopted method or a settlement-ready oracle. No runtime, live index, source entitlement or production configuration is changed by this document.

**Author:** Codex (GPT-6), working with @gagattzo.

## Purpose and relationship to the live index

Develop a credit-only reference for long/short exposure to the market pricing of AI credit risk. Preserve ACSI's debt-minus-GPU gap as a separate divergence indicator. A long credit-stress position should benefit from widening credit spreads, subject to the contract's execution, funding and liquidation mechanics.

The existing v0.2 method is unchanged. It removed the financing/asset scores' 100-point ceiling; this proposal does not relabel the live gap as bounded 0–100.

Proposed product-page language:

> Tracks the market pricing of AI credit risk, not realized default losses. Credit stress can peak before bankruptcy.

The contract description must additionally explain entry/exit prices, funding, fees and liquidation. A credit-stress reference is not default insurance.

## Decisions and unresolved parameters

| Item | Direction for v1 | Remaining decision |
| --- | --- | --- |
| Exposure | Absolute AI credit stress; no subtraction of GPU stress or broad-market credit stress | Final eligible universe and borrower register |
| Units | Credit spreads in basis points; no calm/distress anchors or arbitrary score ceiling | Exact yield/spread and maturity conventions |
| Across borrowers | Equal weights, fixed between scheduled reweights | Group definition and review calendar |
| Within a borrower | Fixed selected issues provide one borrower allocation | Comparable-issue selection and within-group weights |
| CDS | Corroboration and cash/CDS basis initially | Any later inclusion shares an explicit borrower allocation |
| Missing data | Never renormalize onto the available borrowers | Final carry limit, publication cutoff and venue response |
| Default | Explicit event state; no disappearance, zero imputation or implicit renormalization | Recovery source, transition, valuation and contract settlement |
| Reweighting | No unexplained position P&L solely from changing membership | Series settlement or explicit continuity adjustment |
| Current data | Distinguish portfolio marks, transactions and publisher charts | Qualified daily tape and appropriate use rights |

## Formula and borrower groups

For a version with G borrower groups:

    W[g] = 1 / G
    spread[g,t] = sum_j a[j,g] * spread[j,t]
    raw_credit_spread[t] = sum_g W[g] * spread[g,t]

Within each group, a[j,g] are nonnegative, sum to one, and stay fixed for that version. This formula describes a complete, qualified, pre-default observation. Unresolved default states and missing inputs do not evaluate as zero. No default mapping is implicitly defined by this formula.

Four corporate bonds issued by the same CoreWeave borrower do not receive four borrower allocations. Separate project borrowers with CoreWeave as tenant remain distinct legal claims unless the documented obligor/guarantee structure justifies consolidation. Equal borrower weights do not eliminate common-tenant concentration. The six tenant labels in the chart are not a verified borrower register.

The register must identify each included issue, borrower group, legal obligor, guarantor/support scope, security/seniority, relevant common tenant, document provenance, and effective dates. A default affects the claim or obligor specified by the event policy; it does not automatically place every bond sharing that tenant into default. Do not infer identical recoveries across different claims.

Publish each borrower's weight and contribution, plus direct and tenant-linked common exposures. Avoid daily weights based on market value, trading volume or freshness. Equal weighting removes the need for debt-outstanding amounts to set top-level weights, but not the need to validate the legal grouping.

Equal reference-entity weights are consistent with the weighting principle in the CDX rules [1]. CDX also has premium, default and notional-adjustment mechanics; this proposed arithmetic spread reference does not inherit those economics.

## Per-issue observation policy

The preferred transaction estimator will resolve corrections/cancellations, special-price conditions and duplicate records before estimation. Customer-trade medians above a minimum size are a candidate, not a finalized rule or proof of manipulation resistance.

Test customer buys and sells separately, interdealer corroboration, trade clustering, size thresholds and full-session versus closing-window estimates. A single print is not independent evidence merely because it passes a size threshold. Multiple prints need not represent unrelated traders. A full-day median can miss a genuine late repricing.

Preserve execution, dissemination, first-observed and publication times separately. Match the rate benchmark to the bond observation; do not create a supposedly fresh spread from an old bond yield and a new Treasury rate. Validate accrued interest, coupon schedules, calls, amortization and settlement before treating model yields as comparable.

Keep three source tracks separate: qualified transactions, fund portfolio marks, and verified publisher-chart observations. The current free holdings-based model is not a TRACE last-sale feed. Research observations and source comparisons do not establish permission or fitness for a public settlement benchmark.

## Freshness and publication states

The policy must distinguish `fresh`, `carried`, `stale`, `missing`, `default_pending` and `default_resolved`. Default status is not an age label. Every reading carries source dates, first-observed times, method version, calendar/cutoff, coverage by fixed economic weight, and reasons for ineligibility.

Proposed research experiment: allow at most **one completed US trading session** of carry after the last qualified observation. This is a shadow-test parameter, not a validated production threshold. Specify holidays, early closes and daylight-saving transitions before measuring its coverage. Also expose elapsed hours, because a holiday weekend still creates market risk.

| State | Research output | Contract consequence |
| --- | --- | --- |
| Fresh, all required inputs qualified | Dated raw spread and full provenance | Candidate fixing, subject to all other gates |
| Within explicit carry limit | Dated reading labelled carried; unchanged weights and original observation dates | Eligibility depends on an approved carry policy, not the flag alone |
| Beyond limit or required input missing | No new qualified fixing; retain last historical reading with its original date | Invoke predefined venue response; never silently declare the old mark current |
| Default pending/resolved | Separate event record; no invented spread | Requires the approved event/settlement policy |

The venue response must cover funding, new exposure, liquidation marks, halt/settlement semantics and resumption. No generic pause or forced settlement is assumed safe. A successful scraper heartbeat is not a fresh market observation. Corrections may revise research history but cannot silently rewrite past contractual settlements or liquidations.

## Default and reweighting: required contract decision

Keeping the defaulted borrower in the basket until reweighting is necessary bookkeeping, but does not by itself define a valid payoff. Recovery is a price or loss fraction; spread is an annualized rate. A recovery-based spread proxy requires a stated horizon/cash-flow convention and is synthetic. The recovery may not yet be available on the event date, and different seniorities can recover differently.

Before any contract uses the reference, specify:

1. Credit-event definition, affected legal obligations, evidence and effective time.
2. Eligible recovery sources, observation window, correction policy and fallback when no auction or qualified recovery price exists.
3. Treatment between the event and recovery determination; no unannounced freeze at an economically arbitrary level.
4. Transition from pre-default spread to the chosen post-event representation, including a worked position-P&L example.
5. Removal/replacement at the next review and how any mechanical level change is separated from market P&L.

Two architectures must be evaluated explicitly: a raw spread series with contract-level event/roll settlements, or a continuity-adjusted tradable reference that records those adjustments. The latter is no longer literally the current average borrower spread. Keep the raw spread diagnostic identifiable. Do not call an undefined post-default yield an observed spread or approve a recovery-to-bp function merely because it is pre-announced.

CDX handles credit events with settlement and a reduced remaining notional. ICE's explanation of credit-futures cash-flow equivalence illustrates why a separate cash-flow component is needed when the remaining index value changes [2]. This is a design reference, not a claim that the proposed perp has those capabilities.

## Worked counterexamples

All figures here are synthetic, freely reproducible arithmetic examples. They are not market observations, historical returns or executable attacks.

| Scenario | Result | Required property |
| --- | --- | --- |
| Four already-distressed issues in a 24-issue basket each widen 100 bp | Cross-sectional median changes 0 bp; equal-issue mean changes 16.67 bp | Preserve genuine tail contributions |
| Bid stays 99, offer 101; customer flow changes from mostly buys to mostly sells | Pooled median falls 2 price points without a midpoint move | Control for side composition |
| Eight early trades at 100, two late trades at 90 | Full-day median remains 100 | Measure event lag, not just outlier resistance |
| A 40%-weighted group at 1,500 bp disappears; other 60% remains at 300 bp | Renormalized basket falls from 780 to 300 bp | No missing/default renormalization |
| A 10%-weighted default proxy at 3,000 bp is replaced by 300 bp | Raw index falls 270 bp solely due to replacement | Explicit roll accounting |
| A 10%-weighted group transitions from 4,000 bp to a prescribed 2,000 bp default proxy | Raw index falls 200 bp at the event | Validate the transition, not merely the terminal value |
| Old bond yield 10%; Treasury benchmark changes from 4% to 3.5% | Apparent 50 bp widening without a fresh bond observation | Synchronized input dates and freshness |

Run from a fresh checkout of this proposal:

```bash
python research/tools/credit_index_counterexamples.py
```

No private November 25 snapshot, third-party packages or network access is needed. The script demonstrates failure modes, not a completed estimator or default policy.

## Implementation order and acceptance

1. **Borrower register:** validate the 24 candidate issues and legal group map. Freeze top-level equal weights, eligible issue set and within-group allocation before comparing realized volatility.
2. **Data audit:** retain daily originals and availability times; profile qualified trade coverage where access permits. Quantify what the one-session carry experiment would make unavailable. Verify model cash flows and same-date curves.
3. **Contract specification:** settle the default/roll choice and stale-oracle response using numerical position-P&L examples and the intended venue's actual capabilities. A purely mechanical bookkeeping change must not create unexplained economic profit/loss; recovery news and genuine credit moves may.
4. **Separate implementation PRs:** source/decoder changes follow DATA.md; method/schema extensions and shadow calculations follow the lab's review process. Do not encode unimplemented keys into a nominally runnable method YAML.
5. **Validation:** compare the fixed-weight candidate to existing methods on appropriate common observations, examine influence and event lag, then run the registered 20-trading-day forward test. This is a minimum process gate, not evidence of tail-risk or settlement fitness by itself. No parameters are tuned to maximize volatility.

What would change this recommendation: grouping that cannot be verified; insufficient qualified observations under a defensible freshness policy; an estimator that suppresses genuine repricing; concentration that makes feasible venue capacity negligible; or default/roll accounting that cannot deliver the stated exposure. These findings should change the universe, policy or contract design, not be hidden by reweighting or relabelling.

## Relationship to existing work

- [Issue #11](https://github.com/gagattzo/acsi-lab/issues/11): bond data and access. This proposal replaces the assumption that a cross-sectional median alone solves influence and coverage; it does not claim the data task is finished.
- [Issue #12](https://github.com/gagattzo/acsi-lab/issues/12): tenant divergence, retained as a separate research diagnostic.
- [PR #10](https://github.com/gagattzo/acsi-lab/pull/10): CDS eligibility and robustness/lag tradeoff.
- [PR #13](https://github.com/gagattzo/acsi-lab/pull/13): separate Stack proposal. This document neither adopts nor modifies its layers, weights or implementation.

## Primary references

[1] [S&P CDX HY/IG Index Rules, February 2026, Weighting](https://www.spglobal.com/spdji/en/documents/methodologies/sp-cdx-hy-ig-index-rules.pdf).

[2] [ICE: credit futures cash-flow equivalence and credit events](https://www.ice.com/publicdocs/FAQCash_Flow_Equivalence.pdf).

Both checked November 27, 2026. External mechanics are explanatory references; the proposed contract remains unspecified until its own event, roll and venue rules are approved.
