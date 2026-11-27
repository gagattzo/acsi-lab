#!/usr/bin/env python3
"""Print the current Stack junior proxy basket on Hyperliquid. Read-only: no orders, no keys.

Weights come from a constrained regression (core4, fit 2025-04-01 to 2026-11-26 on a backfilled research series,
not published editions). Long stress = short the AI borrowers, long the Nasdaq-100 perp to cancel market beta.
Usage: python -m hl.proxy_basket [gross_usd]   (default 10000)
"""
import json, sys, urllib.request

API = 'https://aistress-stack-api.palmer.workers.dev/latest'
HL = 'https://api.hyperliquid.xyz/info'
# points of junior score per 100% move in the underlying (fit coefficients); sign: negative = short
COEF = {'xyz:ORCL': -64.41, 'xyz:IREN': -26.52, 'xyz:NBIS': -15.36, 'xyz:XYZ100': 200.19}
FIT = 'walk-forward out of sample (302 days): R2 0.32, corr 0.57, direction hit 70%, tracking error 4.5 pts/day'


def get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={'content-type': 'application/json', 'user-agent': 'aistress-proxy-basket/0.1'})
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    gross = float(sys.argv[1]) if len(sys.argv) > 1 else 10000.0
    latest = get(API)
    j = latest.get('junior') or {}
    mids = get(HL, {'type': 'allMids', 'dex': 'xyz'})
    g = sum(abs(v) for v in COEF.values())
    print(f"Stack junior {j.get('score')} {j.get('band')} (as_of {latest.get('date')}, change {j.get('change')})")
    print(f"Proxy basket, ${gross:,.0f} gross, long stress. Moves about ${gross / g:,.2f} per junior point (fitted).")
    print(f"{'perp':<12}{'side':<7}{'notional $':>12}{'mid':>12}{'size':>12}")
    for coin, c in COEF.items():
        usd = c / g * gross
        mid = float(mids.get(coin, 'nan'))
        print(f"{coin:<12}{'short' if usd < 0 else 'long':<7}{abs(usd):>12,.0f}{mid:>12,.2f}{abs(usd) / mid:>12.3f}")
    print('Rebalance: reset notionals weekly (Monday); refit weights quarterly.')
    print(FIT)
    print('Blind to CDS-only moves (L2): captured about 25% of the CDS-driven index move on those days.')


if __name__ == '__main__':
    main()
