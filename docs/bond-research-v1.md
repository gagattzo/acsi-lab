# AI cash-bond research v1

For the consolidated, code-checked reference, read the [current index manual](current-index-manual.md). This document provides the component-specific detail.

This version replaces the mixed-population, volume-weighted bond experiment. It is a **research observation**, not a settlement oracle, credit rating, default insurance price or a substitute for the versioned headline CDS methodology. The old level series is retained in hashed revision files and must not be spliced into this method.

## Fixed universe and aggregation

The configured 24 issues are fixed until an explicitly versioned reconstitution. The four CoreWeave notes form one issuer group; the three Applied Digital notes form one group; the remaining 17 distinct issuers/SPVs each form one group. Every group has weight 1/19. Within a group, each configured issue has equal weight. The comparison basket gives each of the 24 issues weight 1/24. Both baskets use arithmetic means, never medians across borrowers.

Missing issues do **not** redistribute their weight. A basket level exists only when every configured issue has an eligible current or permitted carried observation. Historical partial baskets produce null levels. Historical changes can only compare complete levels of this same method and universe. A difference between equal-bond and equal-issuer series is not a pure CoreWeave factor: other issuer weights differ too. Legal-issuer diversification does not eliminate common tenant, guarantor or economic exposure.

## Within-bond observation

Take the median reported yield and median reported price among same-session customer trades with reporting party D and contra party C, disseminated face amount at least $100,000, finite yield and positive finite price. Require at least three eligible prints. These are initial research quality parameters, not empirically established manipulation-resistance guarantees. A large printed trade receives no extra weight. Dissemination volume caps therefore do not distort median weighting. Any nonempty modifier or special-price flag is excluded conservatively, including primary-market and weighted-price trades. Extreme yields are not discarded simply because they exceed 50%.

Portal M/O/N values are retained as snapshot statuses; they are **not** treated as FINRA lifecycle cancellation codes. The public portal does not supply verified trade identity and correction linkage here. Duplicate-looking rows may be different trades. These limitations prohibit a settlement-quality claim even when coverage is 24/24. The oldest date in a truncated 500-row snapshot is excluded because it may contain only a partial session. No earlier observations are invented or filled backward.

For each issue, subtract the linearly interpolated **same-date** US Treasury par yield at remaining final maturity from the median reported yield, and multiply the percentage-point difference by 100 for basis points. There is no prior-curve fallback or extrapolation outside curve tenors. This is a reported-yield spread estimate: it does not independently establish whether each reported yield is yield-to-maturity, yield-to-call or yield-to-worst. Callable bonds and project amortization require offering-document validation before settlement use. It is not OAS. Median yield and price are separate summaries and need not correspond to a single executable trade.

## Freshness and publication

The calendar uses explicitly dated SIFMA US bond-market recommendations in `config/bond_calendar.json`, including Columbus Day and Veterans Day. Good Friday 3 April 2026 is an early-close session, not a full holiday. Early-close dates remain sessions. Dates outside the verified calendar range stop the calculation; extend the dated calendar from the official SIFMA source before that limit. The expected completed session rolls at 18:00 America/New_York, allowing end-of-day collection. Emergency closures require a dated `closed_dates` or `open_overrides` configuration update. All ages count market sessions, independently of whether any constituent traded that day.

Carry an eligible spread for at most five sessions, with the original source date and a carried flag. Carrying keeps the prior spread, including its prior Treasury comparison; it is not repriced against the current curve and does not imply a new trade. The basket date nevertheless requires a same-date Treasury row. Beyond that age, publish a gap. Freeze basket calculation for any missing same-date Treasury curve. Newly downloaded Treasury years are refreshed on every run, requested dynamically across the observation range, retried once, and archived by content hash; old inputs remain available independently if a source fails. Actual source date, expected session, generation timestamp, coverage, carried count, and quality are distinct manifest fields. API request time must never impersonate market observation time.

Prior same-method published dates earlier than the current bounded source window are retained. Dates within the observed window are recomputed; prior public JSON versions are archived by hash. This preserves research history while explicitly avoiding a claim to an indefinitely complete transaction tape.

## Default policy and release gate

There is **no approved recovery-to-spread calibration**. Inventing one now would create arbitrary derivative economics. A declared default in persistent `config/default_events.json` (`events` list with `issuer_group` and `default_date`) stops the basket from that date with `default_rule_pending`; the defaulting weight is not deleted or redistributed. As currently implemented, any dated event in the registry blocks the basket, without an issuer-membership filter. The registry is a manual operational input, not automatic event detection. Before a tradable reference can launch, publish a separately versioned event policy specifying trigger authority, recovery observation, fixing equation and parameters, event persistence through reconstitution, challenges and revisions. A missed manual event is a remaining operational risk; this research series has no verified automated default-event feed.

Settlement eligibility is always false in this research version. A complete research date is not authorization to settle a perp. Required release gates include verified trade lifecycle handling, contractual yield conventions, independently reviewed constituent/tenant mapping, empirically tested minimum activity thresholds, default rules, source freshness and outage simulations, publication consistency and independent calculation verification.

## Operational separation

The executable private replay, verified snapshot gate, separate public-research rights record, and collection/calculation timestamps are specified in [bond operations v2](bond-operations-v2.md). These controls do not change the basket economics. Private candidates may have values while public history remains withheld.
