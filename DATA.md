# Data pipeline

The lab's rule: **agents change the index only through data the pipeline has decoded, tested and hashed.** A method file combines pipeline features; it cannot run code. Opinions don't move the number; loaders, tests and scorecards do.

```
raw files            data/raw/dtcc/YYYY-MM-DD.zip          exactly as DTCC published them
  │  sha256          manifests/dtcc_sha256.csv              committed; a changed file stops the run
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

The Stack index (`stack/`) reads these canonical tables for its L2 layer and adds SEC, FRED and price sources with their
own availability rules; its sources, caches and hashes are described in [docs/stack.md](docs/stack.md).

## Why each guard exists

| Guard | What it stops |
| --- | --- |
| Raw-file hashes, committed | Someone, or a republished file, quietly changing history |
| One test per cleaning rule | A fix for one quirk silently undoing another |
| Schemas with ranges | Garbage reaching the median. The schema check caught a 0.01 bp "spread" on CoreWeave, 6 Feb 2026 |
| Features only, no code in methods | Heuristics nobody can check, and code that runs on the maintainers' machines |
| Scores on daily changes, not levels | Two trending series always look correlated |
| Stability by half | A method fitted to one stretch of history |
| Forward test | Tuning to the year everyone can see. Only days after a method is registered count, and adoption waits for 20 of them |
| Licence check | Building on data we may not publish |

## Known quirks

| Quirk | Status | Test |
| --- | --- | --- |
| A blank upfront read as 0 priced trades at their coupon | fixed 26 Nov 2026, also on the live site | `test_blank_upfront_is_missing_not_zero` |
| The impossible sign of an unsigned upfront could be taken | fixed 26 Nov 2026, also on the live site | `test_infeasible_upfront_reading_is_never_taken` |
| Coupons written in percent (1 = 1%) drop the trade | fixed 26 Nov 2026 (issue #7): 48 Oracle and 9 Meta trades recovered | `test_percent_coupon_is_normalised` |
| Unwind payments (UWIN) read as upfronts | fixed 26 Nov 2026: 26 Oracle trades removed | `test_unwind_payment_is_not_an_upfront` |
| Quoted spreads sometimes in raw bp (365, not 0.0365) | handled; 1 Meta record in a year | `test_quoted_spread_in_raw_bp_is_read_as_bp` |
| A failed download cached as an empty file, never retried | fixed 26 Nov 2026: only valid ZIPs are written | `test_failed_download_writes_nothing_and_is_retried` |
| A republished raw file stops the run | now a logged conflict: original kept, day left out | `test_republished_file_is_a_logged_conflict` |
| Capped notionals (`5,000,000+`) | flagged per day (`n_capped`); the upfront is reported on the cap, no bias found | `test_capped_notional_is_counted` |

CoreWeave has no quoted on-the-run 5-year print in the year: every CoreWeave level is implied from upfronts with a model
(flat hazard, 40% recovery, 4.5% rate). `cds_daily.official_kind` says so on every row. What the P0 fixes changed:
`research/findings/2026-11-26-p0-tape-hygiene.md`.

Field-by-field decoding of the raw file: `research/sources/dtcc-sbsr-credits.md`.

## Adding a source

One pull request with all five, or it is not merged:
1. **Decoding:** `research/sources/<id>.md`, every field the loader reads.
2. **Loader:** `harness/<id>.py`, raw files to `data/raw/<id>/`, hashes to `manifests/<id>_sha256.csv`.
3. **Schema:** `schema/<table>.yaml` for each canonical table it writes.
4. **Tests:** one per cleaning rule and per known quirk, on synthetic rows; plus anchors against published values.
5. **Feature:** the new `source:` prefix in `schema/method.yaml` and `harness/score.py`, and the source's terms in `sources.yaml`.
