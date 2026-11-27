# The Stack index

Four layers of the capital structure of AI-infrastructure borrowers (L1 equity, L2 traded credit, L3 private loan
marks, L4 fundamentals), each with its own band, plus Depth: how many layers are at Stressed or worse. The method file
is `stack/stack-v0.yaml`, checked against `schema/stack_method.yaml`. Cutpoints, windows, anchors and weights are
proposed examples, not calibrated; a human freezes them before any backfill is evidence.

```
python -m stack.build --as-of 2026-11-25 [--layers L1,L2] [--offline] [--json out.json] [--weekly]
python -m stack.track [--as-of D] [--history PATH]
python -m stack.track --verify
```

## L1 Equity

Is the equity of AI borrowers, AI-HPC miners and their exposed lenders falling by more than the market, chips, credit,
volatility and rates explain?

Legs (Candidate A in METHODOLOGIES):

- E1 AI borrowers: CRWV, ORCL, NBIS.
- E2 AI-HPC miners: IREN, CIFR, WULF, CORZ, APLD.
- E3 exposed lenders: the equal-weight daily return of ARCC, OBDC, BXSL, FSK and MAIN minus BIZD, as one series. This
  list is a placeholder until the exposure map ranks the top 5 BDCs by AI exposure / NAV.

For each name, on each trading day t:

1. The daily log return of the adjusted close, only between consecutive trading days. A gap or a membership boundary
   gives no return, so an old price never leaks across it.
2. The residual: an OLS with intercept fitted on the previous 126 trading days (day t is never in its own fit), applied
   to day t. Factors: QQQ and SMH returns, HYG minus IEF return (or the change in HY OAS, `factors.credit`), the change
   in VIX (FRED VIXCLS or Yahoo ^VIX, `factors.vix`), the changes in the 2-year and 10-year Treasury yields (FRED DGS2,
   DGS10). The fit needs 120 valid days of the 126.
3. s = minus the sum of the last 5 residuals. A falling residual is stress, so higher s is more stress.
4. z = robust z of s against the name's own previous 252 values (126 needed, else `insufficient_history`).

A name's weight is its leg weight (E1 0.45, E2 0.25, E3 0.30) split equally over the leg's live names. A ticker listed
in `groups` joins that borrower group (ORCL is Oracle, CRWV is CoreWeave); any other ticker is its own group; the E3
spread counts as one group. The 20% group cap, the 2-group rule and junior hysteresis (2 consecutive fresh closes) come
from the shared engine. The layer is withheld when less than 70% of the leg weight is live.

The same calculation on raw returns (no factors) is published next to it as `Z_raw` and raw contributions. It is shown,
not scored.

Availability. An equity close is available on its own day. A FRED observation dated t is treated as available on the
next weekday. The residual for t is computed only once every factor for t is available, so a build for as-of D usually
ends at D-1 for L1: `session` is the last residual date and `available` is the date it could first be computed. The
value is `fresh` when `available` is the as-of date, otherwise `carried` (a carried value cannot move a band). A FRED
blank (a bond-market holiday) counts as a closed market: the change is 0 and the day is flagged.

Membership windows (`membership`, hand-entered from the prior backtest, to be verified against filings): NBIS from 21 Oct
2024 (older Yahoo rows are Yandex), WULF from 14 Dec 2021, CIFR from 30 Aug 2021, APLD from 13 Apr 2022, IREN from 17
Nov 2021, CORZ excluded from 7 Jul to 30 Oct 2025 (the merger-arbitrage window on CRWV).

Sources and files. Prices: Yahoo Finance via yfinance, research use, not republishable. yfinance is optional
(`pip install -r requirements-stack.txt`) and imported only when a download is needed. Closes are cached as
`data/raw/prices/<ticker>.csv`, FRED series as `data/raw/fred/<id>.csv` (both gitignored, sha256 in the build's
inputs). A failed download writes nothing; `--offline` uses the cache only.

Not done yet (each is listed in the layer's `todo` output):

- BTC control for the miners. Yahoo's BTC-USD daily close is at 00:00 UTC, after the US close, so a same-day BTC return
  is a lookahead. `factors.btc: lagged` uses the bar that closed at 00:00 UTC on day t; it is off by default. A BTC
  print at 21:00 UTC needs another source.
- The E3 lender list (above).
- US holidays in the FRED availability rule (the next weekday can be one day early around a holiday).
- A licensed or exchange end-of-day feed: Yahoo has no republication right, so L1 is research only until one exists.

## L3 Private loan marks

Are business development companies (BDCs) marking down the loans they hold to AI-infrastructure borrowers, on more
of them, or letting interest be paid in kind? Scope: the observed BDC book only. Most AI debt sits with private funds,
banks, insurers and project bonds that file no schedule of investments.

Source: the SEC's BDC Data Sets (`soi.tsv` in each `YYYY_MM_bdc.zip`, plus `sub.tsv` for filing dates; see
`research/sources/sec-bdc-datasets.md`). Rows, patterns and net assets come from `research/tools/bdc_exposure.py`,
imported unchanged: current-period rows only, roll-up rows dropped, named obligors vs keyword leads. Files before
2026_08 carry cost and fair value under the labels "Adjusted cost basis" and "Initial fair value of Investment" (the
standard columns are present but empty); `stack/l3_bdc.py` copies them into the standard columns before reading. On the
31 Dec 2025 comparatives in 2026_08 the two agree on 31,743 cost values (208 differ, tranches with identical text).

Review status, from the human rows of `research/bdc/review_log.csv` (an agent's "propose accept" never counts):
`signed off` when the facility or its borrower group has a human accept (the 26 Nov 2026 decision was made at group
level), `rejected` when a human rejected the facility, `candidate` otherwise. Keyword leads are always candidates.
Candidates are listed and counted, never scored. `score_rows: named_candidates` is a comparison run that also scores
named obligors without a sign-off, so the cost of verification is visible; it is labelled in the output.

Facility matching, per holder (CIK), between a vintage and the quarter before it: exact (borrower group, instrument
class, maturity, spread or fixed rate, and the letters of the filer's text, so a moving coupon or share of net assets
does not break it), then terms only, then text only, each fallback used only when it is unique on both sides. Only
funded debt is matched. Unfunded commitments (blank, zero or negative cost and fair value, or "unfunded" in the text),
equity stakes, and facilities whose principal is outside 0.5 to 1.5 times cost (a commitment, a 1,000x scale error, or a
partly drawn line whose cost and fair value mix drawn and undrawn parts) are listed, not matched.

Metrics per borrower group over matched facilities, weighted by current principal (or cost when principal is not near
cost) times the staleness weight of the vintage (full to 120 days after the period end, 0 at 180):

- m1 markdown: mean change in fair value / cost from the prior vintage.
- m2 breadth: share of matched principal below 0.95 of cost.
- m3 PIK: share with a PIK rate above 0, split into PIK at origination (already there in the prior vintage, shown, not
  scored) and PIK added since (scored). The PIK rate is the tag, else a "x% PIK" in the text (labelled).
- m4 non-accrual: not implemented. It is almost never tagged in `soi.tsv` and needs the filing footnotes.
- m0 fair value / cost level: context, weight 0. L3 scores changes and breadth, not levels.

Each metric has an anchor z (proposed examples: m1 a = -0.01 s = 0.01, m2 a = 0.10 s = 0.10, m3 a = 0.05 s = 0.05, m0 a =
0.97 s = 0.03, lower is stress for m0 and m1), so the Watch edge (Z = 0) sits at a 1-point mean markdown, 10% breadth or
5% added PIK. Group z = weighted mean of its metric z (m1 0.5, m2 0.3, m3 0.2). Layer Z = matched-principal group
weights with the 20% cap (equal weights and `cap_infeasible` below 5 groups), the 2-group rule, and senior hysteresis
replayed over monthly release points (each month end and the as-of date). Group values are layer attribution over the
observed BDC book, not a credit view of the borrower.

Point in time. A row is available from its filing's `filed` date: a build for D reads only filings filed on or before D,
and for each holder and period the latest such filing (an amendment applies from its own date). A point with no new
scored filing since the previous point holds the last fresh value (`carried`, it cannot move the band) and only its
`weight_factor` (principal-weighted staleness) falls; at 0 it is `stale`. The monthly zip lags: filings after the last
published file (2026_10 on 27 Nov 2026) are not visible yet, and the output says so (`source_lag`).

Exposure map. Each build writes `data/stack/exposure_map.csv` (first line "# observed BDC book"): per holder, AI book fair
value / net assets, the borrower groups held, cost, fair value, mark date, filing date and link; per borrower group,
holders, facilities, funded principal, cost and fair value. Signed-off, named, funded rows from each holder's latest
filing by D, loans and equity. Facts as filed, no score of any kind.

Register and files. `python -m stack.l3_bdc --register [--as-of D] [--offline]` writes `research/bdc/stack_register.csv`:
one row per holder, facility and period with its match rule, review status and basis, instrument class, funded flag,
match key, prior-vintage match (exact, terms, text, unmatched, new to the holder, no prior vintage) and the prior FV /
cost. It prints the match rate. Zips are cached in `data/raw/sec_bdc/` (gitignored) and hashed into
`manifests/sec_bdc_sha256.csv` in `bdc_exposure.py`'s format; a cached file whose hash differs from the manifest is
flagged (`manifest_match`). Downloads need `SEC_USER_AGENT` (a clear error without it) and are spaced to 8 per second;
`--offline` reads the cache only.

```
SEC_USER_AGENT="<organisation> <contact address>" python -m stack.build --as-of 2026-11-25 --layers L3
python -m stack.l3_bdc --register --as-of 2026-11-25 --offline
```

Not done yet:

- m4 non-accrual (footnote parsing of each filing).
- `weight_factor` is reported but `stack/build.py` does not yet pass it to the senior composite (a carried L3 counts
  at full weight there).
- The companion "AI book FV / cost minus the BDC's whole-book FV / cost" from METHODOLOGIES is not computed.
- Quarterly files before 2025 Q4 (`YYYYqQ_bdc.zip` from 2022 Q4) are not in the month list, so the replay starts in
  February 2026; adding them is one line in the method file once their column labels are checked.
- Anchors and weights are proposed examples. With one or two matched facilities per group, a single facility sets
  its group's reading.

## L4 Fundamentals

Do the filings of AI borrowers and AI-HPC miners show a widening funding gap, more leverage against revenue, or a burst
of debt events?

Issuers (`layers.L4.ciks`, each in one borrower group in `layers.L4.issuer_groups`): CoreWeave, Oracle, Nebius, IREN,
Cipher Mining, TeraWulf, Core Scientific, Applied Digital. Metrics per issuer, higher is more stress:

- f1 funding gap = (debt due within 24 months + capex TTM - operating cash flow TTM) / (cash + undrawn facilities).
  Debt due within 24 months is the maturity schedule (next 12 months + year two) when it is tagged at the period end,
  else current debt plus year two from the latest schedule in the previous year, else current debt only (flagged
  `year_two_missing`, it understates). Undrawn facilities are almost never tagged without a dimension: then cash only,
  flagged `undrawn_missing` (this overstates the gap).
- f2 (debt + finance lease + operating lease liabilities - cash) / revenue TTM. A missing lease tag counts as 0 and is
  flagged (it understates).
- f3 leverage effect of the interest burden: not implemented. It is shown with status `not_implemented` and is not a
  scored metric, so it does not lower any weight.
- f4 count of 8-K and 8-K/A filings in the last 90 days (filing date on or before the as-of date) whose items include
  2.03 or 2.04, from the submissions JSON. Item 1.01 is counted and shown, never scored. An issuer with no 8-K history
  (Nebius files 6-K, which has no item codes) has f4 missing, not zero.

Each metric's z = 0.5 x (x - a) / s + 0.5 x (x - the same metric about 4 quarters earlier) / s, with a and s in the
method file (proposed examples: f1 a 1.0 s 1.0, f2 a 3.0 s 2.0, f4 a 1 s 1). A missing part lowers the metric's weight
(only the level: half weight); it is never filled with a calm value. f1 and f2 carry the engine's staleness weight (full
to 120 days after the period end, 0 at 180, the value itself never moved). Issuer score = mean of its live metric z,
issuer weight = mean of its scored metrics' weights. Group z = issuer-weighted mean. Layer Z = group z with the 20% cap;
the layer is withheld when the live issuer weight is under 70% of the target. The band is replayed over every release
date since `history_start` with senior hysteresis (one new release moves it up) and the 2-group rule. On a day without a
release the value is `carried` and the band cannot move.

Point in time. Every XBRL fact is available from its `filed` date, so a build for as-of D reads only facts filed on or
before D. Per concept and period, the latest value filed by D applies; its date is the first filing that reported that
same value (a comparative column, even rounded to millions, is not new). A restatement applies only from its own filing
date and the original value is kept in the output. TTM: an annual fact ending on the period end, else four consecutive
quarters, a quarter being a 3-month fact or the difference of two year-to-date facts with the same start (Q4 = fiscal
year minus nine months). This covers 10-Q cash flows that are year-to-date only, fiscal years ending in May (Oracle) or
June (IREN) and annual-only filers (TTM at fiscal year ends only). A negative derived quarter of revenue or capex is
dropped: the two year-to-date facts had different scopes.

Tag coverage. companyfacts holds standard-taxonomy facts without dimensions only; extension tags are invisible. Debt is
read from a ladder of recipes in `stack/l4_fundamentals.py`. A partial recipe (convertibles only, or one carrying amount)
is used only if no full recipe exists at that period end and no full recipe in the previous 365 days reported more debt;
otherwise the metric is missing for that period (`debt_tag_gap`). Each issuer's `coverage` and the layer's
`coverage_gaps` list what is live, stale or missing. Known gaps (27 Nov 2026 snapshot): Nebius has one annual period
tagged (2025, from its 20-F), so f1 and f2 are stale and f4 is missing; TeraWulf tags only its convertibles after March
2026; Core Scientific tags no current debt after March 2026; no issuer tags undrawn facilities.

Sources and files. `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` and
`https://data.sec.gov/submissions/CIK##########.json` (plus older submissions pages when the recent block does not reach
back far enough), cached per UTC day as `data/raw/sec/<date>/{companyfacts,submissions,submissions_pages}/` (gitignored,
sha256 in the build's inputs). Each value carries its period end, filing date, accession and filing URL. The User-Agent
is read from the environment variable `SEC_USER_AGENT` (required for any download, never hardcoded); requests are spaced
to at most 8 per second. `--offline` reads the newest cached snapshot and does not need the variable.

```
SEC_USER_AGENT="<organisation> <contact address>" python -m stack.build --as-of 2026-11-25 --layers L4
python -m stack.build --as-of 2026-11-25 --layers L4 --offline
```

Not done yet:

- f3 (rate effect and leverage effect of the interest burden; only the leverage effect would score).
- Undrawn facilities: needs the dimensional facts (the XBRL instance of each filing) or a hand-kept register.
- Nebius quarterly figures: its 6-K results are not tagged; a labelled grade-C path (hand entry from the 6-K) is open.
- Ratios with a small denominator give very large z (Cipher's f2 with revenue TTM of about 146 million against 5.6
  billion of debt; Applied Digital's f1 a year earlier, when cash was 74 million). The engine does not clip by design;
  whether to transform these ratios is a human decision before calibration.
- A revised series for restatements: today a restated value is used from its filing date and flagged; it does not yet
  go to a separate revised series.
