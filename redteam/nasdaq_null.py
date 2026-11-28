"""Red team, finding #2 follow-up: are the Nasdaq "regime" numbers real or chance?

nasdaq_redteam.py reports a rolling 10-day R^2 peak of 0.797 and a weekly R^2 that can reach about 0.22. This script
checks three things those numbers depend on:

  1. Weekly start day. Non-overlapping 5-session changes can start on any of five sessions; it reports all five.
  2. Rolling-window peaks against chance. The index changes are rotated against the Nasdaq returns (a circular shift),
     which keeps each series' own autocorrelation but breaks any link between them. Every rotation of at least 15
     sessions is scored the same way. The p-value is the share of rotations that match or beat the real figure.
  3. Stress days. R^2 on the Nasdaq's worst 10% and 5% days, and the one-day lag (yesterday's Nasdaq on today's index).

Deterministic: no random draws.

Usage:
  python redteam/nasdaq_null.py [methods/v0.1-live.yaml ...]
"""

import argparse
import pathlib
import statistics
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from score import build, fred, load_trades, ols  # noqa: E402

MIN_SHIFT = 15


def paired(method_path, trades_path):
    method = yaml.safe_load(open(method_path))
    trades, cal = load_trades(trades_path)
    s = build(method, trades, cal)
    ndx = fred("NASDAQCOM")
    out = []
    for a, b in zip(s, s[1:]):
        if a["date"] in ndx and b["date"] in ndx:
            out.append({"date": b["date"], "d": b["value"] - a["value"], "r": ndx[b["date"]] / ndx[a["date"]] - 1.0,
                        "v": b["value"], "ndx": ndx[b["date"]]})
    return out


def rolling_r2(dg, rr, window):
    out = []
    for i in range(window, len(dg) + 1):
        res = ols(dg[i - window:i], rr[i - window:i])
        if res and res["r2"] is not None:
            out.append(res["r2"])
    return out


def r2(y, x):
    res = ols(y, x)
    return res["r2"] if res and res["r2"] is not None else float("nan")


def run(method_path, trades_path):
    p = paired(method_path, trades_path)
    n = len(p)
    dg = [q["d"] for q in p]
    rr = [q["r"] for q in p]
    v = [q["v"] for q in p]
    nd = [q["ndx"] for q in p]

    print("=" * 72)
    print(f"{pathlib.Path(method_path).name}: {n} paired sessions, {p[0]['date']} to {p[-1]['date']}")

    print(f"\nDaily R^2: {r2(dg, rr):.4f}")
    weekly = []
    for off in range(5):
        idx = list(range(5 + off, n, 5))
        weekly.append((off, len(idx), r2([v[i] - v[i - 5] for i in idx], [nd[i] / nd[i - 5] - 1 for i in idx])))
    print("Weekly (non-overlapping 5-session changes) R^2 by start session:")
    for off, k, val in weekly:
        print(f"  start +{off}: N={k:3d}  R^2={val:.3f}")
    vals = [w[2] for w in weekly]
    print(f"  range {min(vals):.3f} to {max(vals):.3f}, median {statistics.median(vals):.3f}")

    for window in (10, 20):
        obs = rolling_r2(dg, rr, window)
        o_max, o_mean = max(obs), statistics.mean(obs)
        maxes, means = [], []
        for k in range(MIN_SHIFT, n - MIN_SHIFT + 1):
            rot = dg[k:] + dg[:k]
            r = rolling_r2(rot, rr, window)
            maxes.append(max(r))
            means.append(statistics.mean(r))
        p_max = sum(m >= o_max for m in maxes) / len(maxes)
        p_mean = sum(m >= o_mean for m in means) / len(means)
        print(f"\nRolling {window}-session R^2 ({len(obs)} windows; null = {len(maxes)} rotations):")
        print(f"  peak  {o_max:.3f}  vs chance median peak {statistics.median(maxes):.3f}  p = {p_max:.2f}")
        print(f"  mean  {o_mean:.3f}  vs chance median mean {statistics.median(means):.3f}  p = {p_mean:.2f}")

    order = sorted(range(n), key=lambda i: rr[i])
    for share in (0.10, 0.05):
        k = max(5, int(share * n))
        idx = order[:k]
        moves = [dg[i] for i in idx]
        o_mean = statistics.mean(moves)
        null = [statistics.mean((dg[s:] + dg[:s])[i] for i in idx) for s in range(MIN_SHIFT, n - MIN_SHIFT + 1)]
        p_up = sum(m >= o_mean for m in null) / len(null)
        print(f"\nNasdaq worst {int(share * 100)}% days (N={k}, return <= {rr[idx[-1]]:.2%}): "
              f"R^2 = {r2(moves, [rr[i] for i in idx]):.4f}, mean index move {o_mean:+.2f} pts "
              f"(chance median {statistics.median(null):+.2f}, p = {p_up:.2f})")
    calm = order[int(0.10 * n):n - int(0.10 * n)]
    print(f"Middle 80% of days: mean index move {statistics.mean(dg[i] for i in calm):+.2f} pts")
    print(f"\nOne-day lag, Nasdaq(t-1) -> index(t): R^2 = {r2(dg[1:], rr[:-1]):.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("methods", nargs="*", default=[str(ROOT / "methods" / "v0.1-live.yaml"),
                                                    str(ROOT / "methods" / "v0.2-live.yaml")])
    ap.add_argument("--trades", default=str(ROOT / "data" / "canonical" / "cds_trades.csv"))
    args = ap.parse_args()
    for m in args.methods:
        run(m, args.trades)
