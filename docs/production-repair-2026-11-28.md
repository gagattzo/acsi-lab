# Production repair, 28 November 2026

The bond experiment was incorrectly inserted into CoreWeave's headline calibration without a version change. Restore the existing v0.2 CDS headline and frozen weekly edition; no bond research value enters it. A shared calculator serves the homepage, share pages and cards. Missing/stale inputs produce an explicit dated weekly fallback. Published weekly editions are unchanged.

The bond research methodology is separately versioned in bond-research-v1.md. Old partial-universe trend claims are withdrawn. The current portal collector cannot initialize its session: its existing public endpoint returned HTTP 404 on 28 November. The official bond page returned 200 but did not establish the required session; its application script was inaccessible. New reference levels therefore remain unavailable. Source data need an operational, authorized source before this can function as a daily bond feed.

## Automation and preservation

Production runs at 23:30 UTC weekdays and retries at 07:00 UTC the following morning. It records missing observations, retains independent inputs, and marks failed runs visibly. The mirrored lab workflow is disabled for collection to avoid competing publishers. The scheduled path has no paid source or purchase.

Private raw files and logs live in ignored private_data/. Public configuration uses stable IDs and TRACE symbols. Immutable source ZIPs are encrypted with AES-256-GCM and an RSA-4096-wrapped random key. The public key enables collectors to append snapshots without possession of the private decryption key. The owner's private recovery bundle contains the key; it must never be committed. Encrypted artifacts are retained even if Git publication fails, and successful updates commit them with public research outputs and revisions.

Plaintext source files are removed from the current public repository and excluded from Vercel builds. They remain in earlier public Git commits; this repair does not rewrite shared repository history or claim those past copies have become private. Historical deletion requires coordinated history cleanup and cannot revoke copies already downloaded.

The source lifecycle status mapping and contract-specific yield convention remain unverified. Default events are persistent, explicit and manually maintained; until a recovery rule is approved, any declared default stops the reference. The verified calendar expires after 1 January 2027; extend from SIFMA before then. The feed is research-only and never settlement-eligible.
