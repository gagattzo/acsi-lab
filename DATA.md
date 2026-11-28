# Data pipeline

Read the [current index manual](docs/current-index-manual.md) first for product scope, exact calculation rules and operational status. This page describes the lab pipeline; it does not make every historical scorecard current.

The lab's rule: **agents change the index only through data the pipeline has decoded, tested and hashed.** A method file combines pipeline features; it cannot run code. Opinions don't move the number; loaders, tests and scorecards do.

```
raw files            data/raw/dtcc/YYYY-MM-DD.zip          exactly as DTCC published them
  │  sha256          manifests/dtcc_sha256.csv              conflicts logged; originals retained
  ▼
loader               harness/dtcc.py                        cleaning rules, one test each in tests/
  ▼
canonical tables     data/canonical/cds_trades.csv          one row per priced trade   (schema/cds_trades.yaml)
                     data/canonical/cds_daily.csv           one row per entity-day     (schema/cds_daily.yaml)
  │  validate        harness/validate.py                    types, ranges, blanks, unique keys
  ▼
features             schema/method.yaml                     what a method may use: dtcc:<ENTITY>, min_trades, pool_days, max_carry_days
  ▼
method               methods/*.yaml                         inputs, anchors, sides; no code
  ▼
scorecard            harness/score.py                       independence, movement, manipulation, freshness,
                                                            stability by half, forward test, licence
```

## Run it

```
pip install -r requirements.txt -r requirements-dev.txt
python harness/dtcc.py --days 400                 # download, hash-check, clean, write the canonical tables
python harness/validate.py schema/*.yaml           # check the tables against their schemas
python -m pytest -q tests                          # every known quirk of the data has a test
python harness/score.py methods/v0.1-live.yaml     # score a method
```

## Why each guard exists

| Guard | What it stops |
| --- | --- |
| Raw-file hashes, committed | Someone, or a republished file, quietly changing history |
| One test per cleaning rule | A fix for one quirk silently undoing another |
| Schemas with explicit types and finite/nonnegative spreads | Malformed tables, NaN, infinities and negative spreads are rejected. There is no empirical 10/100 bp floor or arbitrary economic spread ceiling; payment direction is checked by model feasibility |
| Source-quality sidecar with canonical hashes | Partial source windows, quarantined lifecycles or mismatched CSVs cannot replace the accepted scoreboard |
| Explicit unsigned-upfront ambiguity | A nearby quote or previous mark cannot choose between two valid model prices |
| Features only, no code in methods | Heuristics nobody can check, and code that runs on the maintainers' machines |
| Scores on daily changes, not levels | Two trending series always look correlated |
| Stability by half | A method fitted to one stretch of history |
| Forward test | Only days after registration count toward the normal 20-trading-day adoption gate. Historical reconstruction is not forward evidence. The earlier v0.2 adoption did not complete this gate; see the current manual |
| Licence check | Building on data we may not publish |

## Known quirks

| Quirk | Status | Test |
| --- | --- | --- |
| A blank upfront read as 0 priced trades at their coupon | fixed 26 Nov 2026, also on the live site | `test_blank_upfront_is_missing_not_zero` |
| Coupon-dependent spread floors were treated as payment-direction proof | replaced by strict root/domain checks; ambiguous two-root rows are excluded | `tests/test_dtcc_pricing_quality.py` |
| Coupons written in percent (1 = 1%) drop the trade | fixed 26 Nov 2026 (issue #7): 48 Oracle and 9 Meta trades recovered | `test_percent_coupon_is_normalised` |
| Unwind payments (UWIN) read as upfronts | fixed 26 Nov 2026: 26 Oracle trades removed | `test_unwind_payment_is_not_an_upfront` |
| Quoted spreads sometimes in raw bp (365, not 0.0365) | handled; 1 Meta record in a year | `test_quoted_spread_in_raw_bp_is_read_as_bp` |
| Reported quotes under 10 bp (34 in a year, all 0.81–9.0 bp on high-yield names, CoreWeave at 1.2 bp among them) | rejected as reporting errors since `dtcc-strict-v2`; no ceiling; never applied to an upfront's roots | `test_quote_under_10bp_is_a_reporting_error_with_no_ceiling`, `test_quote_floor_never_rules_out_an_upfront_low_root` |
| A failed download cached as an empty file, never retried | fixed 26 Nov 2026: only valid ZIPs are written | `test_failed_download_writes_nothing_and_is_retried` |
| A republished raw file stops the run | now a logged conflict: original kept, day left out | `test_republished_file_is_a_logged_conflict` |
| Capped notionals (`5,000,000+`) | Flagged per day (`n_capped`); code divides the reported upfront by the displayed notional. Historical capped/uncapped comparisons do not prove that this ratio has the correct semantics for every record | `test_capped_notional_is_counted` |

Earlier full-year profiling found no quoted on-the-run CoreWeave 5-year print. In the strict 42-file replay (1 October–27 November), all 87 eligible CoreWeave upfronts were ambiguous and no CoreWeave daily level was accepted. An upfront-derived level can exist only after the strict direction checks pass under the simplified model (flat hazard, 40% recovery, fixed 4.5% discount rate). `cds_daily.official_kind` distinguishes accepted quoted, upfront-implied and mixed levels; it is a legacy field name, not benchmark approval. Earlier P0 results remain at `research/findings/2026-11-26-p0-tape-hygiene.md`.

Field-by-field decoding of the raw file: `research/sources/dtcc-sbsr-credits.md`.

## Website and lab scope

The lab scorer is a historical/proposal harness, not the production headline publisher. In the current implementation, lab v0.2 removes the upper ceiling for all inputs, permits financing re-averaging when at least two inputs remain, and has no post-edition expiry for fixed inputs. The website instead preserves the edition's financing membership, checks held-input expiry and uses the capped weekly GPU score. CDS decoder parity tests do not establish general scoring-method parity. These differences are documented in the [current manual](docs/current-index-manual.md); a full institutional reconciliation remains a release gate.

## Current quality and revision contract

`cds_source_quality.json` records the decoder version, source attempt time, requested/available/missing dates,
lifecycle quarantine and pricing exclusions. After both canonical files are written it binds them by SHA-256.
Incomplete source windows remain explicitly incomplete, even when some research prices can be calculated.
The scoreboard runner requires complete source status and matching canonical hashes, and publishes all methods
together; a blocked run preserves the preceding scoreboard and writes a separate attempt record.

The request-time CDS series is revisable research, not an immutable fixing. The separate
[research-fixing contract](docs/research-fixings-v1.md) freezes a candidate, method and input hashes at explicit
cutoff/publication times; subsequent corrections are linked vintages, never overwrites. Neither hashing nor an
old observation date proves point-in-time availability. Institutional and settlement eligibility remain false.

## Adding a source

One pull request with all five, or it is not merged:
1. **Decoding:** `research/sources/<id>.md`, every field the loader reads.
2. **Loader:** `harness/<id>.py`, raw files to `data/raw/<id>/`, hashes to `manifests/<id>_sha256.csv`.
3. **Schema:** `schema/<table>.yaml` for each canonical table it writes.
4. **Tests:** one per cleaning rule and per known quirk, on synthetic rows; plus anchors against published values.
5. **Feature:** the new `source:` prefix in `schema/method.yaml` and `harness/score.py`, and the source's terms in `sources.yaml`.
