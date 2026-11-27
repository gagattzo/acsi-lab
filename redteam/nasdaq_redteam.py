"""Red team, finding #2: Stress-testing the Nasdaq correlation claim on ACSI v0.1.

Evaluates whether the index's independence claim (R^2 = 0.017) breaks down under:
  1. Baseline OLS: full sample, first half vs second half, last 60d, weekly changes, and levels.
  2. Rolling regressions: 10-day and 20-day windows to detect temporary high-beta / high-R^2 regimes.
  3. Tail regimes: behavior during severe Nasdaq selloffs (bottom 5% / 10%) vs rallies (top 5% / 10%).
  4. Lead/Lag structure: cross-correlations from -5 to +5 days to check if credit lags or leads equity.

Usage:
  python redteam/nasdaq_redteam.py [methods/v0.1-live.yaml]
"""

import argparse
import math
import pathlib
import statistics
import sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from score import build, fred, load_trades, ols, ROOT as SCORE_ROOT


def corr(res):
    if not res or res.get("r2") is None:
        return 0.0
    return math.copysign(math.sqrt(max(0.0, res["r2"])), res.get("beta", 0.0))


def run_redteam(method_path, trades_path):
    method = yaml.safe_load(open(method_path))
    trades, cal = load_trades(trades_path)
    s = build(method, trades, cal)
    ndx = fred("NASDAQCOM")

    # Align consecutive scored days
    paired = []
    for a, b in zip(s, s[1:]):
        da, db = a["date"], b["date"]
        if da in ndx and db in ndx:
            d_val = b["value"] - a["value"]
            r_ndx = ndx[db] / ndx[da] - 1.0
            paired.append(
                {
                    "date": db,
                    "prev_date": da,
                    "val": b["value"],
                    "d_val": d_val,
                    "ndx": ndx[db],
                    "r_ndx": r_ndx,
                }
            )

    n_total = len(paired)
    print("=" * 70)
    print(f"ACSI LAB RED-TEAM: NASDAQ CORRELATION STRESS TEST (N={n_total} days)")
    print(f"Period: {paired[0]['date']} to {paired[-1]['date']}")
    print("=" * 70)

    # 1. Baseline OLS
    dg = [p["d_val"] for p in paired]
    rr = [p["r_ndx"] for p in paired]

    ols_full = ols(dg, rr)
    half = n_total // 2
    ols_h1 = ols(dg[:half], rr[:half])
    ols_h2 = ols(dg[half:], rr[half:])
    ols_60 = ols(dg[-60:], rr[-60:])

    # Levels
    vals = [p["val"] for p in paired]
    ndx_lvl = [p["ndx"] for p in paired]
    ols_lvl = ols(vals, ndx_lvl)

    # Weekly (5-day changes)
    dg_5d = [paired[i]["val"] - paired[i - 5]["val"] for i in range(5, n_total, 5)]
    rr_5d = [paired[i]["ndx"] / paired[i - 5]["ndx"] - 1.0 for i in range(5, n_total, 5)]
    ols_weekly = ols(dg_5d, rr_5d)

    print("\n[1] BASELINE OLS REGRESSIONS")
    print(f"  • Full Period (N={ols_full['n']}): R^2 = {ols_full['r2']:.4f}, Beta = {ols_full['beta']/100:.3f} pts per 1% move, Corr = {corr(ols_full):+.3f}")
    print(f"  • First Half (N={ols_h1['n']}):  R^2 = {ols_h1['r2']:.4f}, Beta = {ols_h1['beta']/100:.3f} pts per 1% move, Corr = {corr(ols_h1):+.3f}")
    print(f"  • Second Half (N={ols_h2['n']}): R^2 = {ols_h2['r2']:.4f}, Beta = {ols_h2['beta']/100:.3f} pts per 1% move, Corr = {corr(ols_h2):+.3f}")
    print(f"  • Last 60 Days (N={ols_60['n']}): R^2 = {ols_60['r2']:.4f}, Beta = {ols_60['beta']/100:.3f} pts per 1% move, Corr = {corr(ols_60):+.3f}")
    print(f"  • Weekly 5d Changes (N={ols_weekly['n']}): R^2 = {ols_weekly['r2']:.4f}, Beta = {ols_weekly['beta']/100:.3f} pts per 1% move, Corr = {corr(ols_weekly):+.3f}")
    print(f"  • Levels (Misleading Trending Baseline): R^2 = {ols_lvl['r2']:.4f}, Corr = {corr(ols_lvl):+.3f}")

    # 2. Rolling Regressions
    def rolling_eval(window):
        r2s, betas = [], []
        detail = []
        for i in range(window, n_total + 1):
            w_dg = dg[i - window : i]
            w_rr = rr[i - window : i]
            res = ols(w_dg, w_rr)
            if res and res["r2"] is not None:
                r2s.append(res["r2"])
                betas.append(res["beta"] / 100)
                detail.append(
                    {
                        "end_date": paired[i - 1]["date"],
                        "r2": res["r2"],
                        "beta": res["beta"] / 100,
                    }
                )
        detail.sort(key=lambda x: -x["r2"])
        return {
            "window": window,
            "mean_r2": statistics.mean(r2s),
            "median_r2": statistics.median(r2s),
            "p90_r2": sorted(r2s)[int(0.90 * len(r2s))],
            "p95_r2": sorted(r2s)[int(0.95 * len(r2s))],
            "max_r2": max(r2s),
            "top_windows": detail[:3],
        }

    r10 = rolling_eval(10)
    r20 = rolling_eval(20)

    print("\n[2] ROLLING REGRESSIONS (Instability & Regime Detection)")
    print(f"  • Rolling 10-Day Windows (N={len(dg)-9}):")
    print(f"      Median R^2: {r10['median_r2']:.3f} | Mean R^2: {r10['mean_r2']:.3f} | 95th Percentile: {r10['p95_r2']:.3f}")
    print(f"      PEAK R^2: {r10['max_r2']:.3f} (Beta: {r10['top_windows'][0]['beta']:.2f} pts/1% on window ending {r10['top_windows'][0]['end_date']})")
    print(f"      Top 3 windows: " + ", ".join(f"{w['end_date']} (R^2={w['r2']:.3f}, β={w['beta']:.2f})" for w in r10['top_windows']))

    print(f"  • Rolling 20-Day Windows (N={len(dg)-19}):")
    print(f"      Median R^2: {r20['median_r2']:.3f} | Mean R^2: {r20['mean_r2']:.3f} | 95th Percentile: {r20['p95_r2']:.3f}")
    print(f"      PEAK R^2: {r20['max_r2']:.3f} (Beta: {r20['top_windows'][0]['beta']:.2f} pts/1% on window ending {r20['top_windows'][0]['end_date']})")
    print(f"      Top 3 windows: " + ", ".join(f"{w['end_date']} (R^2={w['r2']:.3f}, β={w['beta']:.2f})" for w in r20['top_windows']))

    # 3. Tail-Regime / Asymmetry Analysis
    sorted_ndx = sorted(paired, key=lambda x: x["r_ndx"])
    n10 = max(5, int(0.10 * n_total))
    selloffs_10 = sorted_ndx[:n10]
    rallies_10 = sorted_ndx[-n10:]

    n05 = max(5, int(0.05 * n_total))
    selloffs_05 = sorted_ndx[:n05]
    rallies_05 = sorted_ndx[-n05:]

    ols_sell10 = ols([p["d_val"] for p in selloffs_10], [p["r_ndx"] for p in selloffs_10])
    ols_rally10 = ols([p["d_val"] for p in rallies_10], [p["r_ndx"] for p in rallies_10])
    ols_sell05 = ols([p["d_val"] for p in selloffs_05], [p["r_ndx"] for p in selloffs_05])

    print("\n[3] TAIL-REGIME & ASYMMETRY ANALYSIS")
    print(f"  • Nasdaq Worst 10% Selloffs (N={len(selloffs_10)}, return <= {selloffs_10[-1]['r_ndx']:.2%}):")
    print(f"      R^2 = {ols_sell10['r2']:.4f}, Beta = {ols_sell10['beta']/100:.2f} pts/1%, Corr = {corr(ols_sell10):+.3f}")
    print(f"      Mean index move on selloffs: {statistics.mean(p['d_val'] for p in selloffs_10):+.2f} pts vs calm days: {statistics.mean(p['d_val'] for p in sorted_ndx[n10:-n10]):+.2f} pts")

    print(f"  • Nasdaq Worst 5% Extreme Selloffs (N={len(selloffs_05)}, return <= {selloffs_05[-1]['r_ndx']:.2%}):")
    print(f"      R^2 = {ols_sell05['r2']:.4f}, Beta = {ols_sell05['beta']/100:.2f} pts/1%, Corr = {corr(ols_sell05):+.3f}")

    print(f"  • Nasdaq Best 10% Rallies (N={len(rallies_10)}, return >= {rallies_10[0]['r_ndx']:.2%}):")
    print(f"      R^2 = {ols_rally10['r2']:.4f}, Beta = {ols_rally10['beta']/100:.2f} pts/1%, Corr = {corr(ols_rally10):+.3f}")

    # 4. Lead / Lag Structure
    print("\n[4] LEAD / LAG STRUCTURE (Cross-Correlation across ±5 Trading Days)")
    print(f"  {'Lag (Days)':<12} {'Specification':<42} {'R^2':>7} {'Beta':>12} {'Corr':>8}")
    print("  " + "-" * 75)

    lead_lag = []
    for k in range(-5, 6):
        if k < 0:
            y = dg[-k:]
            x = rr[:k]
            res = ols(y, x)
            spec = f"Nasdaq(t{k}) -> ACSI(t) [Credit lags by {-k}d]"
            b_str = f"{res['beta']/100:+.2f}"
        elif k > 0:
            y = rr[k:]
            x = dg[:-k]
            res = ols(y, x)
            spec = f"ACSI(t) -> Nasdaq(t+{k}) [Credit leads by {k}d]"
            b_str = f"{res['beta']*100:+.2f}"
        else:
            res = ols_full
            spec = "Contemporaneous: Nasdaq(t) -> ACSI(t)"
            b_str = f"{res['beta']/100:+.2f}"

        lead_lag.append((k, spec, res))
        print(f"  {k:<12} {spec:<42} {res['r2']:>7.4f} {b_str:>12} {corr(res):>+8.3f}")

    print("=" * 70)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("method", nargs="?", default=str(ROOT / "methods" / "v0.1-live.yaml"))
    ap.add_argument("--trades", default=str(ROOT / "data" / "canonical" / "cds_trades.csv"))
    args = ap.parse_args()
    run_redteam(args.method, args.trades)
