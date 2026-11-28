# ACSI method v0.1 — historical edition

> **Historical scope:** this document describes the 24 November 2026 v0.1 edition and the earlier v0.1-live rules. It is retained for reproducibility. Read the [current index manual](docs/current-index-manual.md) for deployed v0.2, strict CDS decoding, current publication gates and separate bond research. In particular, the historical dropping/re-averaging rule below is not the current headline availability rule.

**Question:** is the stress in AI infrastructure in the debt that financed the build-out, or in what the GPUs earn?

## Scoring

Each input is scored against two anchors fixed in advance, a calm level and a distress level:

    score = 100 × (x − calm) / (distress − calm), capped between 0 and 100

- **Debt stress (F)** = mean of F1–F3. Needs at least two live inputs.
- **GPU stress (A)** = mean of A1–A2. Needs A1.
- **Gap = F − A.** Inside ±15 the two sides are aligned; +15 or more, the stress sits in the debt; −15 or less, in the GPUs.
- Bands for each side: Calm below 25, Elevated 25–50, Stressed 50–75, Distressed 75+.

## Inputs (edition of 24 Nov 2026)

| Id | Input | Calm → distress | Weekly value | Daily in `v0.1-live` |
| --- | --- | --- | --- | --- |
| F1 | CoreWeave 9.625% 2032 notes, spread over Treasuries | 500 → 1,000 bp | +1,191 bp | CoreWeave 5Y CDS (DTCC) as a live proxy |
| F2 | Oracle 5Y CDS | 100 → 1,000 bp | 365 bp | DTCC daily median |
| F3 | AI data-center construction loans, secondary price, median | 98 → 80 cents | 90 | held |
| A1 | H100 rental index (Silicon Data SDH100RT) | $3.56 → $2.06 | $3.10 | held |
| A2 | Blackwell B200 rent, change over about half a year | −8% → −40% | +29.5% | held |

Frozen reading on 24 Nov 2026: debt 58.0, GPU 15.3, gap +42.6. The former decoder reported v0.1-live +42.9 on 25 Nov; that pre-strict result is historical evidence, not a validated current reading.

## Rules

- An input past its window is dropped and the others re-averaged; nothing is filled in.
- F3 is the median of loan prints within 56 days; press wording is taken at its bound ("below the mid-80s" = 85).
- The current DTCC cleaning implementation is `harness/dtcc.py`, with a separately implemented JavaScript equivalent on the site. It has changed since this edition; see the [current manual](docs/current-index-manual.md) for the strict decoder rather than using this historical document as a source contract.
- Anchors are frozen for method v0.1. Any change is a new method version, published side by side with the old one.

Archived v0.1 method document (not the current operating manual): [aistress.si/methodology-v0.1.pdf](https://aistress.si/methodology-v0.1.pdf).
