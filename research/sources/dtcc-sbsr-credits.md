# DTCC public swap data: SEC credit file

**Id in sources.yaml:** `dtcc` · **Status:** loader in harness (`harness/dtcc.py`) · **Last checked:** 2026-11-26 by Claude (Opus 5.5), run by @gagattzo

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
| Event type | TRAD trade, NOVA novation, ETRM early termination, CLRG clearing | code | blank on 146 | not yet |
| Execution Timestamp | When the trade was done | ISO 8601, UTC | corrections can carry an earlier date | yes: same-day trades only |
| Expiration Date | Maturity | YYYY-MM-DD | | yes: on-the-run 5Y only (20 Jun / 20 Dec, five years out) |
| Underlying Asset Name, UPI Underlier Name | Reference entity | text | spelling varies | yes, matched with the RED code |
| Underlier ID-Leg 1 | RED code, ISIN or loan id | text; `LX…` = loans | can hold two ids joined with `;` | yes |
| UPI FISN | Product | e.g. `NA/CDS Corp SN Sr`, `NA/CDS Corp Basket` | | not yet; could replace name matching |
| Fixed rate-Leg 1 | Running coupon | decimal, 0.01 = 1% | blank on 301; `1` on 16 (see quirks) | yes: 0.5 or more is percent, divided by 100; kept only if 0 < C <= 10% |
| Spread-Leg 1 | Quoted spread | decimal, 0.013 = 130 bp | blank on 869; `0` on 54 = not quoted | yes: decimals 0.001-0.5, or raw bp 5-20,000 read as bp; 10-5,000 bp kept |
| Notional amount-Leg 1 | Size | currency units | capped sizes end in `+` | yes, for the upfront percentage |
| Other payment amount | Upfront paid at the trade | currency units, **unsigned** | blank when there is no upfront | yes |
| Other payment type | UFRO upfront, UWIN unwind | code | blank on 414 | yes: only UFRO amounts are read as upfronts |
| Platform identifier | Venue code | e.g. BILT, TWSF, XXXX | | no |
| Cleared | Clearing flag | N, I | to decode | no |

## Quirks
1. **A blank upfront is not zero.** In JavaScript `Number('')` is 0, so a trade with neither a quoted spread nor an upfront priced at its coupon. See `findings/2026-11-26-blank-upfronts.md`. Fixed on aistress.si on 26 Nov 2026.
2. **Some coupons are in percent.** Oracle records carry `Fixed rate-Leg 1 = 1` instead of `0.01` (95 in the last year). Until 26 Nov 2026 the harness read them as a 100% coupon and dropped the trade; now 0.5 or more is divided by 100. 48 Oracle and 9 Meta priced trades recovered (issue #7).
3. **Upfronts carry no sign.** Both readings are computed and the one closer to the day's quoted median wins.
4. **Capped notionals.** Large trades show the cap with a `+`; the true size is larger. The upfront amount appears to be reported on the capped size too: on days with both, capped and uncapped implied spreads differ by a median 0.5 bp (Oracle, 165 days). Counted per day as `n_capped`.
8. **Unwinds are not upfronts.** `Other payment type = UWIN` is the payment to close a trade. 26 on-the-run Oracle records in the year; no longer read as prices.
9. **Raw bp in the spread field.** Rare (1 Meta record in the year): a quoted spread of 435 means 435 bp.
5. **One of two repositories.** ICE Trade Vault is the other SEC repository, so some trades never appear here.
6. **Late reports.** Dealers may report up to 24 hours late; intraday files only hold what has been reported.
7. **Lifecycle records are not prices.** MODI and TERM describe changes to existing trades.

## Terms
SEC rules require public dissemination free of charge. Open question: read DTCC's public price dissemination terms of use and record here exactly what they allow for storing and republishing.

## Open questions
- ~~Does gating upfronts on `Other payment type = UFRO` change any daily level?~~ Yes, a little: see `findings/2026-11-26-p0-tape-hygiene.md`.
- Can `UPI FISN` and RED codes replace name matching entirely?
- What do the `Cleared` values mean, and do cleared and uncleared trades price differently?
- Does ICE Trade Vault publish a comparable free file?
