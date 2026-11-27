# FINRA TRACE (corporate bond trades, including 144A)

**Id in sources.yaml:** `finra_trace` · **Status:** decoded from public documentation; **not usable until licensed** · **Last checked:** 2026-11-27 by Claude (Opus 5.5), run by @gagattzo

## What it is
TRACE is FINRA's record of every over-the-counter trade in US corporate bonds, private 144A bonds included, reported
within 15 minutes and disseminated in real time. Most AI data-center debt is 144A high-yield paper (CoreWeave's
unsecured notes, the tenant-backed campus bonds), so TRACE is the only complete daily price record for it.

For ACSI it would fix the debt side's three problems at once:
- **No ceiling to hide behind:** prices exist for many issuers, not two CDS names.
- **Fresh:** most of these bonds trade on most days. CoreWeave CDS had no print on 25 Nov 2026, while its bonds
  widened 31 to 109 bp that day.
- **Hard to push:** a median across 24 issues and 6 tenant groups cannot be moved by one trade.

## How to get it, and what each way allows
| Route | What you get | Terms |
| --- | --- | --- |
| finra.org "Corporate and Agency Trade Activity" page (dataset `FixedIncomeMarket` / `CorporateAndAgencyTradeActivity`) | Trade-level prints by CUSIP: price, yield, size (capped), side, time | For people only: the Fixed Income Data User Agreement bars robots and any automatic process that monitors or copies the data, commercial or not. **Never scripted by this lab** |
| FINRA API (`api.finra.org`), **Public** credential from the API Console (individual FINRA Gateway account) | The `fixedIncomeMarket` dataset group; whether it includes per-bond trade prices is to be checked with `research/tools/finra_probe.py` | Fixed Income Specific Terms: non-commercial use; data and derived data may be redistributed to end users for their non-commercial use, with FINRA attributed as owner and source, no fee, no further redistribution. **The route this lab uses** |
| TRACE data licence (BTDS / BTDS-144A feeds, vendor or end-user agreement) | Real-time and end-of-day feeds | Required for commercial use, including settling a contract on the index |
| Market-data vendors (Bloomberg, ICE, MarketAxess CP+) | Prices, often evaluated, by CUSIP | Paid; redistribution usually barred |

Dissemination caps: trades over $1 million in high-yield bonds are shown as "1MM+", so sizes are floors, as with DTCC
notionals.

## The basket
The 24 high-yield data-center bonds ZeroHedge charted on 25-26 Nov 2026 (universe per Morgan Stanley Research,
28 Sep 2026), grouped by who pays the rent. CUSIPs still to be confirmed from the offering documents or FINRA.

| Tenant group | Issues |
| --- | --- |
| CoreWeave unsecured | CRWV 9.25% 2030, 9% 2031, 9.75% 2031, 9.625% 2032 |
| CoreWeave is the tenant | APLD 9.25% 2030, EDGCOM 7.5% 2031, CORZ 7.75% 2031, ELKGVP 7.5% 2031, ELNFOR 7% 2031, GALAXY 9.875% 2031, DDRIVE 8.875% 2031 |
| Fluidstack, Google-backstopped | WULF 7.75% 2030, CIFR 7.125% 2030, FLASHC 7.25% 2030, MERIDI 6.25% 2031 |
| Nvidia / Oracle tenant | TRACTC 5.875% 2031, PFORGE 6.75% 2031, TRACTD 6.5% 2031, YNDRDC 6.875% 2031 |
| SoftBank / Jane Street tenant | SECMOS 8.875% 2031, ZENARC 8.875% 2031 |
| Amazon / Meta tenant | BLKPRL 6.125% 2031, STNGRY 6% 2031, CLSPRK 7.875% 2031 |

## Fields the index would use
| Field | Meaning | Use |
| --- | --- | --- |
| cusip, issueSymbolIdentifier | The bond | key |
| tradeExecutionDate, tradeExecutionTime | When | day of the print |
| lastSalePrice, lastSaleYield | Price and yield of the trade | yield minus the interpolated Treasury = G-spread |
| reportedTradeVolume | Size, capped at 1MM+ for high yield | weight or a minimum-size filter |
| reportingSideCode, contraPartyTypeCode | Customer buy or sell vs dealer | a customer-trade-only filter, the usual guard against dealer marks |
| tradeStatus, isAsof, tradeModifierCode, specialPriceFlag | Cancels, corrections, late and special prints | cleaning, as with EROR/CORR on DTCC |

## Quirks to expect (from FINRA documentation, to be checked on real data)
1. Sizes above the cap are floors.
2. Cancels and corrections arrive as separate records.
3. Late (as-of) reports change past days.
4. Odd lots and inter-dealer prints sit next to institutional customer trades and can be far off the market.

## Terms
Through the API with a Public credential: a free public index derived from TRACE is allowed, attributed to FINRA,
with no fee. A contract settling on the index is commercial: before that, TRACE inputs need a licence or must be
removed. Raw TRACE rows are never committed to this repository; only derived daily levels are published.

## Open questions
- Which FINRA product covers end-of-day 144A prices by CUSIP at the lowest cost, and does it allow publishing a
  derived index (not the prints themselves)?
- Customer-only filter or all prints: which one survives the one-trade test?
