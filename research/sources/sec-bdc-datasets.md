# SEC BDC Data Sets

**Id in sources.yaml:** `sec_bdc` · **Status:** decoded; exposure-map tool in `research/tools/bdc_exposure.py` · **Last checked:** 2026-11-26 by Claude (Opus 5.5), run by @gagattzo

## What it is
The SEC's structured extract of the XBRL that business development companies (BDCs) file. `soi.tsv` is the schedule
of investments: one row per tagged holding, with cost, fair value, rate and maturity. It is the only free, public,
regularly updated record of what private-credit lenders hold and at what mark. For ACSI it could feed a private-loan
layer (AGENT-BRIEF L3) and today feeds the exposure map in `research/bdc/`.

## How to get it
`https://www.sec.gov/files/datastandardsinnovation/data/business-development-company-bdc-data-sets/YYYY_MM_bdc.zip`
(monthly since 2026-01; quarterly `YYYYqQ_bdc.zip` for 2022 Q4 to 2025 Q4). No authentication. Declare a User-Agent.
Each zip holds `soi.tsv` plus the usual financial-statement data sets (`sub`, `num`, `pre`, `tag`, `txt`, `cal`).
Sizes vary with the filing calendar: 2026_08 is 78 MB (169 filers, 30 Jun 2026 marks), 2026_09 is 0.4 MB (2 filers),
2026_10 is 1.8 MB (4 filers, including Ares Capital at 30 Sep 2026). Third-quarter 10-Qs land in the 2026_11 file.

## Fields
| Field | Meaning | Unit / format | Blanks and odd values | Used? |
| --- | --- | --- | --- | --- |
| adsh, cik, name, form, filed | Filing and filer | | | yes |
| ddate, period | Date of the value; period of the filing | YYYY-MM-DD | rows with ddate before period are prior-period comparatives | yes: ddate = period only |
| inlineurl | Link to the filing | URL | | yes: every map row links to it |
| Investment, Identifier Axis | The holding, as free text: borrower, tranche, often rate and maturity | text | 77% filled (2026_08); the structured issuer name is filled on 4% | yes: matching |
| Investment Owned, Cost | Amortised cost | USD | 71% filled; negative on unfunded commitments marked below par | yes |
| Investment Owned, Fair Value | Fair value at ddate | USD | 81% filled; negative or 0 on unfunded commitments | yes |
| Investment Owned, Balance, Principal Amount | Principal | USD | sometimes the full commitment, sometimes off by 1,000x (see quirks) | only within 0.5-1.5x of cost |
| Investment Owned, Net Assets, Percentage | Share of the BDC's net assets | decimal | 40% filled | no: net assets come from num.tsv |
| Investment, Interest Rate, Paid in Kind | PIK rate | decimal | 6% filled overall; 0 of the AI-infrastructure rows | not yet |
| num.tsv AssetsNet / StockholdersEquity | Net assets of the BDC | USD | tag varies by filer; segments must be blank | yes: denominator |

## Quirks
1. **Identity is free text.** The borrower is inside a long string ("Debt Investments - 232.7% United States ... Ark
   Data Centers, LLC Industry IT Services Interest Rate 8.4..."). Matching is by pattern, so every match needs a human.
2. **Common words collide.** "Helios" matched Galaxy's data-center campus and also home services, aerospace and a UK
   materials company; "Switch" matched PlantSwitch. The patterns in the tool are anchored for this; new patterns
   need the same check.
3. **Roll-up rows.** A row ending in `[Member]` totals the facilities listed under it. Summing it double-counts.
4. **Prior-period comparatives** sit in the same file (ddate before period).
5. **Unfunded commitments** appear with blank, zero or negative cost and fair value.
6. **Principal at the wrong scale.** One CoreWeave row (Franklin BSP) tags principal as 479,468,000 against a cost of
   466,000; others tag the full commitment. Principal is used only when it is within 0.5-1.5x of cost.
7. **One family, many vehicles.** Goldman Sachs BDC, Goldman Sachs Private Credit, Phillip Street and West Bay report
   the same Ark Data Centers tranches at the same marks. Per-holder sums are right; a borrower total counts each.
8. **Retroactive refresh.** The SEC has refreshed files after publication (per AGENT-BRIEF), so each zip is hashed at
   download in `manifests/sec_bdc_sha256.csv`.

## Terms
U.S. government data, public domain in the United States. SEC fair-access policy: declare a User-Agent and stay
under 10 requests a second.

## Open questions
- Non-accrual is almost never tagged (1 row in 2026_08). It has to come from filing footnotes.
- A stable facility key across quarters: the tool hashes holder + normalised identifier + maturity, which breaks if a
  filer rewrites the identifier text. Test it on 2026_05 vs 2026_08 before any same-facility markdown is scored.
- Which data-center operators count as AI infrastructure (Vantage, EdgeConneX, CyrusOne, QTS are hyperscale
  developers; Ark Data Centers is colocation) is a human decision, recorded in `research/bdc/review_log.csv`.
