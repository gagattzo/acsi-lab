# Immutable research fixings v1

This release adds an executable fixing record, separate from the request-time website reading. It does **not** launch a tradable benchmark. All artifacts have `publication_authorized: false` and `settlement_eligible: false`. Private cash-bond candidates and CDS research use distinct `series_kind` and `series_id` values; the headline gap is not silently inserted into a credit-level series.

## What is frozen

`harness/research_fixing.py` records the observation date, information cutoff, publication timestamp, method ID/version/implementation-profile hash, sanitized constituent values and fixed weights, source snapshot hashes, calculation quality, and any superseded artifact hash. All times are explicit arguments; the core builder does not fetch data or read the system clock. JSON is serialized with sorted keys, finite numbers and a specified UTF-8 representation before hashing.

The method independently fixes the full constituent set and positive weights summing to one. Only a weighted mean is supported. Missing constituents retain their original weight and make the aggregate null. Constituents whose sign is ambiguous, whose solver failed, whose source is incomplete, or whose credit event lacks a rule also produce null. The method defines the carry limit in calendar days or an explicitly supplied session calendar. Older observations are marked carried automatically, then stale when the limit is exceeded. A carried observation is not a new market print.

Source and constituent availability timestamps must not exceed the cutoff. Constituents cannot be available before their source snapshots, and their observed market dates cannot follow the fixing date. Publication cannot precede the cutoff. These checks detect inconsistent timestamps; they do not independently authenticate a collector's timestamp claims.

A successful calculation does not establish release readiness. This version always records outstanding gates for data rights, independent model validation, manipulation/liquidity policy, credit-event/recovery policy, publication/revision governance, and oracle/dispute operations. Supplying a minimum print count or a caller-written approval flag cannot turn those gates green. There is no invented default recovery-to-spread equation: a declared unresolved event blocks calculation and preserves all weights. Once a constituent has an unresolved default in a frozen vintage, later dates and corrections in that series cannot silently clear it; an approved resolution/retraction policy needs a separately versioned series.

## Append-only vintages

Artifacts are stored under:

```
<output>/<series_id>/<observation_date>/000001-<artifact_sha256>.json
```

The first record has `supersedes: null`. A correction is a new numbered file referencing the latest artifact hash, with a strictly later publication time and a nondecreasing cutoff. The original stays unchanged. Exact retries return the original path. Unannounced rewrites, revision forks, missing intermediate vintages, bad hashes and method changes within a series are rejected. A method or implementation-profile change requires a new series ID so history cannot be spliced accidentally. The bond profile includes calculator/cleaner/fixing code hashes and the configured trade-size, print-count and carry rules; changing a calculation rule cannot silently enter the existing series.

Writes use a POSIX advisory lock across the series, an fsynced temporary file and atomic exclusive creation; an existing file is never replaced. This is an application-level append-only record on a local filesystem, **not** a digital signature, externally witnessed timestamp, WORM store, disaster-recovery service or authenticated public oracle. A privileged actor controlling the directory can rewrite an entire chain. Independent archival/signing and distribution are future release work.

## Core command

```
python -m harness.research_fixing \
  --candidate private_data/candidate.json \
  --method private_data/fixing-method.json \
  --source tape=private_data/source-snapshot.csv \
  --output-dir private_data/fixings
```

Use `--supersedes <latest-artifact-sha256>` only for a new correction vintage. Keep source files and generated artifacts private unless separately authorized. Output JSON contains source IDs and content hashes, never the source bytes or their local paths. Unknown fields are rejected rather than copied into the output. IDs must be public-safe aliases; the caller is responsible for choosing aliases that do not disclose private security identities.

Minimal method shape (synthetic illustration, not an adopted methodology; replace the dummy implementation hash with the actual frozen implementation/profile digest):

```json
{
  "id": "example-credit-level",
  "version": "v1",
  "series_kind": "cds_research",
  "unit": "bp",
  "aggregation": "weighted_mean",
  "implementation_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "constituents": [{"id": "name_a", "weight": 1.0}],
  "freshness": {"basis": "calendar_days", "maximum_age": 10}
}
```

Minimal candidate shape:

```json
{
  "series_id": "private-example-v1",
  "observation_date": "2026-11-27",
  "cutoff_at": "2026-11-28T12:00:00Z",
  "published_at": "2026-11-28T12:05:00Z",
  "sources": [{
    "id": "tape",
    "available_at": "2026-11-28T11:00:00Z",
    "complete": true,
    "point_in_time_verified": false,
    "availability_basis": "local_snapshot_observed",
    "completeness_scope": "local_snapshot",
    "upstream_feed_complete": null
  }],
  "constituents": [{
    "id": "name_a", "value": 400,
    "observed_date": "2026-11-27",
    "available_at": "2026-11-28T11:00:00Z",
    "status": "accepted", "source_ids": ["tape"]
  }]
}
```

`complete` applies only to its stated `completeness_scope`. For a local snapshot, `upstream_feed_complete` must be null and an explicit upstream-completeness blocker is recorded. It does not prove that a capped public portal snapshot contains the full historical market tape. `point_in_time_verified` remains a collector claim; a hash cannot establish past availability. A local snapshot observed now must use `availability_basis: local_snapshot_observed` with `point_in_time_verified: false`.

## Native private bond replay integration

Run the native private replay first, then freeze one observation:

```
python -m harness.freeze_bond_research \
  --replay-dir private_data/replay \
  --trades private_data/trace_trades_raw.parquet \
  --universe private_data/bonds_universe.json \
  --treasury private_data/treasury.csv \
  --date 2026-11-27 \
  --recorded-at 2026-11-28T12:00:00Z \
  --published-at 2026-11-28T12:05:00Z \
  --output-dir private_data/fixings
```

Replace the example timestamps with the actual time the local inputs were observed together and the publication time. `recorded-at` must follow replay generation. It is deliberately **not** named `source_fetched_at`: the legacy snapshot has no verified acquisition timestamp, and the artifact does not manufacture one. A historical observation frozen today remains a retrospective research vintage.

The adapter requires an explicitly private replay with the native input/code provenance manifest. It verifies the raw, universe, Treasury, calendar, default-registry and calculator/cleaner hashes, reconstructs the full configured universe and issuer weights, uses the configured session calendar, checks the default registry, and compares its aggregate with the native one-decimal replay value (within 0.051 bp for rounding). Private security IDs and issuer names are not emitted. All source files, replay outputs and relevant calculator/configuration files are hashed. Commands preserve the exact source bytes plus canonical candidate and method JSON in a private content-addressed store under `<output-dir>/_private_sources`, or `--source-archive-dir`. Existing source copies are rehashed, changed input files are rejected, and source bytes never enter the fixing JSON. Keep this directory private; it must never be served with a public website or committed to a public repository. API callers must invoke `archive_sources` explicitly or pass `source_archive_dir` to the native adapter. The resulting value is a fixed-weight research level; this reconstruction is not independent economic validation of the spread model.

The replay and fixing are separate commands so a failed attempt cannot replace a previously frozen value. Neither command enables daily collection or repairs a source endpoint by itself.

## Verification

```
python -m pytest -q tests/test_research_fixing.py tests/test_freeze_bond_research.py
```

Tests cover deterministic hashing, source mismatch, unknown/private fields, missing fixed weights, stale sessions and holidays, future information, ambiguous pricing, unresolved defaults, incomplete sources, idempotence, immutable originals, ordered corrections, forks, tampering, deleted vintages, method changes and native replay consistency. These are software-contract tests, not a liquidity backtest or proof of resistance to market manipulation.
