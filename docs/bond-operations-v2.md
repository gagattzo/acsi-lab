# Bond research operations: calculation, collection and publication

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
| `publication_eligible` | Current output passes calculation, provenance and a separately recorded public research rights gate |
| `fresh_fixed_weight` / `carried_fixed_weight` | Fraction of the fixed issuer-weighted basket supported by same-session / carried observations |

In public mode a source or rights failure nulls a new candidate. It does not overwrite a previously accepted same-method observation or pair that old value with newly recomputed inputs. Original accepted input rows and source dates are retained together, and the failed refresh is flagged separately. This retention is not a declaration that an old market level remains fresh.

## Public research authorization

A successful HTTP response or a `status: ok` string is insufficient. Source verification requires all configured issues, private archived bytes with matching SHA-256 hashes, the correct issue in each snapshot, and an exact match between the archived source and the materialized rows used by the calculator. Changed prices, deleted duplicate rows, mismatched universes, path escapes, missing archives and altered boundary flags block release.

Public derived-research publication additionally requires `--publication-authorization /private/reviewed-rights.json`. No authorization is supplied in the repository. The operator must separately review actual source terms/agreements; the software only validates the record structure and scope. Required nonempty fields are `record_id`, `reviewed_by`, timezone-aware `reviewed_at`, `evidence_reference`, `valid_from`, `valid_through`, `source_id: finra_portal_snapshot`, `method_version: bond-research-v1`, and `scope: public_derived_research`. The observation must fall in the authorized date interval. The public manifest exposes only the authorization status and hash. A boolean, a field inside a downloaded source response, or an authorization for a different use cannot open this gate. This record never confers institutional resale or derivative-settlement rights.

## Collection and scheduling

The configured collector route remains unresolved after HTTP 404 at session initialization. The public FINRA site loading does not prove an available automated endpoint. No new source success is claimed by this change. Browser agreement acceptance is a separate user decision and would not itself authorize automation.

Both repository workflows remain code-validation jobs without collection cron schedules. Manual collection still reports actual operational failures with a nonzero exit code and preserves independent successful inputs, logs and encrypted archives. A private replay is a separate command with distinct status; it cannot make an unavailable daily feed appear healthy. Restore scheduling only after an authorized source adapter completes collection, private retention, calculation and failure-recovery checks end to end.

Validation includes source/membership tampering, deletion of identical trades, failure after a prior success, retained-value/input consistency, public/private path separation and a complete private command-line replay. Economic thresholds and default economics remain research policy, not proven manipulation resistance or an approved derivative payoff.
