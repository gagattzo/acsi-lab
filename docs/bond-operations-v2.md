# Bond research operations: calculation, collection and publication

For the consolidated, code-checked reference, read the [current index manual](current-index-manual.md). This document provides the component-specific detail.

The calculation and source failure are now separate states. An imported snapshot can produce a private research value without pretending that a successful current collection, public distribution permission or settlement reference exists. This change does not adopt a new economic weighting, carry window or activity threshold.

## Private replay

Run from the repository root, with saved **raw** portal snapshots, the corresponding private universe and a dated Treasury file:

```sh
python harness/bond_spreads.py --private-replay \
  --trades /private/trace_trades_raw.parquet \
  --universe /private/bonds_universe.json \
  --treasury-file /private/treasury/2026.csv \
  --as-of 2026-11-27 \
  --out private_data/replay_20261128
```

This is an explicit calculation run, with no network access or source purchase. A complete candidate retains its calculated value even when source provenance is unverified. Every row and manifest remains `publication_eligible=false`, `perp_eligible=false` and `settlement_eligible=false`. A completed calculation exits zero; inspect `calculation_status` and the per-date coverage before interpreting the result. Partial dates remain null. `--out data` and subdirectories of the public static-data directory are rejected. Never upload private replay output as a public workflow artifact.

The adapter accepts at most one 500-row bounded snapshot per issue, preserves identical-row multiplicity and excludes the entire oldest date when the snapshot reaches that bound. It does not repair already lossy preprocessing. Use the original raw file. It rejects oversized/concatenated inputs until a separately validated adapter exists. It neither reconstructs corrections nor creates point-in-time history. A licensed event feed needs its own validated adapter; matching security identifiers is not enough to certify an interchangeable feed.

The manifest records hashes of the raw tape, universe, Treasury file, dated market calendar, default registry, calculator, snapshot adapter and cleaner. Hashes support reproduction; they do not prove source authenticity or contractual permissions.

## Status contract

| Field | Meaning |
| --- | --- |
| `calculation_status` | Whether the fixed basket could be calculated from the inputs available to this run |
| `collection_status` | What the collector actually reported; `unverified` for an inherited import without a manifest |
| `collection_verification_status` | Whether archived snapshot hashes, issue identities, coverage and every materialized calculation field matched |
| `last_collection_attempt_at` | Start of the latest collection attempt, including failures; null for an unrecorded historical import |
| `last_successful_collection_at` | Time of the last verified successful collection, retained across failures |
| `source_fetched_at` | Successful input acquisition time; a failed attempt is never substituted here |
| `publication_eligible` | The individual observation passes its relevant calculation, provenance and public research rights checks; false never authorizes market values |
| `public_market_data_authorized` | The exporter attests a verified source and a separate scoped rights record; API and static checks also require eligible rows and covered dates |
| `fresh_fixed_weight` / `carried_fixed_weight` | Fraction of the fixed issuer-weighted basket supported by same-session / carried observations |

The calculation layer can retain a previously accepted same-method observation and its original inputs after a failed refresh. The public export independently withholds all bond market values when current source verification or rights authorization fails. Private replay preserves calculated issue and basket values. Retention does not make a carried market level fresh or override publication controls.

## Public research authorization

A successful HTTP response or a `status: ok` string is insufficient. Source verification requires all configured issues, private archived bytes with matching SHA-256 hashes, the correct issue in each snapshot, and an exact match between the archived source and the materialized rows used by the calculator. Changed prices, deleted duplicate rows, mismatched universes, path escapes, missing archives and altered boundary flags block release.

Public derived-research publication additionally requires `--publication-authorization /private/reviewed-rights.json`. No authorization is supplied in the repository. The operator must separately review actual source terms/agreements; the software only validates the record structure and scope. Required nonempty fields are `record_id`, `reviewed_by`, timezone-aware `reviewed_at`, `evidence_reference`, `valid_from`, `valid_through`, `source_id: finra_portal_snapshot`, `method_version: bond-research-v1`, and `scope: public_derived_research`. The exporter checks the anchor date, every historical observation date, and original source dates of carried issue marks against the authorized interval. Public metadata records the authorization status, hash and covered dates; the private review record is not copied into the public output. A boolean, a field inside a downloaded source response, or an authorization for a different use cannot open this gate. This record never confers institutional resale or derivative-settlement rights.

## Public value containment

The public export boundary covers individual prices, yields, spread estimates and basket values, including historical input rows. Without a separately supplied valid rights record and verified source, these numeric fields are null in the static JSON files. `/api/trace` independently checks the same permission metadata and covered dates; a legacy manifest with `publication_eligible=false` cannot expose issue marks. The recursive static validator also rejects numeric marks in public revision archives. New overwritten versions are archived under ignored `private_data/bond_revisions/`.

The 28 November privacy correction removed previously public issue marks and legacy basket values from the current repository tree and deployment source. Before this correction, the controls protected raw archives and complete basket publication but did not protect the individual derived marks; that was an exposure. Original copies were retained privately. A forward commit does **not** erase earlier Git commits, cached responses or old deployment URLs. Those remain a separate remediation task; this change must not be described as a purge of past disclosure.

## Collection and scheduling

The configured collector route remains unresolved after HTTP 404 at session initialization. The public FINRA site loading does not prove an available automated endpoint. No new source success is claimed by this change. Browser agreement acceptance is a separate user decision and would not itself authorize automation.

The bond workflow in each repository remains a code-validation job without a collection cron. The separate lab DTCC scorecard remains scheduled at 22:17 UTC on weekdays. Manual collection still reports actual operational failures with a nonzero exit code and preserves independent successful inputs, logs and encrypted archives. A private replay is a separate command with distinct status; it cannot make an unavailable daily feed appear healthy. Restore scheduling only after an authorized source adapter completes collection, private retention, calculation and failure-recovery checks end to end.

Validation includes source/membership tampering, deletion of identical trades, failure after a prior success, retained-value/input consistency, public/private path separation and a complete private command-line replay. Economic thresholds and default economics remain research policy, not proven manipulation resistance or an approved derivative payoff.
