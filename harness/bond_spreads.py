"""harness/bond_spreads.py: Clean Daily AI Infrastructure Credit Spread Time Series.

Turns data/trace_trades_clean.parquet and data/bond_reference.json into a daily G-spread
benchmark for the AI Credit Stress Index (aistress.si).

Pipeline:
  1. Freshness & Coverage Audit:
     Reports min_date, max_date, total clean trades, and days since last trade for all 24 bonds;
     flags any illiquid/stale SPVs.
  2. US Treasury Par Yield Curve Ingestion:
     Pulls daily US Treasury par yields (1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 20Y) directly from the
     official US Treasury XML/CSV feed.
  3. Daily Volume-Weighted Yield & G-Spread:
     Computes volume-weighted average yield (VWAY) and VWAP per bond per day.
     Linearly interpolates matching Treasury yield for remaining maturity and computes G-spread (bps).
  4. Aggregate 24-Bond Basket:
     Carries forward prints up to 5 trading days for illiquid SPVs (marks stale > 5 days).
     Computes:
       a) Equal-Weighted 24-Bond G-Spread (bps)
       b) Issuer-Capped G-Spread (bps) (CoreWeave 4 bonds = 1 weight, APLD 3 bonds = 1 weight)
  5. Public-Safe Export:
     Saves data/acsi_bond_spreads_daily.json and data/acsi_bond_universe_latest.json with
     all CUSIPs strictly stripped (identified by Issuer + Coupon + Maturity + ID).
"""

import argparse
import csv
import io
import json
import os
import pathlib
import urllib.request
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent

TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv"
)

# Issuer grouping map for issuer-capped index
ISSUER_GROUP_MAP = {
    # CoreWeave 4 bonds
    "21873SAB4": "CoreWeave",
    "21873SAC2": "CoreWeave",
    "21873SAG3": "CoreWeave",
    "21873SAK4": "CoreWeave",
    # Applied Digital 3 bonds
    "00202DAA5": "Applied Digital",
    "03772CAA1": "Applied Digital",
    "03790NAA5": "Applied Digital",
    # Tract Reno SPVs
    "78488XAA2": "Tract Reno",
    "69393LAA1": "Tract Reno",
}


def fetch_treasury_curve(cache_path: pathlib.Path = None) -> dict:
    """Fetches official daily US Treasury par yields across all tenors.

    Returns dict mapping date (YYYY-MM-DD) -> dict of tenor_years: yield_percent.
    """
    if cache_path and cache_path.exists():
        raw_text = cache_path.read_text()
    else:
        req = urllib.request.Request(
            TREASURY_CSV_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_text = resp.read().decode("utf-8")
                if cache_path:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(raw_text)
        except Exception as e:
            print(f"[WARN] Failed to fetch US Treasury feed directly: {e}")
            if cache_path and cache_path.exists():
                raw_text = cache_path.read_text()
            else:
                raise

    reader = csv.DictReader(io.StringIO(raw_text))
    tenor_map = {
        "1 Yr": 1.0,
        "2 Yr": 2.0,
        "3 Yr": 3.0,
        "5 Yr": 5.0,
        "7 Yr": 7.0,
        "10 Yr": 10.0,
        "20 Yr": 20.0,
        "30 Yr": 30.0,
    }

    curve = {}
    for row in reader:
        raw_date = row.get("Date")
        if not raw_date:
            continue
        try:
            iso_date = datetime.strptime(raw_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue

        rates = {}
        for col, tenor in tenor_map.items():
            val = row.get(col)
            if val and val != "N/A":
                try:
                    rates[tenor] = float(val)
                except ValueError:
                    pass

        if rates:
            curve[iso_date] = rates

    return curve


def interpolate_treasury_yield(curve_for_date: dict, maturity_years: float) -> float:
    """Linearly interpolates Treasury par yield for a given maturity in years."""
    tenors = sorted(curve_for_date.keys())
    if not tenors:
        return 4.50  # Fallback

    if maturity_years <= tenors[0]:
        return curve_for_date[tenors[0]]
    if maturity_years >= tenors[-1]:
        return curve_for_date[tenors[-1]]

    for t1, t2 in zip(tenors, tenors[1:]):
        if t1 <= maturity_years <= t2:
            y1 = curve_for_date[t1]
            y2 = curve_for_date[t2]
            frac = (maturity_years - t1) / (t2 - t1)
            return y1 + frac * (y2 - y1)

    return curve_for_date[tenors[-1]]


def audit_freshness_and_coverage(df_trades: pd.DataFrame, universe: list, anchor_date: str):
    """Audits and prints coverage, date ranges, and trade recency per bond."""
    anchor_dt = datetime.strptime(anchor_date, "%Y-%m-%d")
    audit_rows = []

    for b in universe:
        cusip = b["cusip"]
        sub = df_trades[df_trades["cusip"] == cusip]
        n_trades = len(sub)

        if n_trades > 0:
            min_d = sub["tradeExecutionDate"].min()
            max_d = sub["tradeExecutionDate"].max()
            max_dt = datetime.strptime(max_d, "%Y-%m-%d")
            days_since = (anchor_dt - max_dt).days
            stale = days_since > 7  # > 5 trading days
        else:
            min_d, max_d = "-", "-"
            days_since = 999
            stale = True

        audit_rows.append(
            {
                "cusip": cusip,
                "symbol": b.get("trace_symbol") or b.get("issueSymbolIdentifier"),
                "issuer": b.get("issuer") or b.get("issuerName"),
                "coupon": b.get("coupon"),
                "maturity": b.get("maturity"),
                "is_144a": "Y" if b.get("is_144a") else "N",
                "min_date": min_d,
                "max_date": max_d,
                "trades": n_trades,
                "days_since": days_since,
                "stale": stale,
            }
        )

    print("\n" + "=" * 105)
    print("FINRA TRACE FRESHNESS & COVERAGE AUDIT (24-BOND AI INFRASTRUCTURE UNIVERSE)")
    print(f"Anchor Date: {anchor_date}")
    print("=" * 105)
    print(
        f"{'#':<3} {'Issuer':<27} {'Coupon/Maturity':<18} {'144A':<5} {'Earliest':<11} {'Latest':<11} {'Trades':<7} {'Days Ago':<9} {'Status'}"
    )
    print("-" * 105)

    stale_count = 0
    for idx, r in enumerate(audit_rows, start=1):
        status_tag = "[STALE SPV]" if r["stale"] else "Active"
        if r["stale"]:
            stale_count += 1
        cpn_mat = f"{float(r['coupon']):.2f}% {r['maturity']}"
        print(
            f"{idx:<3} {r['issuer'][:26]:<27} {cpn_mat:<18} {r['is_144a']:<5} {r['min_date']:<11} {r['max_date']:<11} {r['trades']:<7} {r['days_since']:<9} {status_tag}"
        )
    print("=" * 105)
    print(f"Audit Summary: {len(audit_rows) - stale_count}/24 bonds active; {stale_count} stale SPVs (>5 trading days).")

    return audit_rows


def process_daily_bond_spreads(
    df_trades: pd.DataFrame,
    universe: list,
    treasury_curve: dict,
    max_carry_days: int = 5,
):
    """Computes daily VWAY, VWAP, and G-spreads per bond, then aggregates Equal-Weighted and Issuer-Capped indices."""
    # 1. Filter out obvious fat-finger yields outside [2%, 50%]
    valid_mask = (df_trades["lastSaleYield"] >= 2.0) & (df_trades["lastSaleYield"] <= 50.0)
    df_valid = df_trades[valid_mask].copy()

    # Pre-parse maturities
    cusip_info = {}
    for b in universe:
        c = b["cusip"]
        mat_dt = datetime.strptime(b["maturity"], "%Y-%m-%d")
        issuer_grp = ISSUER_GROUP_MAP.get(c, b.get("issuer") or b.get("issuerName"))
        cusip_info[c] = {
            "issuer": b.get("issuer") or b.get("issuerName"),
            "issuer_group": issuer_grp,
            "maturity_dt": mat_dt,
            "maturity_str": b["maturity"],
            "coupon": float(b["coupon"]),
            "is_144a": b.get("is_144a"),
            "description": b.get("description") or f"{b.get('issuer')} {b.get('coupon')}% {b.get('maturity')}",
        }

    # 2. Daily volume-weighted yield (VWAY) and price (VWAP) per bond
    bond_daily = {}
    grouped = df_valid.groupby(["tradeExecutionDate", "cusip"])

    for (t_date, cusip), group in grouped:
        vols = group["reportedTradeVolume"].values
        yields = group["lastSaleYield"].values
        prices = group["lastSalePrice"].values

        total_vol = np.sum(vols)
        if total_vol > 0:
            vway = float(np.sum(yields * vols) / total_vol)
            vwap = float(np.sum(prices * vols) / total_vol)
        else:
            vway = float(np.mean(yields))
            vwap = float(np.mean(prices))

        info = cusip_info.get(cusip)
        if not info:
            continue

        trade_dt = datetime.strptime(t_date, "%Y-%m-%d")
        mat_years = (info["maturity_dt"] - trade_dt).days / 365.25

        # Interpolate Treasury yield
        t_curve = treasury_curve.get(t_date)
        if not t_curve:
            # Pick nearest prior curve date
            prior_dates = [d for d in treasury_curve.keys() if d <= t_date]
            t_curve = treasury_curve[prior_dates[-1]] if prior_dates else None

        if t_curve:
            rf_yield = interpolate_treasury_yield(t_curve, mat_years)
            g_spread_bp = (vway - rf_yield) * 100.0
        else:
            rf_yield = None
            g_spread_bp = None

        bond_daily.setdefault(cusip, {})[t_date] = {
            "date": t_date,
            "vway": vway,
            "vwap": vwap,
            "volume": float(total_vol),
            "trades": len(group),
            "mat_years": mat_years,
            "treasury_yield": rf_yield,
            "g_spread_bp": g_spread_bp,
        }

    # 3. Determine all trading dates
    all_trade_dates = sorted(df_valid["tradeExecutionDate"].unique())
    # Filter only days that exist in Treasury curve or trading calendar
    trading_days = [d for d in all_trade_dates if d in treasury_curve or True]

    # 4. Daily aggregation with up to 5 trading days carry-forward
    daily_basket_series = []
    latest_bond_state = {}

    for d_idx, cur_date in enumerate(trading_days):
        day_bond_spreads = {}
        day_issuer_spreads = {}
        day_volumes = 0.0
        day_trades = 0
        bonds_traded_today = 0

        for b in universe:
            c = b["cusip"]
            info = cusip_info[c]
            ig = info["issuer_group"]

            pt = bond_daily.get(c, {}).get(cur_date)
            if pt and pt["g_spread_bp"] is not None:
                # Same day print
                latest_bond_state[c] = {
                    "last_date": cur_date,
                    "vway": pt["vway"],
                    "vwap": pt["vwap"],
                    "g_spread_bp": pt["g_spread_bp"],
                    "treasury_yield": pt["treasury_yield"],
                    "trades": pt["trades"],
                    "volume": pt["volume"],
                }
                day_bond_spreads[c] = pt["g_spread_bp"]
                day_issuer_spreads.setdefault(ig, []).append(pt["g_spread_bp"])
                day_volumes += pt["volume"]
                day_trades += pt["trades"]
                bonds_traded_today += 1
            else:
                # Check carry forward
                st = latest_bond_state.get(c)
                if st:
                    # Count trading days elapsed
                    prior_trade_days = [d for d in trading_days[:d_idx] if d >= st["last_date"]]
                    elapsed_trading_days = len(prior_trade_days)
                    if elapsed_trading_days <= max_carry_days:
                        # Carry forward spread
                        day_bond_spreads[c] = st["g_spread_bp"]
                        day_issuer_spreads.setdefault(ig, []).append(st["g_spread_bp"])

        if day_bond_spreads:
            eq_spread = float(np.mean(list(day_bond_spreads.values())))

            # Issuer-capped: average each issuer group first, then equal-weight issuers
            issuer_means = [float(np.mean(vals)) for vals in day_issuer_spreads.values()]
            issuer_capped_spread = float(np.mean(issuer_means))

            # Benchmark 5Y Treasury yield for reference
            t_5y = treasury_curve.get(cur_date, {}).get(5.0)

            daily_basket_series.append(
                {
                    "date": cur_date,
                    "g_spread_equal_weighted_bp": round(eq_spread, 1),
                    "g_spread_issuer_capped_bp": round(issuer_capped_spread, 1),
                    "treasury_5y_yield": round(t_5y, 2) if t_5y else None,
                    "active_bonds_count": len(day_bond_spreads),
                    "active_issuers_count": len(day_issuer_spreads),
                    "bonds_traded_today": bonds_traded_today,
                    "total_trades_today": day_trades,
                    "total_volume_today": round(day_volumes, 0),
                }
            )

    return daily_basket_series, latest_bond_state, cusip_info


def export_public_safe_datasets(
    daily_series: list,
    latest_bond_state: dict,
    cusip_info: dict,
    universe: list,
    output_dir: pathlib.Path,
    anchor_date: str,
):
    """Exports clean, public-safe JSON series with all CUSIPs strictly stripped."""
    output_dir.mkdir(parents=True, exist_ok=True)
    anchor_dt = datetime.strptime(anchor_date, "%Y-%m-%d")

    # 1. Daily Basket Spreads
    daily_file = output_dir / "acsi_bond_spreads_daily.json"
    with open(daily_file, "w") as f:
        json.dump(daily_series, f, indent=2)
    print(f"\n[Export] Saved {len(daily_series)} daily basket spread records to {daily_file}")

    # 2. Latest Bond Universe (Public Safe - NO CUSIPs)
    public_universe = []
    for idx, b in enumerate(universe, start=1):
        c = b["cusip"]
        info = cusip_info[c]
        st = latest_bond_state.get(c, {})

        last_d = st.get("last_date", b.get("last_trade_date"))
        if last_d:
            days_since = (anchor_dt - datetime.strptime(last_d, "%Y-%m-%d")).days
            status = "active" if days_since <= 2 else ("carried" if days_since <= 7 else "stale")
        else:
            days_since = 999
            status = "stale"

        public_universe.append(
            {
                "id": f"ACSI-BOND-{idx:02d}",
                "issuer": info["issuer"],
                "tenant_group": b.get("category", "AI Infrastructure"),
                "description": info["description"],
                "coupon_rate": info["coupon"],
                "maturity_date": info["maturity_str"],
                "is_144a": bool(info["is_144a"]),
                "last_trade_date": last_d,
                "days_since_last_trade": days_since,
                "latest_vway": round(st.get("vway"), 2) if st.get("vway") else None,
                "latest_vwap": round(st.get("vwap"), 2) if st.get("vwap") else None,
                "latest_g_spread_bp": round(st.get("g_spread_bp"), 1) if st.get("g_spread_bp") else None,
                "status": status,
            }
        )

    universe_file = output_dir / "acsi_bond_universe_latest.json"
    with open(universe_file, "w") as f:
        json.dump(public_universe, f, indent=2)
    print(f"[Export] Saved {len(public_universe)} public-safe bond records (NO CUSIPs) to {universe_file}")


def main():
    parser = argparse.ArgumentParser(description="AI Infrastructure Bond Credit Spread Generator")
    parser.add_argument("--trades", default="data/trace_trades_clean.parquet", help="Clean trades parquet")
    parser.add_argument("--universe", default="data/bonds_universe.json", help="Bonds universe JSON")
    parser.add_argument("--out", default="data", help="Output directory")
    args = parser.parse_args()

    # Load data
    trades_path = pathlib.Path(args.trades)
    if not trades_path.exists():
        trades_path = ROOT / args.trades
    if not trades_path.exists():
        sys.exit(f"Error: {args.trades} not found.")

    univ_path = pathlib.Path(args.universe)
    if not univ_path.exists():
        univ_path = ROOT / args.universe
    if not univ_path.exists():
        sys.exit(f"Error: {args.universe} not found.")

    df_trades = pd.read_parquet(trades_path)
    with open(univ_path) as f:
        universe = json.load(f)

    # 1. Freshness Audit
    anchor_date = df_trades["tradeExecutionDate"].max()
    audit_freshness_and_coverage(df_trades, universe, anchor_date)

    # 2. Fetch US Treasury Par Curve
    print("\nFetching official US Treasury Daily Par Yield Curve...")
    cache_path = pathlib.Path(args.out) / "treasury_curve_2026.csv"
    treasury_curve = fetch_treasury_curve(cache_path)
    print(f"  Ingested {len(treasury_curve)} trading days of Treasury yield curve tenors.")

    # 3. Process Daily Spreads and Aggregate Basket
    print("\nComputing daily Volume-Weighted Average Yields (VWAY) and G-Spreads...")
    daily_series, latest_bond_state, cusip_info = process_daily_bond_spreads(
        df_trades, universe, treasury_curve, max_carry_days=5
    )

    # 4. Export Public Safe JSONs
    out_dir = pathlib.Path(args.out)
    export_public_safe_datasets(
        daily_series, latest_bond_state, cusip_info, universe, out_dir, anchor_date
    )

    # Mirror to acsi-lab/data if needed
    alt_dir = ROOT / "data"
    if alt_dir.resolve() != out_dir.resolve():
        export_public_safe_datasets(
            daily_series, latest_bond_state, cusip_info, universe, alt_dir, anchor_date
        )

    # 5. Summary Results
    print("\n" + "=" * 80)
    print("AI INFRASTRUCTURE CREDIT SPREAD BENCHMARK SUMMARY (aistress.si)")
    print("=" * 80)

    latest = daily_series[-1]
    print(f"Latest Date: {latest['date']}")
    print(f"  • Equal-Weighted 24-Bond G-Spread:  {latest['g_spread_equal_weighted_bp']:>7.1f} bps")
    print(f"  • Issuer-Capped G-Spread:           {latest['g_spread_issuer_capped_bp']:>7.1f} bps")
    print(f"  • Active Bonds: {latest['active_bonds_count']}/24 | Active Issuers: {latest['active_issuers_count']}/19")
    print(f"  • Same-Day Trades: {latest['total_trades_today']} prints | Traded Volume: ${latest['total_volume_today']:,.0f}")
    print(f"  • Matching 5Y US Treasury Yield:    {latest['treasury_5y_yield']}%")

    # 30-Day Change
    if len(daily_series) >= 22:
        prior_30d = daily_series[-22]  # ~22 trading days ≈ 1 calendar month
        d_eq = latest["g_spread_equal_weighted_bp"] - prior_30d["g_spread_equal_weighted_bp"]
        d_cap = latest["g_spread_issuer_capped_bp"] - prior_30d["g_spread_issuer_capped_bp"]
        print(f"\n30-Day Trend (vs {prior_30d['date']}):")
        print(f"  • Equal-Weighted 24-Bond Change: {d_eq:>+7.1f} bps ({prior_30d['g_spread_equal_weighted_bp']:.1f} -> {latest['g_spread_equal_weighted_bp']:.1f})")
        print(f"  • Issuer-Capped Change:          {d_cap:>+7.1f} bps ({prior_30d['g_spread_issuer_capped_bp']:.1f} -> {latest['g_spread_issuer_capped_bp']:.1f})")
    print("=" * 80)


if __name__ == "__main__":
    main()
