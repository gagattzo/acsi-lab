# DTCC public swap data: SEC credit file

**Id in sources.yaml:** `dtcc` · **Status:** strict research loader in harness (`harness/dtcc.py`) · **Decoder correction:** 2026-11-28 (`dtcc-strict-v1`). The sample counts below are historical profiling results, not a fresh audit of the full tape.

## What it is
DTCC is a security-based swap data repository. Under the SEC's Regulation SBSR, dealers report every single-name CDS trade, and the repository must publish it free of charge. This is the only free, transaction-level source of CDS prices, and it feeds F2 (Oracle 5Y CDS) and the live F1 proxy (CoreWeave 5Y CDS).

## How to get it
- End of day: `https://pddata.dtcc.com/ppd/api/report/cumulative/sec/SEC_CUMULATIVE_CREDITS_YYYY_MM_DD.zip`, one zipped CSV per weekday. History goes back at least a year (26 Nov 2025 was available on 26 Nov 2026).
- Intraday: `https://pddata.dtcc.com/ppd/api/report/intraday/sec/SEC_SLICE_CREDITS_YYYY_MM_DD_N.zip`, numbered from 1 with no gaps, several files a minute (about 580 by 20:30 UTC).
- Real time: `https://pddata.dtcc.com/ppd/api/ticker/SEC/CREDITS`, the latest 100 records as JSON.
- No key. A browser-like or descriptive User-Agent works.

## Fields the index reads
Counts are from the 24 Nov 2026 end-of-day file: 1,225 records, 110 columns.

| Field | Meaning | Unit / format | Blanks and odd values | Used? |
| --- | --- | --- | --- | --- |
| Dissemination Identifier | Id of this record | integer | none seen | yes, to apply corrections |
| Original Dissemination Identifier | For corrections, cancellations and lifecycle events, the record it refers to | integer | blank on new trades | yes |
| Action type | NEWT new, MODI modified, CORR corrected, TERM terminated, EROR cancelled | code | NEWT 776, MODI 219, CORR 109, TERM 84, EROR 36 | yes: NEWT and CORR kept, EROR removes |
| Event type | TRAD trade, NOVA novation, ETRM early termination, CLRG clearing | code | blank on 146 (older files leave it blank) | yes: NOVA and other non-trade events price nothing; blank counts as a trade |
| Execution Timestamp | When the trade was done | ISO 8601, UTC | corrections and late reports carry an earlier date | yes: a trade is priced on its execution day, whichever file reports it (weekdays only) |
| Expiration Date | Maturity | YYYY-MM-DD | | yes: on-the-run 5Y only (20 Jun / 20 Dec, five years out) |
| Underlying Asset Name, UPI Underlier Name | Reference entity | text | spelling varies | yes, matched with the RED code |
| Underlier ID-Leg 1 | RED code, ISIN or loan id | text; `LX…` = loans | can hold two ids joined with `;` | yes |
| UPI FISN | Product | e.g. `NA/CDS Corp SN Sr`, `NA/CDS Corp Basket`; `NA/CDS Corp SN` marks bond-referenced and short-dated contracts, `...Ph` physical settlement | | yes: only `CDS Corp SN Sr` prices |
| Fixed rate-Leg 1 | Running coupon | decimal, 0.01 = 1% | blank on 301; `1` on 16 (see quirks) | yes: 0.5 or more is percent, divided by 100; kept only if 0 < C <= 10% |
| Spread-Leg 1 | Quoted spread | decimal, 0.013 = 130 bp | blank on 869; `0` on 54 = not quoted | yes: decimals 0.001-0.5, or raw bp 5-20,000 read as bp; 10-5,000 bp kept |
| Notional amount-Leg 1 | Size | currency units | capped sizes end in `+` | yes: under $1M prices nothing; also the base of the upfront percentage |
| Notional currency-Leg 1, Other payment currency | Currencies | ISO code | a few CHF and EUR; `USD;USD` on rows with two payments | yes: USD only |
| Package indicator, Non-standardized term indicator, Post-priced swap indicator | Flags | TRUE / FALSE, any case; blank on older files | 274 packages, 54 post-priced in a year (our names) | yes: any TRUE prices nothing |
| Other payment amount | Upfront paid at the trade | currency units, **unsigned** | blank when there is no upfront | yes |
| Other payment type | UFRO upfront, UWIN unwind | code | blank on 414 | yes: only UFRO amounts are read as upfronts |
| Platform identifier | Venue code | e.g. BILT, TWSF, XXXX | | no |
| Cleared | Clearing flag | N, I | to decode | no |

## Quirks
1. **A blank upfront is not zero.** In JavaScript `Number('')` is 0, so a trade with neither a quoted spread nor an upfront priced at its coupon. See `findings/2026-11-26-blank-upfronts.md`. Fixed on aistress.si on 26 Nov 2026.
2. **Some coupons are in percent.** Oracle records carry `Fixed rate-Leg 1 = 1` instead of `0.01` (95 in the last year). Until 26 Nov 2026 the harness read them as a 100% coupon and dropped the trade; now 0.5 or more is divided by 100. 48 Oracle and 9 Meta priced trades recovered (issue #7).
3. **Upfronts carry no sign.** Both readings are computed. The previous 10/100 bp coupon-dependent floors were assumptions, not mathematical feasibility bounds, and have been removed. Two admissible roots mean an ambiguous row, which is excluded. Neither a nearby quote nor a previous level chooses the sign. Numerical-domain failures also exclude the row; a numerical upper bound is not proof that the opposite sign is economically impossible. Accepted inversion remains conditional on the simplified flat-hazard, fixed-recovery model. See `docs/reliability-release-2026-11-28.md` at repository root.
4. **Capped notionals.** Large trades show the cap with a `+`; the true size is larger. The upfront amount appears to be reported on the capped size too: on days with both, capped and uncapped implied spreads differ by a median 0.5 bp (Oracle, 165 days). Counted per day as `n_capped`.
5. **Not every record is a price.** Of 3,374 on-the-run 5-year records for our three names in a year, 346 are packages, odd lots, novations, non-standard or post-priced trades, non-USD or not senior CDS. A cancellation (EROR) can arrive days later in another file: 99 trades behind past levels were cancelled that way. See `findings/2026-11-28-cds-contract-checks.md`.
6. **Unwinds are not upfronts.** `Other payment type = UWIN` is the payment to close a trade. 26 on-the-run Oracle records in the year; no longer read as prices.
7. **Raw bp in the spread field.** Rare (1 Meta record in the year): a quoted spread of 435 means 435 bp.
8. **One of two repositories.** ICE Trade Vault is the other SEC repository, so some trades never appear here.
9. **Late reports.** Dealers may report up to 24 hours late; intraday files only hold what has been reported.
10. **Lifecycle records are not prices.** MODI and TERM describe changes to existing trades.

## Terms
SEC rules require public dissemination free of charge. Open question: read DTCC's public price dissemination terms of use and record here exactly what they allow for storing and republishing.

## Open questions
- ~~Does gating upfronts on `Other payment type = UFRO` change any daily level?~~ Yes, a little: see `findings/2026-11-26-p0-tape-hygiene.md`.
- Can `UPI FISN` and RED codes replace name matching entirely?
- What do the `Cleared` values mean, and do cleared and uncleared trades price differently?
- Does ICE Trade Vault publish a comparable free file?
