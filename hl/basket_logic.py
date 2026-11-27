"""Pure sizing, rounding and delta logic for the Stack proxy basket. No network, no keys. Unit-tested."""
import math
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN

# Fitted coefficients: junior points per 100% move in the underlying (core4 fit, 2025-04-01 to 2026-11-26,
# backfilled research series). Negative = short. XYZ100 stands in for QQQ in the fit.
DEFAULT_WEIGHTS = {'xyz:ORCL': -64.41, 'xyz:IREN': -26.52, 'xyz:NBIS': -15.36, 'xyz:XYZ100': 200.19}

PERP_MAX_DECIMALS = 6
MAX_SIG_FIGS = 5


def usd_per_point(weights, gross):
    """Fitted basket P&L per junior point for a given gross notional."""
    g = sum(abs(v) for v in weights.values())
    return gross / g


def target_notionals(weights, gross):
    """Signed USD notional per leg, scaled so the absolute notionals sum to gross."""
    g = sum(abs(v) for v in weights.values())
    if g <= 0:
        raise ValueError('weights sum to zero')
    return {c: w / g * gross for c, w in weights.items()}


def round_size(sz, sz_decimals):
    """Round an absolute size down to the asset's size step (never overshoot the target)."""
    q = Decimal(1).scaleb(-sz_decimals)
    return float(Decimal(str(abs(sz))).quantize(q, rounding=ROUND_DOWN))


def round_size_nearest(sz, sz_decimals):
    q = Decimal(1).scaleb(-sz_decimals)
    return float(Decimal(str(abs(sz))).quantize(q, rounding=ROUND_HALF_EVEN))


def price_decimals(px, sz_decimals):
    """Decimals allowed for a perp price: at most 5 significant figures and at most 6 - szDecimals decimals.
    Integer prices are always allowed."""
    if px <= 0:
        raise ValueError('price must be positive')
    int_digits = int(math.floor(math.log10(px))) + 1
    by_sig = max(MAX_SIG_FIGS - int_digits, 0)
    return min(by_sig, PERP_MAX_DECIMALS - sz_decimals)


def round_price(px, sz_decimals, is_buy):
    """Valid tick price that never makes the order more aggressive than px: buys round down, sells round up."""
    d = price_decimals(px, sz_decimals)
    q = Decimal(1).scaleb(-d)
    p = Decimal(str(px)).quantize(q, rounding=ROUND_DOWN if is_buy else ROUND_UP)
    # rounding up can add a digit (e.g. 99999.5 -> 100000); re-check sig figs
    d2 = price_decimals(float(p), sz_decimals)
    if d2 < d:
        p = p.quantize(Decimal(1).scaleb(-d2), rounding=ROUND_DOWN if is_buy else ROUND_UP)
    return float(p)


def limit_price(mid, is_buy, slippage, sz_decimals):
    """IOC limit at mid +/- slippage, rounded so the cap is never exceeded."""
    raw = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
    return round_price(raw, sz_decimals, is_buy)


def plan_leg(coin, target_usd, pos_sz, mid, sz_decimals, band=0.10, min_order_usd=10.0):
    """Orders needed to move one leg from pos_sz (signed contracts) to target_usd (signed USD).

    Returns (orders, note). Each order: {coin, is_buy, sz, reduce_only, usd}.
    Rules:
      - skip when |delta| < max(band * |target|, min_order_usd)
      - sign flip: a reduce-only close of the whole position, then an opening order for the target
      - shrinking on the same side: reduce-only
      - growing: normal order
      - any order under min_order_usd is dropped (HL minimum order value), except a reduce-only full close
    """
    tgt_sz = round_size_nearest(target_usd / mid, sz_decimals) * (1 if target_usd >= 0 else -1)
    if tgt_sz == 0 and pos_sz != 0:
        # zero target: close whatever is there, even dust below the band or the minimum
        close = round_size_nearest(pos_sz, sz_decimals)
        if close > 0:
            return [{'coin': coin, 'is_buy': pos_sz < 0, 'sz': close, 'reduce_only': True, 'usd': close * mid}], 'close'
    delta = tgt_sz - pos_sz
    delta_usd = abs(delta) * mid
    thresh = max(band * abs(target_usd), min_order_usd)
    if delta_usd < thresh:
        return [], f'within band (|delta| ${delta_usd:,.2f} < ${thresh:,.2f})'
    orders = []
    flip = pos_sz != 0 and tgt_sz != 0 and (pos_sz > 0) != (tgt_sz > 0)
    if flip or tgt_sz == 0:
        close = round_size(pos_sz, sz_decimals)
        if close > 0:
            orders.append({'coin': coin, 'is_buy': pos_sz < 0, 'sz': close, 'reduce_only': True, 'usd': close * mid})
        if tgt_sz != 0:
            sz = round_size(tgt_sz, sz_decimals)
            if sz * mid >= min_order_usd:
                orders.append({'coin': coin, 'is_buy': tgt_sz > 0, 'sz': sz, 'reduce_only': False, 'usd': sz * mid})
        return orders, 'flip' if flip else 'close'
    shrinking = pos_sz != 0 and abs(tgt_sz) < abs(pos_sz)
    sz = round_size(delta, sz_decimals)
    if sz == 0 or sz * mid < min_order_usd:
        return [], f'below minimum order (${sz * mid:,.2f})'
    orders.append({'coin': coin, 'is_buy': delta > 0, 'sz': sz, 'reduce_only': shrinking, 'usd': sz * mid})
    return orders, 'shrink' if shrinking else ('open' if pos_sz == 0 else 'grow')


def plan_flatten(positions, mids, sz_decimals):
    """Reduce-only closes for every non-zero basket position."""
    out = []
    for coin, pos in positions.items():
        sz = round_size_nearest(pos, sz_decimals[coin]) if pos else 0
        if sz > 0:
            out.append({'coin': coin, 'is_buy': pos < 0, 'sz': sz, 'reduce_only': True,
                        'usd': abs(pos) * mids[coin]})
    return out


def funding_cost_hourly(funding_rate, side):
    """Hourly funding cost for our side (positive = we pay). HL: positive funding means longs pay shorts."""
    return funding_rate * (1 if side > 0 else -1)


def effective_leverage(requested, asset_max):
    return max(1, min(int(requested), int(asset_max)))


def parse_order_response(resp, n):
    """Turn an exchange order response into n per-order results: {'ok', 'filled', 'resting', 'error'}.
    HL returns {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [...]}}} where each status is
    {'filled': {'totalSz', 'avgPx', 'oid'}}, {'resting': {'oid'}} or {'error': msg}; or {'status': 'err', ...}."""
    bad = {'ok': False, 'filled': 0.0, 'resting': None, 'error': None}
    if not isinstance(resp, dict) or resp.get('status') != 'ok':
        return [dict(bad, error=f'request rejected: {str(resp)[:200]}') for _ in range(n)]
    try:
        sts = resp['response']['data']['statuses']
    except (KeyError, TypeError):
        return [dict(bad, error=f'unexpected response: {str(resp)[:200]}') for _ in range(n)]
    out = []
    for i in range(n):
        st = sts[i] if i < len(sts) else None
        if not isinstance(st, dict):
            out.append(dict(bad, error='missing status'))
        elif 'error' in st:
            out.append(dict(bad, error=str(st['error'])[:200]))
        elif 'filled' in st:
            out.append({'ok': True, 'filled': float(st['filled'].get('totalSz', 0)), 'resting': None, 'error': None})
        elif 'resting' in st:
            out.append({'ok': True, 'filled': 0.0, 'resting': st['resting'].get('oid'), 'error': None})
        else:
            out.append(dict(bad, error=f'unknown status {str(st)[:100]}'))
    return out


def projected_gross(positions, orders, prices):
    """Sum of |position after all orders| x price, using the worst (highest) price given per coin."""
    after = dict(positions)
    for o in orders:
        after[o['coin']] = after.get(o['coin'], 0.0) + (o['sz'] if o['is_buy'] else -o['sz'])
    return sum(abs(sz) * prices[c] for c, sz in after.items())
