# Production repair, 28 November 2026 — archived first repair

> **Superseded operational report.** This is the earlier repair record from 28 November, preserved as an audit trail. The later strict-decoder release and private replay supersede its headline, test-count and calculation-status statements. Read the [current index manual](current-index-manual.md), [reliability release](reliability-release-2026-11-28.md) and [bond operations](bond-operations-v2.md) for current behavior. At the later release check, the current headline was unavailable; the page displayed the dated 24 November v0.1 edition (+42.6). The private replay produced 14 complete candidate values across 45 dates, while public bond publication remained withheld and fresh collection remained paused.

The bond experiment was incorrectly inserted into CoreWeave's headline calibration without a version change. The repair restores the existing v0.2 CDS headline and preserves the frozen weekly edition. Bond research does not enter that headline. One calculator serves the homepage, share pages and cards. Missing or expired inputs produce an explicit dated weekly fallback. Both rendering exceptions were corrected, including the CDS mini-chart that prevented headline updates; optional chart failures no longer suppress the headline. The share card and page use the same CDS request deadline.

Bond research has its own method version, bond-research-v1. Partial-universe trend claims are withdrawn. At this earlier repair checkpoint, source observation date: 27 November 2026; diagnostic coverage: 24 bonds/19 borrower groups, including six same-session and 18 carried observations. Quality is collection_error, so there is no new verified basket fixing. The old experiment remains separately archived.

## Feed and automation status

The existing collector initialization endpoint returns HTTP 404. FINRA's public bond page loads in the browser, but search requires agreement acceptance; no replacement collection endpoint or automated access has been verified. The website itself must not be described as down.

The former daily collection workflow now validates code on relevant pull requests, pushes and manual dispatch only. Its cron schedules and deliberate failure step have been removed in production and the lab. It has read-only repository permissions, collects no sources and publishes no observations. Scheduling remains paused until private source execution and durable retention pass an end-to-end run. No paid source or purchase is configured.

The internal runner preserves failed-source inputs, actual market dates, independent Treasury progress and explicit quality status. Private raw files/logs live in ignored private_data/. Immutable source archives use AES-256-GCM with an RSA-4096-wrapped random key. Existing archives are saved in AISTRESS_Private_Source_Checkpoint.zip, privately, with the recovery key in the separate private recovery bundle. Neither source archives nor the private key may be committed or uploaded to public workflow artifacts.

Public configuration uses stable IDs and TRACE symbols. Plaintext source files are removed from the current public tree and excluded from Vercel. Earlier public Git commits still contain those files; this repair does not rewrite shared history or revoke copies previously downloaded.

## Remaining release gates

Cancellation/correction lineage and issue-specific yield conventions remain unverified. Default events are persistent and manually maintained; a declared default stops reference publication until a recovery rule is calibrated and approved. The verified market calendar expires after 1 January 2027 and must be extended from SIFMA. Equal borrower weights do not eliminate common tenant exposure. This is research-only: neither the bond series nor the headline is approved here as a perpetual settlement reference.

Historical validation of this first repair: 28 Python regression tests and 12 Node tests passed. The then-deployed decoder displayed +67.8 using Oracle 25 November and CoreWeave 24 November observations. **That is not the current verified reading:** the later strict decoder rejects the ambiguous CoreWeave upfronts in the audited window and withholds the current headline. The dated weekly edition remains visible. These earlier test counts are likewise superseded by the later release evidence.

## Public evidence status

At this first repair checkpoint, all 45 **public** daily rows had null basket levels. The public site therefore supplied no validated basket history or purchase-decision evidence. The later repaired private replay produced 14 candidate values across the same 45 dates; those private calculations are separate from public publication eligibility and do not restore daily collection. Diagnostic input coverage is not a published index reading. On 27 November, the configured filter (at least three customer trades of $100,000 or more) produced six fresh bond marks, with 18 carried; do not describe 23 of 24 as fresh under this filter. Private test findings remain separate from public validation. The homepage shows a compact research-preview state and keeps source diagnostics and the last collected table inside an expandable details panel.
