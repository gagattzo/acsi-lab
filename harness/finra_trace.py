"""FINRA TRACE Scraper and Pipeline for AI Infrastructure Corporate & 144A Bonds.

Part of ACSI Lab (https://aistress.si/lab).
Fetches bond reference data and TRACE secondary trades from FINRA's Fixed Income Data portal:
  https://www.finra.org/finra-data/fixed-income/bond

Outputs:
  - data/bond_reference.json
  - data/trace_trades_raw.parquet
  - data/trace_trades_clean.parquet (deduplicated, cancelled trades filtered out, corrections applied)
"""

import argparse
import asyncio
import json
import os
import pathlib
import random
import sys
import time
from datetime import datetime

import pandas as pd
from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Endpoints discovered from portal reverse-engineering
PORTAL_URL = "https://www.finra.org/finra-data/fixed-income/bond"
SESSION_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/group/Firm/name/ActiveIndividual/dynamiclookup/examCode"
REF_DATA_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/FixedIncomeMarket/name/CorporateAndAgencySecurities"
TRADE_DATA_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/FixedIncomeMarket/name/CorporateAndAgencyTradeHistory"
SEARCH_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/FixedIncomeMarket/name/BondSearch"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Reference and trade schema fields
REF_FIELDS = [
    "cusip",
    "issueSymbolIdentifier",
    "issuerName",
    "couponRate",
    "maturityDate",
    "productSubTypeCode",
    "is144A",
    "isCallable",
    "couponType",
    "industryGroup",
    "moodysRating",
    "standardAndPoorsRating",
    "traceGradeCode",
    "isConvertible",
    "nextCallDate",
    "lastSalePrice",
    "lastSaleYield",
    "lastTradeDate",
    "priceChangeNumber",
    "priceChangePercent",
    "isPerpetual",
]

TRADE_FIELDS = [
    "tradeExecutionDate",
    "tradeExecutionTime",
    "tradeStatus",
    "reportedTradeVolume",
    "lastSalePrice",
    "lastSaleYield",
    "tradeModifierCode",
    "tradeModifier2Code",
    "specialPriceFlag",
    "isAsof",
    "reportingPartySideCode",
    "reportingPartyTypeCode",
    "contraPartyTypeCode",
    "cusip",
    "issueSymbolIdentifier",
    "issuerName",
    "remunerationIndicator",
    "atsFlag",
]


class FinraTraceClient:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.api_context = None
        self.xsrf_token = None
        self.cookies = {}

    async def start(self):
        """Starts Playwright Chromium, accepts user agreement, captures cookies and tokens."""
        print("[1/4] Launching Playwright Chromium & capturing session state...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        self.api_context = await self.playwright.request.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Accept": "application/json"},
        )

        # Negotiate session and retrieve XSRF token
        resp = await self.api_context.get(SESSION_URL)
        if resp.status != 200:
            raise RuntimeError(f"Failed to initialize session: HTTP {resp.status}")

        storage = await self.api_context.storage_state()
        for cookie in storage.get("cookies", []):
            self.cookies[cookie["name"]] = cookie["value"]
            if cookie["name"] == "XSRF-TOKEN":
                self.xsrf_token = cookie["value"]

        if not self.xsrf_token:
            raise RuntimeError("Could not retrieve XSRF-TOKEN.")

        print(f"      Session established. XSRF-TOKEN: {self.xsrf_token}")
        print(f"      Captured Cookies: {list(self.cookies.keys())}")
        print(f"      Backend Reference URL: {REF_DATA_URL}")
        print(f"      Backend Trade History URL: {TRADE_DATA_URL}")

    async def fetch_reference(self, cusip_or_symbol: str) -> dict:
        """Fetches reference data from CorporateAndAgencySecurities."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-XSRF-TOKEN": self.xsrf_token,
            "User-Agent": USER_AGENT,
        }
        payload = {
            "fields": REF_FIELDS,
            "orFilters": [
                {
                    "compareFilters": [
                        {"fieldName": "cusip", "fieldValue": cusip_or_symbol, "compareType": "EQUAL"},
                        {"fieldName": "issueSymbolIdentifier", "fieldValue": cusip_or_symbol, "compareType": "EQUAL"},
                    ]
                }
            ],
            "offset": 0,
            "limit": 1,
        }
        resp = await self.api_context.post(REF_DATA_URL, headers=headers, data=json.dumps(payload))
        if resp.status != 200:
            print(f"  [WARN] Failed reference lookup for {cusip_or_symbol}: HTTP {resp.status}")
            return None
        body = await resp.json()
        raw = body.get("returnBody", {}).get("data")
        records = json.loads(raw) if raw else []
        return records[0] if records else None

    async def fetch_trades(self, cusip_or_symbol: str, limit: int = 500) -> list:
        """Fetches TRACE trades from CorporateAndAgencyTradeHistory."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-XSRF-TOKEN": self.xsrf_token,
            "User-Agent": USER_AGENT,
        }
        payload = {
            "fields": TRADE_FIELDS,
            "orFilters": [
                {
                    "compareFilters": [
                        {"fieldName": "cusip", "fieldValue": cusip_or_symbol, "compareType": "EQUAL"},
                        {"fieldName": "issueSymbolIdentifier", "fieldValue": cusip_or_symbol, "compareType": "EQUAL"},
                    ]
                }
            ],
            "sortFields": ["-tradeExecutionDate", "-tradeExecutionTime"],
            "offset": 0,
            "limit": limit,
        }
        resp = await self.api_context.post(TRADE_DATA_URL, headers=headers, data=json.dumps(payload))
        if resp.status != 200:
            print(f"  [WARN] Failed trades lookup for {cusip_or_symbol}: HTTP {resp.status}")
            return []
        body = await resp.json()
        raw = body.get("returnBody", {}).get("data")
        return json.loads(raw) if raw else []

    async def close(self):
        if self.api_context:
            await self.api_context.dispose()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


def clean_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Cleans raw TRACE trades:
    - Normalizes 1MM+ volume dissemination caps
    - Filters out cancelled trades (status C, Y, X, CANCEL)
    - Applies corrections and removes duplicates
    """
    if df.empty:
        return df

    initial_len = len(df)
    df = df.copy()

    # 1. Volume cap normalization: '1MM+' -> 1,000,000.0, set boolean isVolumeCapped flag
    if "reportedTradeVolume" in df.columns:
        df["isVolumeCapped"] = df["reportedTradeVolume"].astype(str).str.contains(r"\+", na=False)
        clean_vol = (
            df["reportedTradeVolume"]
            .astype(str)
            .str.replace("1MM+", "1000000", regex=False)
            .str.replace("+", "", regex=False)
        )
        df["reportedTradeVolume"] = pd.to_numeric(clean_vol, errors="coerce")

    # 2. Price and yield numeric conversion
    if "lastSalePrice" in df.columns:
        df["lastSalePrice"] = pd.to_numeric(df["lastSalePrice"], errors="coerce")
    if "lastSaleYield" in df.columns:
        df["lastSaleYield"] = pd.to_numeric(df["lastSaleYield"], errors="coerce")

    # 3. Filter out cancellations
    cancelled_mask = df["tradeStatus"].astype(str).str.upper().isin(["C", "Y", "CANCEL", "X"])
    cancelled_count = int(cancelled_mask.sum())
    df_clean = df[~cancelled_mask].copy()

    # 4. Deduplicate tape prints
    subset_cols = [
        "cusip",
        "tradeExecutionDate",
        "tradeExecutionTime",
        "reportedTradeVolume",
        "lastSalePrice",
        "reportingPartySideCode",
        "contraPartyTypeCode",
    ]
    dedup_cols = [c for c in subset_cols if c in df_clean.columns]
    df_dedup = df_clean.drop_duplicates(subset=dedup_cols, keep="last")

    print(
        f"      Tape cleaning: {initial_len} raw prints -> "
        f"{cancelled_count} cancelled removed -> "
        f"{len(df_dedup)} clean trades ({initial_len - len(df_dedup)} dropped total)."
    )
    return df_dedup


async def run_pipeline(universe_path: str, test_mode: bool = False, output_dir: str = "data"):
    # 1. Load universe
    if not os.path.exists(universe_path):
        # Check in acsi-lab/data or current data
        alt_path = os.path.join(ROOT, universe_path)
        if os.path.exists(alt_path):
            universe_path = alt_path
        else:
            raise FileNotFoundError(f"Universe file {universe_path} not found.")

    with open(universe_path, "r") as f:
        universe = json.load(f)

    client = FinraTraceClient(headless=True)
    await client.start()

    bonds_to_process = universe[:2] if test_mode else universe
    mode_label = "[TEST MODE - First 2 Bonds]" if test_mode else f"[FULL RUN - {len(universe)} Bonds]"
    print(f"\n[2/4] {mode_label} Fetching reference data and TRACE trades...")

    all_refs = []
    all_trades = []
    summary_rows = []

    for idx, bond in enumerate(bonds_to_process, start=1):
        cusip = bond.get("cusip")
        symbol = bond.get("trace_symbol") or bond.get("issueSymbolIdentifier")
        issuer = bond.get("issuer") or bond.get("issuerName") or "Unknown"

        print(f"\n[{idx}/{len(bonds_to_process)}] Querying {cusip} ({symbol}) - {issuer[:30]}...")

        # 1. Fetch reference details
        ref = await client.fetch_reference(cusip)
        if ref:
            all_refs.append(ref)
            print(f"      Reference: {ref.get('issuerName')} | {ref.get('couponRate')}% {ref.get('maturityDate')} | 144A: {ref.get('is144A')}")
        else:
            print(f"      Reference: [No data]")

        # 2. Fetch trade prints
        trades = await client.fetch_trades(cusip, limit=500)
        print(f"      Trades: {len(trades)} records retrieved")
        all_trades.extend(trades)

        # Record summary info
        latest_price = trades[0].get("lastSalePrice") if trades else (ref.get("lastSalePrice") if ref else None)
        latest_yield = trades[0].get("lastSaleYield") if trades else (ref.get("lastSaleYield") if ref else None)
        summary_rows.append(
            {
                "Issuer": (ref.get("issuerName") if ref else issuer)[:28],
                "CUSIP": cusip,
                "Symbol": symbol,
                "Coupon/Maturity": f"{float((ref.get('couponRate') if ref else bond.get('coupon')) or 0):.2f}% {ref.get('maturityDate') if ref else bond.get('maturity')}",
                "144A": "Y" if (ref.get("is144A") == "Y" if ref else bond.get("is_144a")) else "N",
                "Trades": len(trades),
                "Latest Price": f"{float(latest_price):.2f}" if latest_price else "-",
                "Latest Yield": f"{float(latest_yield):.2f}%" if latest_yield else "-",
            }
        )

        # Print raw JSON fields in test mode
        if test_mode:
            print(f"\n--- Raw JSON Reference Fields for {cusip} ---")
            print(json.dumps(ref, indent=2))
            if trades:
                print(f"\n--- Raw JSON Trade Fields for {cusip} (Latest Print) ---")
                print(json.dumps(trades[0], indent=2))

        # Polite rate-limiting delay
        if idx < len(bonds_to_process):
            delay = random.uniform(2.0, 3.0)
            print(f"      Waiting {delay:.2f}s polite delay...")
            await asyncio.sleep(delay)

    await client.close()

    # [3/4] Export Datasets
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Reference Data JSON
    ref_file = out_dir / "bond_reference.json"
    with open(ref_file, "w") as f:
        json.dump(all_refs, f, indent=2)
    print(f"\n[3/4] Saved {len(all_refs)} reference records to {ref_file}")

    # 2. Raw TRACE trades Parquet (Append-Only)
    raw_file = out_dir / "trace_trades_raw.parquet"
    df_new = pd.DataFrame(all_trades)
    
    if raw_file.exists():
        df_existing = pd.read_parquet(raw_file)
        df_raw = pd.concat([df_existing, df_new], ignore_index=True)
        # Deduplicate on key fields to preserve history past 500 cap
        df_raw = df_raw.drop_duplicates(
            subset=["cusip", "tradeExecutionDate", "tradeExecutionTime", "reportedTradeVolume", "lastSalePrice", "tradeStatus"],
            keep="last"
        )
    else:
        df_raw = df_new

    df_raw.to_parquet(raw_file, index=False)
    print(f"      Saved {len(df_raw)} raw trades to {raw_file} (including appended history)")

    # 3. Clean TRACE trades Parquet
    df_clean = clean_trades(df_raw)
    clean_file = out_dir / "trace_trades_clean.parquet"
    df_clean.to_parquet(clean_file, index=False)
    print(f"      Saved {len(df_clean)} clean trades to {clean_file}")

    # Also mirror to acsi-lab/data if running from outside
    alt_dir = ROOT / "data"
    if alt_dir.resolve() != out_dir.resolve():
        alt_dir.mkdir(parents=True, exist_ok=True)
        with open(alt_dir / "bond_reference.json", "w") as f:
            json.dump(all_refs, f, indent=2)
        df_raw.to_parquet(alt_dir / "trace_trades_raw.parquet", index=False)
        df_clean.to_parquet(alt_dir / "trace_trades_clean.parquet", index=False)

    # [4/4] Print Summary Table
    print("\n" + "=" * 90)
    print(f"{'#':<3} {'Issuer':<28} {'CUSIP':<10} {'Coupon/Maturity':<18} {'144A':<5} {'Trades':<8} {'Price':<8} {'Yield':<8}")
    print("=" * 90)
    for idx, r in enumerate(summary_rows, 1):
        print(f"{idx:<3} {r['Issuer']:<28} {r['CUSIP']:<10} {r['Coupon/Maturity']:<18} {r['144A']:<5} {r['Trades']:<8} {r['Latest Price']:<8} {r['Latest Yield']:<8}")
    print("=" * 90)
    print(f"Pipeline completed successfully. {len(summary_rows)} bonds processed.")


def main():
    parser = argparse.ArgumentParser(description="FINRA TRACE Pipeline for AI Infrastructure Bonds")
    parser.add_argument("--universe", default="data/bonds_universe.json", help="Path to universe JSON")
    parser.add_argument("--test", action="store_true", help="Test mode on first 2 bonds only")
    parser.add_argument("--out", default="data", help="Output directory")
    args = parser.parse_args()

    asyncio.run(run_pipeline(args.universe, test_mode=args.test, output_dir=args.out))


if __name__ == "__main__":
    main()
