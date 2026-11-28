# AISTRESS: Current Index Manual

**Document version 1.3 | Verified 28 November 2026 | Research implementation**

This manual explains the calculations, data controls and operating behavior that actually exist. It is written for investment, product, risk and engineering reviewers. It does not adopt a new economic methodology or certify a benchmark. The opening sections explain the product; the later sections provide the implementation and operating reference.

**Implementation examined:** website commit `022c39b82e977cced2e00243748de58b4dc98101`; lab commit `09990a69828834852355e61b2fbecce2510cbb37`. The website commit was the READY production deployment at verification. All 79 tracked website files and 90 tracked lab files matched the corresponding local release trees before this documentation update. References in section 14 pin those baseline versions. Version 1.1 additionally documents the bond-publication containment change shipped with this manual; it changes public output controls, not the index arithmetic or CDS decoder. Version 1.2 documents decoder `dtcc-strict-v2`: reported quotes under 10 bp are rejected as reporting errors (section 5), and a shared parity fixture is checked in continuous integration (section 12). It changes which reported quotes are accepted, nothing else: the upfront sign rule, the lifecycle gate, the index arithmetic and the weekly edition are unchanged. Version 1.3 documents decoder `dtcc-attested-v3` and a narrower headline gate. When an unsigned upfront has two feasible readings, the side of the coupon now comes from the name's latest dated public CDS mark (section 5). The headline is withheld only by source problems that can change the CDS levels it uses (section 4). The index arithmetic, the scoring anchors and the weekly edition are unchanged.

The authoritative maintained source is `docs/current-index-manual.md` in the lab repository, mirrored to the website repository. The website HTML and PDF are generated from that same text. Earlier reports remain historical records. If an implementation change alters a rule below, the manual must be reviewed and versioned with that change. A dated status observation is not a promise that a feed will remain in that state.

## 1. What AISTRESS measures

AISTRESS compares stress in selected AI-infrastructure financing with stress in GPU rental economics. It converts each input into a score relative to two chosen anchors: a calm level and a distress level. The headline is the financing score minus the GPU score.

A positive gap means financing is more stressed on these chosen scales. It does not establish that lenders are wrong, that GPUs are mispriced, or that a profitable convergence trade exists. The gap is measured in **score points**, not basis points, default probability or investment return. A credit spread in basis points is a different measurement: 100 basis points equals one percentage point of annual spread.

The financing sample is deliberately narrow. It includes CoreWeave credit, Oracle CDS and selected construction-loan prices. It is not a diversified measure of every AI borrower, all hyperscaler debt or the market value of data-center collateral. The anchors, borrower selection and use of CDS as a bond proxy are economic choices, not facts established by the scoring formula.

There are three separate outputs:

| Output | What it measures | Current role |
| --- | --- | --- |
| Published weekly edition | Five dated inputs scored under the method in force for that edition | Historical publication; 24 November edition is v0.1 |
| Current research headline | CoreWeave and Oracle CDS substituted into the held edition, labelled v0.2 | Revisable calculation; withheld when its inputs fail |
| Private cash-bond basket | Fixed-weight average of reported-yield spreads across 24 issues | Separate experiment; does not enter the headline |

The separate research-fixing writer can freeze a credit-level candidate and its provenance. It does not turn the website gap into a tradable settlement reference.

## 2. Status at verification

The production status endpoint returned `status: unavailable`, `current: null` and `fixing: null`. Its reasons included incomplete CDS source/lifecycle checks and no accepted unambiguous CoreWeave price. The homepage therefore displayed the frozen 24 November weekly edition: debt **57.963**, GPU **15.333**, gap **+42.630**, rounded to **58**, **15**, **+43**. This historical edition is preserved as published; it is not retroactively certified by the stricter decoder.

The former current headline **+67.8** relied on the earlier decoder and is not a validated current reading. In the separate 42-file replay from 1 October through 27 November, all 87 eligible CoreWeave upfront reports had ambiguous payment direction under the simplified model. That finding is scoped to the inspected sample. It is not a statement that every CoreWeave trade is unknowable from every possible source.

**Version 1.3 replay.** The website code of this version was run on the same nine DTCC files (17 to 27 November) that the production endpoint read on 28 November. It returned `status: research` with gap **+67.8** (credit 83.1, GPU 15.3). CoreWeave was 1,373 bp on 24 November, the side of its coupon taken from a 6 November public mark. Oracle was 373 bp on 25 November. Source quality stayed `partial` because of quarantined lineages. The latest quarantined CoreWeave record was executed on 18 November and the latest Oracle record on 8 October, both before the levels used, so they are reported as a note. The same number as the former headline is reached by a different, sourced rule; it remains a research reading, not a validated or settlement value.

The private saved-snapshot bond replay produced 14 complete calculation candidates across 45 dates, covering 6-27 November. The public aggregate bond series remained withheld. A subsequent review found that the public constituent and historical input files still exposed derived market values despite that basket-level restriction; version 1.1 corrects this separate publication failure. On 27 November, six issues were fresh and 18 were carried; only 15.79% of fixed basket weight was fresh. These are retrospective private calculation diagnostics, not proof of a successful daily feed or a public benchmark history. Private values and source archives are separate from this public manual.

Automated bond collection remains paused after HTTP 404 at the configured portal initialization route. The separate DTCC research scorecard workflow remains scheduled. Neither successful software tests nor a replay of saved data establishes successful current collection.

## 3. Published weekly edition: exact arithmetic

For an input value `x`, calm anchor `c` and distress anchor `d`:

```text
raw score u = 100 * (x - c) / (d - c)
weekly v0.1 score = min(100, max(0, u))
financing score F = arithmetic mean of eligible financing scores
GPU score A = arithmetic mean of eligible GPU scores
gap G = F - A
```

The direction of an input is encoded by its anchors. Higher spreads increase stress; lower loan prices or GPU rents increase stress. Weekly financing needs at least two eligible inputs. The GPU side requires H100 input A1. Under this historical weekly method, excluded inputs are removed and the remaining eligible inputs are averaged. This is not the fixed-weight missing-data policy used by the separate bond basket and fixing writer.

| Input | 24 November value | Calm to distress | Weekly score |
| --- | --- | --- | --- |
| F1 CoreWeave 2032 bond spread estimate | 1,190.8 bp | 500 to 1,000 bp | 100.000 |
| F2 Oracle five-year CDS | 365 bp | 100 to 1,000 bp | 29.444 |
| F3 Construction-loan price median | 90 cents per dollar | 98 to 80 cents | 44.444 |
| A1 H100 rental index | $3.10 per GPU-hour | $3.56 to $2.06 | 30.667 |
| A2 B200 rental change | +29.5% | -8% to -40% | 0.000 |

Thus `F = (100 + 29.444444 + 44.444444)/3 = 57.962963`; `A = (30.666667 + 0)/2 = 15.333333`; `G = 42.629630`. Calculations use unrounded numbers; display rounding does not change the calculation.

The gap labels are: at least +15, stress sits in debt; at most -15, stress sits in GPUs; between those boundaries, aligned. Each side is Calm below 25, Elevated from 25 to below 50, Stressed from 50 to below 75, and Distressed at 75 or above. These are score bands, not agency credit ratings.

**Source qualifications for this edition.** F1 derives from a press-reported intraday bond price of 74.9 and a computed yield, with a 20 November Treasury comparison. It is not the private daily bond calculation. F3 is the median of three facility observations, `[85, 90, 90]`; press language such as “below the mid-80s” is encoded at its bound. These are not three verified executable trades. F3's date is the latest included report, 20 November. A2 targets roughly 26 weeks, but the public observations used in this edition span about 21 weeks. H100 and B200 observations are held values from source pages, not continuously licensed feeds.

Weekly freshness is assessed relative to the edition date:

| Input | Labelled fresh through age | Eligible through age |
| --- | --- | --- |
| F1 | 7 calendar days | 14 calendar days |
| F2 | 7 calendar days | 21 calendar days |
| F3 | 14 calendar days | 56 calendar days |
| A1 and A2 | 7 calendar days | 14 calendar days |

F3 includes loan observations within its 56-day window. The archive is not re-aged or rewritten every day. The weekly builder assumes curated, correctly dated edition inputs; it is not a general external-data validator. Original `/e/<date>/` editions disable live fetching and remain historical publications.

## 4. Current headline: the actual v0.2 implementation

The current website substitutes **CoreWeave five-year CDS for F1's bond spread** and takes the latest accepted Oracle five-year CDS for F2. It preserves the financing membership established by the edition, currently F1, F2 and F3, each with nominal weight one-third. It does not substitute the private bond basket when CDS is missing.

```text
open score(x, c, d) = max(0, 100 * (x - c) / (d - c))
F_current = mean(open F1 from CRWV CDS,
                 open F2 from ORCL CDS,
                 held open F3 loan score)
G_current = F_current - held weekly GPU score A
```

There is no upper ceiling on the financing scores. **The deployed website holds the weekly GPU aggregate, which was calculated with the v0.1 ceiling.** It does not apply an uncapped GPU formula again. That distinction matters if future GPU inputs move beyond their distress anchors. The current held inputs happen to give the same GPU value either way.

Both CDS names must have an accepted observation no more than **10 calendar days** old, using UTC dates; age 10 is valid, age 11 is stale. Held loan and GPU observations must already have been observed and remain within their own calendar-day windows. The held H100/B200 observations of 24 November remain within their 14-day window through 8 December and expire on 9 December unless a new edition is supplied.

The deployed CDS endpoint supplies source quality. Since version 1.3 the headline is withheld only by a source problem that can change the values it uses: the latest completed-session CDS level of CoreWeave and of Oracle. It is withheld (fail closed) by an invalid file, a transport error, an unrecognised source flag, a flag without the detail needed to judge it, a file missing before the latest published file (`source_gap`), or more than two unpublished weekdays after it (`source_stale`). A quarantined lineage withholds it when any version of any of its records names CoreWeave or Oracle, or names no reference entity, and was executed on or after the date of that input's level, or has an unreadable execution date (`lifecycle_affects_input`). Up to two weekdays not yet published, provisional intraday data and quarantines that cannot reach the inputs are listed as notes in `/api/index` (`current_session_pending`, `intraday_excluded`, `lifecycle_quarantine_outside_inputs`); the endpoint's overall source flags stay visible. The headline uses completed sessions only: an intraday level never prices it.

Why an earlier quarantine cannot reach an input: a day's level uses only trades executed that day, so a record executed before the level date cannot change it or create a newer level. An orphan's missing ancestor was reported before the first fetched file and therefore executed before it; the 12-day file window covers the 10-day carry, so every level used lies inside the window. A correction that moved an execution date into the window would itself be a fetched record with that date, and it would withhold the headline. A quarantine on an earlier day can still revise earlier chart points: the research series remains mutable.

Both required CDS names and the held inputs must pass; missing inputs do not redistribute the current edition's weights. The shared helper checks source quality when that object is present; direct callers of the helper must not treat an omitted quality object as independent verification.

The request-time timeline uses the union of accepted Oracle/CoreWeave dates. It does not mint a new official daily close on days without new accepted prices. Individual source dates remain visible; a recent aggregate date does not mean both borrowers traded that day. If current availability fails, the helper returns no current timeline. The browser can load earlier editions; `/api/index` supplies the current edition and status, not a comprehensive multi-edition fixing history.

At current three-input financing weights and above the zero-score floor, one extra basis point of CoreWeave CDS adds approximately **0.066667 gap points**; one basis point of Oracle CDS adds **0.037037 gap points**. “CoreWeave is 70%” in earlier analysis described its contribution at particular values, not its nominal weight. Equal score weights do not imply equal sensitivity to raw spreads.

## 5. CDS data: collection, eligibility and price interpretation

### Collection scope and file integrity

The website requests the current UTC date and previous 11 calendar dates, skipping Saturdays and Sundays. This is a bounded weekday window, **not a holiday-aware trading calendar** or a full-market history. A request timeout is 12 seconds. For only the two most recent requested weekdays, a missing/404 cumulative file can trigger a numbered intraday-file fallback. Intraday files remain provisional; a missing interior slice, invalid file or hard scan limit makes the set incomplete. The fallback scans in batches of 40 up to 8,000 files.

The decoder checks ZIP structure, supported compression, expansion bounds, CRC, decoded size, UTF-8, required unique CSV headers, row width and indicator domains. Missing or renamed required fields are an error, not evidence of no trades. A structurally empty file still requires valid headers.

“Complete” source status means the requested files passed the checks within that bounded window. It does not mean every market trade was reported to this repository, every lifetime correction was included, or historical point-in-time availability was verified.

### Trade lifecycle

Dissemination IDs and original IDs are resolved across fetched files before pricing. Identical duplicate reports collapse. Conflicting versions, missing ancestors/roots, cycles, multiple roots or correction branches without a unique timestamp order are quarantined. A valid chain needs one NEWT root. An EROR cancels the linked chain; an old price is not resurrected. NEWT and CORR can supply prices. MODI and TERM are recognized lifecycle actions but do not themselves become a new executed price.

Accepted trades are assigned to execution date. Quality checks cover the three tracked entities, Oracle, CoreWeave and Meta. A Meta lifecycle problem therefore marks overall source quality partial; since version 1.3 it does not withhold the headline, because Meta has no headline weight (section 4). The ledger reports `input_impact`: for every version of every record in a quarantined lineage, and every counted row with an unknown action or no identifier, the latest execution day and the number of unreadable execution days per tracked name, plus `UNIDENTIFIED` for records naming no reference entity. A bounded window can create unresolved ancestry; the implementation does not claim to have reconstructed an indefinitely complete ledger.

### Eligible CDS observations

The price sample requires a tracked corporate single-name senior CDS, USD notional and blank/USD payment currency, at least $1 million disseminated notional, blank/TRAD event type, and no flagged package, nonstandard or post-priced transaction. LX-prefixed loan identifiers and TRS/DDTL names are excluded. The on-the-run five-year maturity is exact: before 20 March, 20 December of year+4; from 20 March through 19 September, 20 June of year+5; from 20 September, 20 December of year+5.

A suspected-basket filter compares eligible tracked names with standard 1% or 5% coupons and nonzero upfronts. Different names with absolute upfront/notional fractions within `0.000001` and execution timestamps within 120 seconds are excluded. This is a conservative heuristic, not proof of package identity. Its false-positive/negative rates have not been independently established.

### Quotes and unsigned upfronts

A present nonzero quoted spread takes priority. Positive values below 0.5 are read as decimal annual spreads; values at least 5 are read as basis points; values from 0.5 to below 5 are rejected as unresolved units. A decoded quote below 10 bp (0.001 as a decimal) is rejected as a reporting error with reason `quote_below_10bp`; 10 bp itself is accepted, and there is no upper ceiling. This quote-quality rule applies only to reported quotes: it is never used to rule out the low-spread root of an unsigned upfront. In the year of history examined, all 34 eligible on-the-run quotes under 10 bp were between 0.81 and 9.0 bp on high-yield or distressed names (lab finding `2026-11-28-decoder-choices-evidence.md`); that supports the rule as a data-quality screen, not as proof that a genuine sub-10 bp trade is impossible. An invalid present quote does not fall through to an upfront estimate. A blank or zero quote is treated as no quoted price.

For upfront conversion, only UFRO payments count; UWIN does not. Blank upfront is missing, while explicit zero is a value. Coupons written as percentages are normalized; only standard 1% and 5% coupons qualify for this conversion. Public notional caps are retained as a limitation; using the disseminated amount does not independently prove the uncapped economics.

The model uses 40% recovery, a fixed 4.5% annual rate with continuous discounting, flat nonnegative hazard, quarterly premium approximation, Act/360 premium/accrual factors and half-period accrual on default. Step-in is one elapsed day after execution. Remaining maturity uses 365.25 days per year. The equation solved is:

```text
protection present value - coupon * risky annuity
    = signed upfront fraction + coupon * accrued fraction
par spread = protection present value / risky annuity
```

The hazard search interval is `[0, 3]`, with 90 bisection iterations and residual tolerance `1e-10`. A numerical boundary is not a market ceiling. A root outside the supported domain is unresolved; it cannot prove the opposite payment direction is correct.

The decoder tests both signs of the absolute upfront/notional fraction. One valid root is accepted when the other direction is economically infeasible within the nonnegative-hazard formulation. Two equivalent roots, within `1e-9` in spread, are accepted. An unresolved numerical/domain case is excluded.

Two distinct valid roots lie on opposite sides of the coupon: above it the protection buyer pays the upfront, below it the buyer receives it. Since version 1.3 (`dtcc-attested-v3`) the side comes from the name's latest dated public five-year CDS mark in the lab's `harness/sign_attestations.json`. The website keeps a byte-identical copy, `api/_sign_attestations.json`, and continuous integration fails if they differ. Each mark is one level stated in a dated, linked report that cites a data provider, such as Markit, ICE, Deutsche Bank or S&P Global data reported by Reuters or CNBC. A mark decides only for its own name and only for trades executed on or after its date. It must be the name's latest mark, at most **21 calendar days** old, and at least **1.5 times** away from the coupon: at least 750 bp or at most 333 bp for a 5% coupon. The two readings must also be at least **2 times** apart. Otherwise the trade remains ambiguous and is excluded with reason `no_public_mark`, `public_mark_stale`, `public_mark_near_coupon` or `readings_near_coupon`. The mark never sets the level: the DTCC trade does. A nearby quote, previous level, agency rating, coupon floor or large payment cannot choose the sign. Daily rows count these trades in `n_sign_attested`; trade rows record `sign_by = attested_side` and `sign_mark_date`. Only CoreWeave has marks.

Evidence and limits (lab finding `2026-11-28-coreweave-public-marks.md`): a rule based on agency ratings was rejected, because investment-grade names often trade five to seven notches tighter than the rating grid implies and it discarded their true readings. Against nine dated public CoreWeave marks with accepted DTCC trades within two days, the higher reading was within -12% to +7% of the mark on eight dates. On the ninth, early June 2026, CoreWeave was below its coupon and the lower reading matched. Using only earlier marks, the rule decided five of those marks, all on the correct side, and abstained on the rest. This is a small sample. The marks are maintained by hand: if none is added within 21 days, CoreWeave stops pricing. A jump across the coupon between marks, for example after a rescue, would be misread until the next mark.

The daily accepted level is the **equal-trade median** of quoted and accepted model-implied spreads, including trades whose side came from a public mark. One accepted trade is sufficient under the current CDS method. The method therefore does not establish independent multi-trade support or resistance to manipulation. Diagnostic counts and ambiguity information can exist even when there is no accepted price.

The website publishes integer-basis-point CDS levels and feeds those integers into the headline. The lab retains two-decimal trade spreads and one-decimal daily levels, and its scoring path uses canonical trades. Decoder agreement under stated tolerances is not bit-for-bit equivalence of every downstream headline or independent validation of the model.

## 6. Private cash-bond basket

### Universe and fixed weights

The configured universe contains 24 issues in 19 legal issuer/SPV groups. Four CoreWeave issues share one group; three Applied Digital issues share one group; the other 17 issues each form a separate group. Every group receives `1/19`; its issues split that weight equally. A CoreWeave issue therefore has weight `1/76`, an Applied Digital issue `1/57`, and a single-issue group `1/19`. A separate comparator gives each bond `1/24`.

The public configuration provides issue aliases/TRACE symbols and group membership. Reproduction additionally requires the privately resolved security identifiers, matching universe and raw source snapshots. Legal issuer grouping is not consolidated tenant, guarantor or common economic exposure. The universe remains fixed until an explicitly versioned reconstitution. Missing issues never transfer their weights to survivors.

### Within-bond mark and Treasury comparison

For a bond/session, the calculator requires at least three rows with reporting party D, contra party C, disseminated face amount at least $100,000, finite volume and reported yield, positive finite price, portal status M/O/N, and blank first/second modifier and special-price flag. Oldest-boundary rows are excluded. There is no customer buy/sell-side split. Negative finite yields are not automatically discarded. The bond filter does not independently apply the CDS USD/seniority rules; instrument selection comes from the universe.

Exactly identical-looking portal rows retain their multiplicity because verified trade IDs and correction linkage are unavailable. Three eligible rows are not proof of three independent trades. Large volume does not give extra median weight. For a snapshot capped at 500 rows, the entire oldest execution date is excluded as potentially partial; the adapter rejects concatenated/oversized snapshots.

```text
remaining maturity in years = calendar days to final maturity / 365.25
bond spread in bp = 100 * (median reported yield - Treasury yield)
basket = sum over groups [1/19 * mean(group's issue spreads)]
```

Both yields in the spread formula are percentage-point values. The Treasury yield is linearly interpolated on the **same trade date's par-yield curve** at remaining final maturity. There is no prior-curve fallback or extrapolation beyond available tenors. Price and yield medians are separate summaries and need not represent one executable trade. This is a reported-yield spread estimate, not option-adjusted spread. Yield-to-maturity/call/worst conventions and amortization require independent instrument validation.

### Calendar, carry and null results

The configured calendar covers 1 January 2026 through 1 January 2027. It uses dated US bond-market closure recommendations and explicit overrides; early-close days still count as sessions. Requests outside the verified range stop. The expected completed session rolls at 18:00 America/New_York, including daylight-saving changes.

A same-session mark has age zero. An eligible spread can be carried unchanged for ages one through five market sessions; age six is stale. Carry retains the original spread and its original Treasury comparison. It does not imply a fresh trade or a new daily rate adjustment. Nevertheless, the basket date must itself have a Treasury row.

A basket exists only with all 24 eligible current/carried issue marks, the required Treasury row and no active declared default. Otherwise the aggregate is null. Issue counts and fixed economic weight are distinct freshness measures. Daily JSON levels round to one decimal basis point; the fixing adapter reconstructs the unrounded weighted value.

The default registry is manual and currently has no automated monitoring. As implemented, **any registry entry dated on or before a basket session blocks the whole basket**, without first filtering issuer-group membership. The state is `default_rule_pending`. There is no approved recovery-to-spread calibration. Blocking preserves the defaulting weight; it does not settle the economic loss.

### Calculation is separate from publication

A private replay can produce values from saved raw inputs while collection remains unverified and publication remains withheld. Private output sets `private_candidate: true`, `publication_eligible: false`, `perp_eligible: false` and `settlement_eligible: false`. It cannot write into the public `data/` directory. Replay files remain mutable until separately frozen.

Public mode requires archive hashes, the full configured membership, acquisition chronology, snapshot identity, counts, boundary metadata and exact materialization of calculation fields. A source response saying “ok” is insufficient. It also requires a separate reviewed rights record for public derived research. No such record is supplied. The public exporter additionally checks authorization against each output date and each carried observation's source date. Individual prices, yields, spreads and aggregate levels are withheld when this check fails. Public diagnostics can remain available without permission to publish market values. The record does not grant institutional resale or derivative-use rights.

A failed public refresh may preserve previously eligible same-method values together with their matching original input rows only when the separate public-market-data authorization checks also pass. New unaccepted candidates are withheld. The public API rejects an accidentally copied private-candidate manifest and independently withholds market values without the required authorization evidence. Static-output validation covers constituent marks, historical inputs and legacy basket files. Source availability, permission, calculation and settlement are separate states.

**Publication incident and scope.** The earlier release left constituent marks and historical research values in public static files and the trace API. They were removed from the current output in the version 1.1 containment release. This forward change does not erase Git history, previously downloaded copies or older deployment URLs. A claim that all historical copies have been withdrawn would require separate verification.

## 7. Frozen research records and corrections

The fixing writer supports `cash_bond_candidate` and `cds_research`, with a weighted-mean calculation only. Positive fixed weights must sum to one within `1e-12`. It freezes observation date, information cutoff, actual publication/record time, method and implementation hashes, constituent values and weights, source hashes, quality and any superseded artifact hash. It does not freeze a supported headline-gap settlement series.

Source and constituent availability cannot be later than the cutoff; constituents cannot precede their sources; publication cannot precede cutoff. The legacy bond adapter uses `local_snapshot_observed`, `point_in_time_verified: false`, `completeness_scope: local_snapshot` and `upstream_feed_complete: null`. A value reconstructed today for an earlier date is retrospective, not a fixing available on that earlier date.

Missing, stale, sign-ambiguous, numerically unresolved, source-incomplete or unresolved-default constituents block the aggregate without changing weights. An unresolved default recorded in a series cannot silently disappear from later dates or correction vintages. Resolving that policy requires a separately versioned series.

Canonical JSON uses sorted keys, compact separators, ASCII escaping, finite values, UTF-8 and a trailing newline. The payload hash excludes its own hash field. Raw bytes and local source paths are not embedded in the fixing; exact inputs, candidate and method are archived separately and privately. The bond adapter verifies source/config/code hashes and checks agreement with the native rounded basket within 0.051 bp.

```text
<output>/<series>/<observation_date>/<sequence>-<sha256>.json
```

The first vintage has no parent. A correction references the latest vintage, has a later publication time and a nondecreasing cutoff. An exact retry returns the same artifact. Forks, missing intermediate vintages, altered hashes and unannounced method changes are rejected. A new method or implementation profile requires a new series ID.

Writes use a series-wide POSIX lock, fsynced temporary file, atomic exclusive hard-link creation and directory synchronization. These are local application controls. They are not signatures, an independently witnessed timestamp, external immutable storage, disaster recovery or an exchange oracle; a privileged directory owner could replace a whole chain. Every research fixing still has `publication_authorized: false` and `settlement_eligible: false`.

## 8. What the website and APIs show

| Surface | Meaning and behavior |
| --- | --- |
| Homepage headline | Current research if eligible; otherwise the dated weekly edition with a reason |
| `/api/index` | Machine-readable current/null, weekly edition, source quality and limitations; HTTP 200 with current, 503 when unavailable |
| `/api/cds` | Dated accepted levels and diagnostics; an individual price may exist while aggregate source quality blocks the headline |
| `/api/trace` | Public bond diagnostics; market values require explicit authorization evidence; refuses private manifests; missing/invalid input returns 503 |
| `/api/og` and `/s/…` | Share rendering using the same headline helper, with weekly fallback; timestamped share links are not frozen fixings |
| `/e/<date>/` | Original dated edition, preserved as published |
| Page JSON download | Weekly reading and reconstruction; not an immutable current CDS fixing |

`/api/index` uses schema `aistress-research-status-v1`. It explicitly returns `mutable_research_series: true`, `fixing: null`, `institutional_use_eligible: false` and `settlement_eligible: false`. `computed_at` is request-time computation, not market execution or successful acquisition time. An unavailable HTTP status with a structured explanation is intentional behavior, not a fabricated zero.

Browser CDS polling is every five minutes, with additional refresh triggers. The CDS API can cache cumulative data for 30 minutes, intraday data for five minutes, and allow stale revalidation for up to 24 hours. `/api/index` caches for 120 seconds. A moving clock or recent poll does not guarantee a fresh trade or a newly collected source. Source dates and quality control interpretation.

The public bond status exposes collection/calculation states and freshness weights in the manifest/status. Its daily row allowlist currently omits the per-date fresh/carried weight fields present in native output. Reviewers needing the full daily freshness panel must inspect the native research output.

## 9. Context panels and historical charts

These displays help interpret the market but do not enter the headline or private bond basket:

| Panel | Calculation and limitation |
| --- | --- |
| Polymarket bankruptcy odds | Bid/ask midpoint where usable, otherwise outcome price; illustrative annual spread uses `-ln(1-p)/years * 0.6 * 10,000`; bankruptcy-only payoff and market odds are not equivalent to CDS |
| Pendle GPU-loan premium | Same-expiry sUSDai vault APY minus plain USDai APY, times 10,000; selection requires vault liquidity of at least $1m and over 60 days remaining, not the same liquidity floor for the comparator |
| Hyperliquid equity perps | Prices for six existing equity-linked markets; these are not an AISTRESS perp launched by this site |
| H100 asking prices | Equal-source median of available Akash, RunPod and optional Vast source estimates; asks/list prices, not executed contracts or a replacement for the held Silicon Data input |
| Recent swap ticker | Recent NEWT reports in publication order; different eligibility/lifecycle scope from the canonical CDS level; never feeds the headline |
| Rating comparison | Fixed reference grid mapped to CDS levels and hardcoded agency references; contextual comparison, not an AISTRESS credit rating or continuously verified agency feed |
| Borrower ladder and BDC holdings | Dated price and filing context; no direct headline weight beyond explicitly identified weekly inputs |

The Pendle panel separately compares vault yield with SOFR after converting APY to simple Act/360 using `360 * ((1+APY)^(1/365)-1)`. This separate number is not the same-expiry vault/plain premium. H100 source quotes are filtered to above $0.30 and below $25 per GPU-hour, summarized within source, rounded to cents and then combined. Market data panels can be thin, stale or unavailable independently of the index.

The “gap in dollars” panel translates the **weekly** financing score onto the H100 anchors: `implied rent = calm + (weekly F/100) * (distress - calm)`. For the 24 November edition this is $2.69056 versus the observed $3.10, a difference of 40.94 cents per GPU-hour, displayed as 41 cents. It is an anchor translation, not fair value, a forecast or a translation of the current CDS headline.

The “flip” chart is a monthly reconstruction using capped H100 stress and the average of capped CoreWeave/Oracle CDS stress, with up to 60 calendar days of carry. It omits construction loans and B200. A crossing drawn between monthly points is interpolated, not an observed event timestamp. It is not the official five-input historical series, a frozen-vintage backtest or the current private bond series. Historical observation grades A-D describe provenance within the edition; “Grade A” there is not an institutional certification of the product.

## 10. Website versus research harness

The lab is a proposal and diagnostic environment. It must not be assumed to reproduce every publication rule merely because it shares decoding and anchors.

| Rule | Deployed headline | Lab v0.2 scoring |
| --- | --- | --- |
| GPU ceiling | Holds capped weekly GPU aggregate | Applies the method's uncapped rule to all inputs |
| Missing financing | Requires both CDS names and valid held inputs; preserves edition membership | Can average remaining financing inputs when at least two exist |
| Fixed observations | Expire under website windows | Count from `as_of` onward without those website expiry checks |
| Earlier history | No current headline before the first usable edition | Can score financing-only history before loan/GPU observations exist |
| CDS precision | Integer-bp API levels | Finer canonical trade precision |

Both default CDS carry limits are 10 calendar days, even though optional lab activity windows use sessions. The default activity rule permits one accepted trade. Other proposed methods may add print counts, session windows or medians of daily medians; their presence in the repository does not make them adopted production policy.

The previous scorecards were calculated before the strict decoder and remain labelled historical. The refresh wrapper requires complete source quality, canonical-file hash agreement and successful scoring of all methods before replacing the accepted scorecard. A blocked attempt preserves prior results and writes a separate last-attempt reason. This prevents incomplete replacement; it does not validate the retained historical numbers under the new decoder.

These differences are documented implementation limits, not silently repaired economic rules. A future convergence of lab and publication behavior requires an explicit reviewed change and new evidence.

## 11. Operating procedures

### Weekly edition

Update the curated source files under the website's `build/`, retain dated source references, and run `python build/make_data.py` to generate `data.js`. Review the edition date, input dates, units, windows, anchors, loan membership and source qualifications. Create the new dated edition directory with the approved `index.html`, `data.js` and every referenced local asset, including `headline.js`; copying only the first two files does not preserve that dependency. Run `node build/editions.js` to rebuild the edition register. Verify the frozen page and current status separately before publication. Do not overwrite an original edition to make later evidence appear contemporaneous.

The repository does not provide a complete institutional maker/checker approval workflow for weekly inputs. Manual source review and release ownership remain operational responsibilities.

### CDS research refresh

From the lab repository:

```sh
pip install -r requirements.txt -r requirements-dev.txt
python harness/refresh_scoreboard.py --collect --days 400
```

Inspect source quality, canonical hashes and `scoreboard/last_attempt.json`, not just process completion. The weekday scorecard schedule is 22:17 UTC. It is separate from the paused bond collection jobs. A green code-validation job does not report a successful market-data update.

**Public CDS marks.** While a name's upfronts have two feasible readings, add a dated public mark for it at least every 21 days, or that name stops pricing. Add one entry to the lab's `harness/sign_attestations.json`: `entity`, `date` (the day the level refers to), `reported_bp`, `provider`, `reported_by` and an `https` link to the report that states the value. Keep one mark per name and date, in date order. Copy the file byte for byte to the website's `api/_sign_attestations.json` in the same change, then rebuild the lab tables. Both decoders reject a malformed file, and continuous integration fails if the two copies differ. Never derive a mark from the index's own output or from an unnamed source.

### Private bond replay and freeze

Install the bond dependencies, then use original saved raw snapshots, the corresponding private universe and a dated Treasury file. The replay/freeze commands do not contact the market-data source or restart collection:

```sh
pip install -r requirements-bonds.txt
python harness/bond_spreads.py --private-replay \
  --trades /private/trace_trades_raw.parquet \
  --universe /private/bonds_universe.json \
  --treasury-file /private/treasury/2026.csv \
  --as-of 2026-11-27 --out private_data/replay

python -m harness.freeze_bond_research \
  --replay-dir private_data/replay \
  --trades /private/trace_trades_raw.parquet \
  --universe /private/bonds_universe.json \
  --treasury /private/treasury/2026.csv \
  --date 2026-11-27 \
  --recorded-at ACTUAL_LOCAL_OBSERVATION_ISO_TIMESTAMP \
  --published-at ACTUAL_RECORD_PUBLICATION_ISO_TIMESTAMP \
  --output-dir private_data/fixings
```

Replace paths and timestamps with the actual inputs and events. The observation date may be historical; the information/publication timestamps must not be fabricated as historical. The private candidate, fixing and `_private_sources` archive stay outside public repositories and static data directories. `published-at` records the artifact's publication into this private research process; it does not mean public distribution is authorized.

Bond collection scheduling should resume only after a supported, authorized adapter completes collection, private retention, calculation and failure recovery end to end. Replacing scraped snapshots with a licensed feed requires a parallel comparison of identifiers, yield conventions, flags, caps, timestamps, corrections and late reports. A different licence alone does not establish identical data semantics.

### Failure handling

For invalid/unavailable DTCC source data, the loader preserves the canonical CSVs. Partial source/lifecycle runs can write partial canonical research CSVs, but the scorecard wrapper preserves the preceding accepted scorecard and records the blocked attempt. Investigate before declaring complete coverage. For ambiguous CDS, retain diagnostics and exclude the price rather than choosing a convenient sign; only a dated public mark can decide the side (section 5). For stale/missing basket inputs, retain weights and return null. For a declared default, record the event and withhold the basket pending an approved event policy. For corrections, append a linked fixing vintage instead of replacing the original. A manual edit cannot clear an unimplemented release gate.

## 12. Verification evidence and its limits

The reliability release passed 214 lab tests, 42 website Python tests and 66 JavaScript tests. Eleven lab full-history tests and one JavaScript full-history test were skipped because the complete canonical history was absent. Separate validation replayed 42 hash-verified DTCC daily files: 59 accepted and 126 diagnostic entity-days agreed between Python and JavaScript under the validator's tolerances. Synthetic scenarios exercised quote/sign selection, corrections, cancellations, source gaps and genuine large repricing.

Version 1.2 adds a shared synthetic parity fixture (`tests/fixtures/dtcc_parity` in both repositories: 48 records in five daily files covering 32 decoding scenarios). The lab rebuilds it and reproduces its expected output. The website's continuous integration decodes it with the JavaScript decoder on every pull request, push and daily run, and fails when its copy differs from the lab's main branch. The full-history comparison needs the source files, which are not in the public repositories, so it runs outside continuous integration; it now fails on an empty or incomplete history instead of passing.

Version 1.3 passed 257 lab tests; five dated regression cases fall outside the rebuilt 400-day window and skip. All 88 JavaScript tests passed with the lab checkout present, none skipped. The lab tables were rebuilt from 286 hash-checked DTCC files (24 October 2025 to 27 November 2026). The JavaScript decoder then reproduced every priced entity-day and diagnostic of that full history, including attested-side counts and the lifecycle `input_impact`, with the full-history test run locally. The parity fixture now has 57 records covering 41 scenarios, including every attested-side branch and the impact summary. The headline gate has its own tests (`tests/headline-gate.test.js`) and was replayed end to end on the nine production files: an unpublished session, a missing interior file, a three-weekday outage and the 10-day carry limit each behaved as specified.

This establishes tested software behavior over those samples. It does not independently validate the simplified CDS model against a standard implementation, prove full-market completeness, establish the cost of manipulating a market, or demonstrate incremental forecasting/hedging value. Historical private bond coverage is not a prospective test. Existing release criteria call for a preregistered forward period of at least 20 trading days; that minimum does not cover rare defaults by itself.

Documentation verification recomputed the archived weekly example from the deployed code, checked method/configuration constants, compared source trees with the deployed/repository commits, and reviewed the differences in section 10. The manual preserves those differences rather than claiming general parity.

## 13. Institutional review and derivative release gates

The present deliverable is a reproducible research implementation with explicit availability controls. Reviewers can assess its economic choices, source scope and failure behavior. Institutional reliance and a tradable perpetual contract still require the following decisions and evidence:

| Gate | Evidence still required |
| --- | --- |
| Source and rights | Supported daily collection; intended distribution/derivative rights; complete, timestamped original vintages; per-date historical authorization checks |
| Instrument mapping | Verified issue, borrower, tenant and guarantor mapping; seniority, calls/amortization and yield conventions; tested CDS/bond proxy basis |
| Model validation | Independent pricing/convention validation with prespecified tolerances, including numerical and distressed cases |
| Market robustness | Influence and outage tolerances; leave-one-out and adversarial tests; real repricing speed; fresh economic weight; forward evidence without retuning |
| Economic rules | Approved weights/reconstitution, rolls, default triggers, recovery observations, post-default treatment, corrections and cessation |
| Publication operations | Named administrator and reviewers, access controls, signed/authenticated releases, retained originals, redundant distribution and incident ownership |
| Perp contract/oracle | Chosen tradable level and payoff; oracle update cadence, stale/outage behavior, funding/mark design, challenges and settlement procedures |

The public gap can fall when GPU stress rises even if financing remains distressed. A proposed credit-risk perp therefore cannot inherit the gap as its payoff without an explicit economic decision. No venue integration, final default payoff or authenticated AISTRESS settlement oracle is implemented by this release. The research-fixing format is groundwork for review, not evidence that these choices have been approved.

## 14. Source map and document maintenance

The following pinned references identify the audited baseline implementation. The version 1.1 bond-publication changes are carried in `harness/bond_publication.py`, `harness/bond_spreads.py`, `harness/validate_bond_publication.py` and the website `api/trace.js` in the same release as this manual; baseline links below do not include that later containment patch. Current repository documentation links to this manual; historical reports and the v0.1 PDF remain labelled for their original scope.

| Topic | Pinned source |
| --- | --- |
| Weekly inputs and server calculation | [Website data.js](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/data.js), [api/_acsi.js](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/api/_acsi.js) |
| Current headline and status | [headline.js](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/headline.js), [api/index.js](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/api/index.js) |
| CDS decoder and lifecycle | [api/cds.js](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/api/cds.js), [api/_dtcc_lifecycle.js](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/api/_dtcc_lifecycle.js) |
| Browser display, history and context | [index.html](https://github.com/gagattzo/Ai-credit-stress-index-/blob/022c39b82e977cced2e00243748de58b4dc98101/index.html), [api directory](https://github.com/gagattzo/Ai-credit-stress-index-/tree/022c39b82e977cced2e00243748de58b4dc98101/api) |
| Lab economic method and scorer | [v0.2-live.yaml](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/methods/v0.2-live.yaml), [score.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/score.py) |
| Bond calculation and provenance | [bond_spreads.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/bond_spreads.py), [bond_provenance.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/bond_provenance.py) |
| Collector and configuration | [finra_trace.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/finra_trace.py), [config directory](https://github.com/gagattzo/acsi-lab/tree/09990a69828834852355e61b2fbecce2510cbb37/config) |
| Research fixing and native adapter | [research_fixing.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/research_fixing.py), [freeze_bond_research.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/freeze_bond_research.py) |
| Scorecard publication and scheduling | [refresh_scoreboard.py](https://github.com/gagattzo/acsi-lab/blob/09990a69828834852355e61b2fbecce2510cbb37/harness/refresh_scoreboard.py), [workflows](https://github.com/gagattzo/acsi-lab/tree/09990a69828834852355e61b2fbecce2510cbb37/.github/workflows) |

Version 1.2 changes are carried in the lab's `harness/dtcc.py` and the website's `api/cds.js` and `api/tape.js`, with the parity fixture and its tests in both repositories; the baseline links above do not include them. Version 1.3 adds `harness/sign_attestations.json` and `api/_sign_attestations.json` (the public marks), `attested_side` in both decoders, `input_impact` in both lifecycle resolvers (`harness/dtcc_lifecycle.py`, `api/_dtcc_lifecycle.js`) and the narrower gate in `headline.js`. When a decoder rule changes, rebuild the parity fixture in the lab and copy it to the website in the same change.

For a future documentation release: compare the implementation, configuration and public status again; update examples and dated status separately from economic rules; resolve or explicitly retain implementation differences; mirror this source; regenerate the HTML/PDF; check links, formula examples and rendering; retain the previous manual version in Git history. A documentation edit must not silently change a methodology or reinterpret an old publication as a current validated fixing.
