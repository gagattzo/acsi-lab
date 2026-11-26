# Who in the observed BDC book lends to AI infrastructure, and at what marks (AGENT-BRIEF P3.1)

**Agent:** Claude (Opus 5.5), run by @gagattzo · **Status:** signed off at group level by @gagattzo on 26 Nov 2026 (`research/bdc/review_log.csv`); published at aistress.si/bdc

## Question
Which business development companies hold debt or equity of AI-infrastructure borrowers (neoclouds, AI/HPC miners,
hyperscale data-center developers), how much, and where are those holdings marked? This is the cheapest test of
the Stack brief's private-loan layer: if the marks sit at cost, a markdown-based layer has nothing to read yet.

## Answer
**Observed BDC book only:** most AI debt sits with private funds, banks, insurers and project bonds that do not file
these schedules. Marks are as filed at 30 Jun 2026 (Ares Capital: 30 Sep 2026).

- 28 BDCs, 10 borrower groups, 54 funded facilities matched to a named obligor. Cost $1,417.6m, fair value $1,462.4m.
- **Debt: 41 facilities, cost $1,366.7m, fair value $1,374.0m, 1.005x cost.** One facility is below 0.97 of cost:
  Firmus Supercloud at Blackstone Secured Lending, 0.948. The same borrower sits at 1.000 at Blackstone Private Credit.
- Equity: 13 positions (Nscale, Crusoe, Together AI), cost $50.9m, fair value $88.4m, 1.74x. These are marked *up*.
- No matched row carries a PIK rate, and non-accrual is not tagged.

So on the filed marks, the private book shows no stress at all. That does not say the borrowers are healthy, because
BDC marks are slow and often generous. It does say that a layer scoring markdowns would read calm today, and that
it can only say something once the 30 Sep marks (the 2026_11 file) can be compared with these.

The largest single exposure relative to the holder is Great Elm Capital: four CoreWeave facilities, 15.5% of net
assets. The broadest borrower is CoreWeave: 14 BDCs, 19 facilities, but only $87.8m at cost. The biggest by size
are hyperscale data-center developers: Vantage ($602.0m across 4 BDCs), EdgeConneX, CyrusOne.

### By borrower

| Borrower group | Kind | BDC holders | Facilities | Cost ($m) | Fair value ($m) | FV / cost |
| --- | --- | --- | --- | --- | --- | --- |
| Vantage Data Centers | data-center | 4 | 5 | 602.0 | 608.3 | 1.010 |
| EdgeConneX | data-center | 2 | 2 | 180.3 | 178.4 | 0.990 |
| CyrusOne | data-center | 1 | 1 | 159.0 | 159.3 | 1.002 |
| Firmus | neocloud | 2 | 2 | 121.0 | 120.0 | 0.992 |
| Galaxy Digital (Helios campus) | ai-hpc | 2 | 2 | 100.0 | 101.0 | 1.010 |
| Nscale | neocloud | 5 | 13 | 58.2 | 96.2 | 1.655 (mostly preferred equity) |
| CoreWeave | neocloud | 14 | 19 | 87.8 | 89.3 | 1.016 |
| Together AI | neocloud | 2 | 3 | 51.2 | 51.2 | 1.000 |
| QTS (Microsoft campus loan) | data-center | 1 | 1 | 46.5 | 46.7 | 1.003 |
| Crusoe | neocloud | 4 | 6 | 11.7 | 11.9 | 1.015 |

### By holder (share of the BDC's net assets)
Full table with filing links: `research/bdc/holders.csv`. The top five:

| BDC | Mark date | Borrowers | Fair value ($m) | FV / cost | Share of net assets |
| --- | --- | --- | --- | --- | --- |
| Great Elm Capital Corp. | 2026-06-30 | CoreWeave | 17.2 | 1.039 | 15.54% |
| Ares Core Infrastructure Fund | 2026-06-30 | CoreWeave, Galaxy, Vantage | 279.1 | 1.014 | 5.55% |
| Lago Evergreen Credit | 2026-06-30 | Crusoe | 7.0 | 0.990 | 4.91% |
| Trinity Capital Inc. | 2026-06-30 | Crusoe, Together AI | 50.2 | 1.005 | 3.97% |
| Crestline Lending Solutions | 2026-06-30 | Galaxy | 12.6 | 1.003 | 3.47% |

### Leads, not counted
29 rows matched only a keyword. The one worth a look is **Ark Data Centers**, a colocation operator. It is held by
four Goldman Sachs vehicles plus Crestline and Vista, and one tranche is marked **0.88 of cost** at all four Goldman
vehicles. That is the lowest mark anywhere in this slice. Whether Ark is AI capacity is open; see `review_log.csv`.

## How it was built
`python research/tools/bdc_exposure.py 2026_08 2026_09 2026_10` (zips hashed in `manifests/sec_bdc_sha256.csv`).
Current-period rows only, each holder's latest filing, roll-up `[Member]` rows dropped, unfunded commitments listed
but not summed. Patterns are anchored after the false matches in `research/sources/sec-bdc-datasets.md` (Helios,
PlantSwitch). Check: on all 18 matched rows where the filer also tagged a percentage of net assets, fair value divided
by the net assets taken from `num.tsv` agrees with it. The $2.23bn keyword figure in the AGENT-BRIEF was not deduplicated and included those false matches,
so it is not used.

## Sign-off
`research/bdc/review_log.csv` holds an agent review of every row (70 "propose accept", 29 "needs research") and the
human decision of 26 Nov 2026, made in chat by @gagattzo at group level: neoclouds, AI/HPC campuses and hyperscale
data-center developers count as AI infrastructure, each shown separately. Keyword leads, Ark Data Centers included,
stay out until researched. The rows were not reviewed one by one; anyone can challenge a row with an issue, and the
tool re-reads the log on every run.

## What would prove it wrong
- A matched obligor that is not the AI-infrastructure entity it appears to be (for example a CoreWeave name on a
  non-CoreWeave SPV), or a large holding the patterns missed. Check: search 2026_08 `soi.tsv` for the legal names in
  CoreWeave's, Crusoe's and Lambda's own debt disclosures.
- The calm reading is wrong if the 30 Sep 2026 marks (2026_11 file) show same-facility markdowns that these 30 Jun
  marks had no hint of.
