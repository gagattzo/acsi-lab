# Production repair, 28 November 2026

The bond experiment was incorrectly inserted into CoreWeave's headline calibration without a version change. The repair restores the existing v0.2 CDS headline and preserves the frozen weekly edition. Bond research does not enter that headline. One calculator serves the homepage, share pages and cards. Missing or expired inputs produce an explicit dated weekly fallback. Both rendering exceptions were corrected, including the CDS mini-chart that prevented headline updates; optional chart failures no longer suppress the headline. The share card and page use the same CDS request deadline.

Bond research has its own method version, bond-research-v1. Partial-universe trend claims are withdrawn. Current source date: 27 November 2026; diagnostic coverage: 24 bonds/19 borrower groups, including six same-session and 18 carried observations. Quality is collection_error, so there is no new verified basket fixing. The old experiment remains separately archived.

## Feed and automation status

The existing collector initialization endpoint returns HTTP 404. FINRA's public bond page loads in the browser, but search requires agreement acceptance; no replacement collection endpoint or automated access has been verified. The website itself must not be described as down.

The production workflow is scheduled for 23:30 UTC weekdays and 07:00 UTC the following morning. It runs regression checks, then deliberately stops before collection in the public repository: private source execution and durable retention must be configured first. Daily collection is not operational. The mirrored lab workflow does not collect, avoiding competing publishers. No paid source or purchase is configured.

The internal runner preserves failed-source inputs, actual market dates, independent Treasury progress and explicit quality status. Private raw files/logs live in ignored private_data/. Immutable source archives use AES-256-GCM with an RSA-4096-wrapped random key. Existing archives are saved in AISTRESS_Private_Source_Checkpoint.zip, privately, with the recovery key in the separate private recovery bundle. Neither source archives nor the private key may be committed or uploaded to public workflow artifacts.

Public configuration uses stable IDs and TRACE symbols. Plaintext source files are removed from the current public tree and excluded from Vercel. Earlier public Git commits still contain those files; this repair does not rewrite shared history or revoke copies previously downloaded.

## Remaining release gates

Cancellation/correction lineage and issue-specific yield conventions remain unverified. Default events are persistent and manually maintained; a declared default stops reference publication until a recovery rule is calibrated and approved. The verified market calendar expires after 1 January 2027 and must be extended from SIFMA. Equal borrower weights do not eliminate common tenant exposure. This is research-only: neither the bond series nor the headline is approved here as a perpetual settlement reference.

Validation: 28 Python regression tests and 10 Node tests pass. Browser verification confirmed the dated CDS headline and share card both display +67.8, using Oracle 25 November and CoreWeave 24 November observations; the bond quality panel is visible. Third-party cached social previews may retain an earlier image until refreshed.
