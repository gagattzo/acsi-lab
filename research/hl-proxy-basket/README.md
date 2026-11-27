# Proxy basket fit (Stack junior on Hyperliquid perps)

Research scripts behind `hl/` and `docs/hl-proxy-basket.md`. Run them from this folder, in order. Inputs and
intermediate files (Yahoo closes, pickles, the research junior series) are gitignored: Yahoo data is not
republishable and the pickles are large. Only the small summaries are committed.

| Step | Script | Output |
|---|---|---|
| 1 | `python series.py 2026-11-26` builds L1 and L2 offline from `data/` | `layers.pkl` |
| 2 | `python junior.py` builds the junior composite from those layers (L2 counted wherever it has a Z) | `junior_research.json` |
| 3 | `python fetch_closes.py` (needs yfinance) | `yahoo_closes.csv` |
| 4 | `python fit.py` aligns daily junior changes with returns of session D-1 and D | `fitdata.pkl` |
| 5 | `python fit2.py` ridge vs constrained fits, three alignments, split at 2026-04-01 | printout |
| 6 | `python fit3.py` walk-forward of the 7-leg basket, slices, HL vs Yahoo basis (needs `hl_hourly.pkl`, HL `candleSnapshot` closes, not scripted here) | `fit7_summary.json` |
| 7 | `python fit4.py` smaller baskets, longer history, basket beta | printout |
| 8 | `python fit5.py` **core4** (ORCL, IREN, NBIS short; QQQ long): final weights, walk-forward, slices | `core4_results.json` |
| 9 | `python fit6.py` core4 vs subsets, block bootstrap of the out-of-sample R2, big-move capture | printout |

Needs numpy and pandas. The research series forces L2 to count from 2026-06-30; it is not the published edition
history. See `docs/hl-proxy-basket.md` for the numbers and caveats.
