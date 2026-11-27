#!/usr/bin/env python3
"""Stack junior proxy basket tracker for Hyperliquid (xyz HIP-3 dex).

Dry-run by default: prints the plan and places nothing. Real orders only with --live.

  python -m hl.tracker                    # dry-run plan (positions read from HL_ACCOUNT if set, else assumed flat)
  python -m hl.tracker --live             # rebalance to target with IOC orders
  python -m hl.tracker --flatten          # plan reduce-only closes of every basket leg (add --live to send)
  python -m hl.tracker --status           # positions, PnL, funding, tracking against the junior score

Env: HL_AGENT_KEY (API wallet private key, only needed for --live), HL_ACCOUNT (master account address).
The key is read from the environment only, never from argv, and is never printed or logged.

The basket is a fixed-weight replica of the Stack junior score (core4 fit). There is no signal-driven scaling:
`exposure_scale` in the config multiplies gross size and defaults to 1.0. Rebalance means resetting each leg to its
target notional (weekly, Monday, US cash session).
"""
import argparse, datetime as dt, fcntl, json, math, os, pathlib, re, sys, time, urllib.request
from zoneinfo import ZoneInfo

from hl import basket_logic as bl

HERE = pathlib.Path(__file__).resolve().parent.parent  # repo root: relative paths resolve here
URLS = {'mainnet': 'https://api.hyperliquid.xyz', 'testnet': 'https://api.hyperliquid-testnet.xyz'}
DEX = 'xyz'
DEFAULTS = {
    'network': 'mainnet',
    'gross_usd': 1000.0,             # sum of |leg notional|
    'exposure_scale': 1.0,           # plain multiplier on gross_usd; no rule changes it automatically
    'weights': dict(bl.DEFAULT_WEIGHTS),
    'rebalance_band': 0.10,          # skip a leg when |delta| < band x |target|
    'min_order_usd': 10.0,           # HL minimum order value
    'max_leverage': 2,               # isolated leverage per leg, capped at each asset's max
    'slippage': 0.005,               # IOC limit = mid x (1 +/- slippage)
    'order_style': 'ioc',            # 'ioc' or 'alo' (post-only at mid, may not fill)
    'max_total_notional_usd': 5000.0,
    'max_funding_cost_hourly': 0.0001,   # abort if our side would pay more than 0.01% per hour on any leg
    'max_mid_oracle_dev': 0.02,      # abort if mid is more than 2% away from the oracle (stale or internal pricing)
    'require_us_cash_session': True, # --live only inside Mon-Fri 09:30-16:00 America/New_York
    'index_url': 'https://aistress-stack-api.palmer.workers.dev/latest',
    'log_path': 'data/hl/hl_tracker.jsonl',
}


class Abort(Exception):
    pass


# --- io ---------------------------------------------------------------------------------------------------------------

def load_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    if path:
        text = pathlib.Path(path).read_text()
        if path.endswith(('.yaml', '.yml')):
            import yaml
            user = yaml.safe_load(text) or {}
        else:
            user = json.loads(text)
        unknown = set(user) - set(DEFAULTS)
        if unknown:
            raise Abort(f'unknown config keys: {sorted(unknown)}')
        cfg.update(user)
    for k in ('HL_AGENT_KEY',):
        if k in json.dumps(cfg):
            raise Abort('config must not contain keys')
    validate_config(cfg)
    return cfg


def _num(cfg, k, lo, hi, lo_open=False):
    v = cfg[k]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Abort(f'config {k} must be a finite number')
    if v < lo or v > hi or (lo_open and v == lo):
        raise Abort(f'config {k}={v} out of range ({"(" if lo_open else "["}{lo}, {hi}])')


def validate_config(cfg):
    if cfg['network'] not in URLS:
        raise Abort(f'config network must be one of {sorted(URLS)}')
    _num(cfg, 'gross_usd', 0, 1e7, lo_open=True)
    _num(cfg, 'exposure_scale', 0, 10)
    _num(cfg, 'max_total_notional_usd', 0, 1e7, lo_open=True)
    _num(cfg, 'rebalance_band', 0, 1)
    _num(cfg, 'min_order_usd', 10, 1e6)
    _num(cfg, 'max_leverage', 1, 50)
    _num(cfg, 'slippage', 0, 0.05, lo_open=True)
    _num(cfg, 'max_funding_cost_hourly', 0, 0.01)
    _num(cfg, 'max_mid_oracle_dev', 0, 0.2, lo_open=True)
    if cfg['order_style'] not in ('ioc', 'alo'):
        raise Abort('config order_style must be ioc or alo')
    w = cfg['weights']
    if not isinstance(w, dict) or not w or any(not isinstance(c, str) or not c.startswith(DEX + ':') for c in w):
        raise Abort(f'config weights must map {DEX}:COIN names to numbers')
    for c, v in w.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise Abort(f'config weight for {c} must be a finite number')
    if sum(abs(v) for v in w.values()) <= 0:
        raise Abort('config weights sum to zero')


ADDR_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')


def checked_account(raw):
    """HL_ACCOUNT must be a 20-byte hex address. The value is never echoed (a pasted private key must not leak)."""
    if raw is None or raw == '':
        return None
    if not ADDR_RE.match(raw.strip()):
        raise Abort('HL_ACCOUNT is not a 0x-prefixed 40-hex-char address (value not shown)')
    return raw.strip().lower()


class RunLock:
    """One tracker run at a time per network and account (overlapping runs would double trades)."""
    def __init__(self, path):
        self.path = path
        self.f = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, 'w')
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise Abort(f'another tracker run holds {self.path}')
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def http_json(url, body=None, retries=3, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={'content-type': 'application/json',
                                                          'user-agent': 'aistress-hl-tracker/0.1'})
    last = None
    for i in range(retries):
        try:
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as e:  # network errors are retried, then raised
            last = e
            time.sleep(1 + i)
    raise last


class Log:
    def __init__(self, path):
        self.path = (HERE / path) if not os.path.isabs(path) else pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = os.environ.get('HL_AGENT_KEY') or None

    def write(self, event, **data):
        rec = {'ts': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'event': event, **data}
        line = json.dumps(rec, default=str, sort_keys=True)
        if self._secret and (self._secret in line or self._secret.removeprefix('0x') in line):
            raise Abort('refusing to log a line containing the agent key')
        with open(self.path, 'a') as f:
            f.write(line + '\n')

    def entries(self, event=None):
        if not self.path.exists():
            return []
        out = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
        return [e for e in out if event is None or e['event'] == event]


# --- market data ------------------------------------------------------------------------------------------------------

def market(cfg, strict=True):
    """Basket legs with asset id, szDecimals, max leverage, margin mode, mid, oracle, mark and hourly funding.
    Raises Abort on a missing dex, a missing leg or data that looks stale. strict=False (flatten) turns the
    mid-vs-oracle check into a warning so the kill switch still works when one leg looks off."""
    base = URLS[cfg['network']]
    dexs = http_json(base + '/info', {'type': 'perpDexs'})
    idx = next((i for i, d in enumerate(dexs) if d and d.get('name') == DEX), None)
    if idx is None:
        raise Abort(f'{DEX} dex not found on {cfg["network"]}')
    meta, ctxs = http_json(base + '/info', {'type': 'metaAndAssetCtxs', 'dex': DEX})
    mids = http_json(base + '/info', {'type': 'allMids', 'dex': DEX})
    legs, warnings = {}, []
    for i, (a, c) in enumerate(zip(meta['universe'], ctxs)):
        if a['name'] not in cfg['weights']:
            continue
        if a.get('isDelisted') and strict:
            raise Abort(f'{a["name"]} is delisted')
        try:
            mid = float(mids[a['name']])
            oracle, mark = float(c['oraclePx']), float(c['markPx'])
            funding = float(c['funding'])
            if min(mid, oracle, mark) <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            if strict:
                raise Abort(f'{a["name"]}: missing or non-positive price or funding data')
            warnings.append(f'{a["name"]}: missing price data, cannot close it here')
            continue
        dev = abs(mid / oracle - 1)
        if dev > cfg['max_mid_oracle_dev']:
            msg = f'{a["name"]}: mid {mid} is {dev:.2%} from oracle {oracle} (stale or off-hours pricing)'
            if strict:
                raise Abort(msg)
            warnings.append(msg)
        legs[a['name']] = {'asset': 100000 + idx * 10000 + i, 'index_in_meta': i, 'sz_decimals': a['szDecimals'],
                           'max_leverage': a['maxLeverage'], 'only_isolated': bool(a.get('onlyIsolated')),
                           'margin_mode': a.get('marginMode') or 'normal', 'mid': mid, 'oracle': oracle,
                           'mark': mark, 'funding_hourly': funding, 'open_interest': float(c['openInterest'])}
    missing = set(cfg['weights']) - set(legs)
    if missing and strict:
        raise Abort(f'legs not on {DEX}: {sorted(missing)}')
    if missing:
        warnings.append(f'legs not on {DEX}, cannot close here: {sorted(missing)}')
    return {'dex_index': idx, 'legs': legs, 'warnings': warnings}


def junior(cfg):
    try:
        j = http_json(cfg['index_url'])
        return {'date': j.get('date'), 'score': (j.get('junior') or {}).get('score'),
                'band': (j.get('junior') or {}).get('band'), 'change': (j.get('junior') or {}).get('change')}
    except Exception as e:
        return {'error': f'index read failed: {type(e).__name__}'}


def positions(cfg, account):
    """{coin: signed size} plus raw position rows for the basket legs, read from the master account on the xyz dex."""
    st = http_json(URLS[cfg['network']] + '/info', {'type': 'clearinghouseState', 'user': account, 'dex': DEX})
    if not isinstance(st, dict) or 'assetPositions' not in st:
        raise Abort('clearinghouseState read failed')
    pos, rows = {c: 0.0 for c in cfg['weights']}, {}
    for ap in st.get('assetPositions', []):
        p = ap.get('position', {})
        if p.get('coin') in pos:
            pos[p['coin']] = float(p['szi'])
            rows[p['coin']] = p
    return pos, rows, st


def open_basket_orders(cfg, account):
    oo = http_json(URLS[cfg['network']] + '/info', {'type': 'openOrders', 'user': account, 'dex': DEX})
    if not isinstance(oo, list):
        raise Abort('openOrders read failed')
    return [o for o in oo if o.get('coin') in cfg['weights']]


def us_cash_session(now=None):
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo('America/New_York'))
    return t.weekday() < 5 and dt.time(9, 30) <= t.time() < dt.time(16, 0), t


# --- planning -----------------------------------------------------------------------------------------------------------

def build_plan(cfg, mk, pos, flatten=False):
    legs = mk['legs']
    gross = cfg['gross_usd'] * cfg['exposure_scale']
    if gross > cfg['max_total_notional_usd'] and not flatten:
        raise Abort(f'gross ${gross:,.0f} exceeds max_total_notional_usd ${cfg["max_total_notional_usd"]:,.0f}')
    targets = {c: 0.0 for c in legs} if flatten else bl.target_notionals(cfg['weights'], gross)
    rows, orders, problems = [], [], []
    for coin, L in legs.items():
        pos.setdefault(coin, 0.0)
        side = 1 if targets[coin] > 0 else -1 if targets[coin] < 0 else 0
        cost = bl.funding_cost_hourly(L['funding_hourly'], side) if side else 0.0
        if flatten:
            o = bl.plan_flatten({coin: pos[coin]}, {coin: L['mid']}, {coin: L['sz_decimals']})
            note = 'close' if o else 'flat'
        else:
            o, note = bl.plan_leg(coin, targets[coin], pos[coin], L['mid'], L['sz_decimals'],
                                  cfg['rebalance_band'], cfg['min_order_usd'])
            if side and cost > cfg['max_funding_cost_hourly']:
                problems.append(f'{coin}: funding cost {cost:.6f}/h exceeds {cfg["max_funding_cost_hourly"]}/h')
        for x in o:
            if cfg['order_style'] == 'alo' and not x['reduce_only']:
                x['tif'], x['limit_px'] = 'Alo', bl.round_price(L['mid'], L['sz_decimals'], x['is_buy'])
            else:
                x['tif'] = 'Ioc'
                x['limit_px'] = bl.limit_price(L['mid'], x['is_buy'], cfg['slippage'], L['sz_decimals'])
        orders.extend(o)
        rows.append({'coin': coin, 'asset': L['asset'], 'target_usd': round(targets[coin], 2),
                     'pos_sz': pos[coin], 'pos_usd': round(pos[coin] * L['mid'], 2), 'mid': L['mid'],
                     'oracle': L['oracle'], 'funding_h': L['funding_hourly'], 'our_cost_h': cost,
                     'lev': bl.effective_leverage(cfg['max_leverage'], L['max_leverage']),
                     'margin': 'isolated-only' if L['only_isolated'] else 'cross allowed (we use isolated)',
                     'action': note, 'orders': [(('BUY' if x['is_buy'] else 'SELL'), x['sz'], x['limit_px'],
                                                 x['tif'], 'RO' if x['reduce_only'] else '') for x in o]})
    orders.sort(key=lambda x: not x['reduce_only'])  # reduce-only first frees margin
    if not flatten:
        worst = {c: max(L['mid'], L['oracle'], L['mark']) * (1 + cfg['slippage']) for c, L in legs.items()}
        proj = bl.projected_gross({c: pos[c] for c in legs}, orders, worst)
        if proj > cfg['max_total_notional_usd']:
            problems.append(f'projected gross ${proj:,.0f} after orders exceeds max_total_notional_usd '
                            f'${cfg["max_total_notional_usd"]:,.0f}')
    return {'gross': gross, 'usd_per_point': bl.usd_per_point(cfg['weights'], gross), 'rows': rows,
            'orders': orders, 'problems': problems}


def print_plan(plan, jr, mk, mode):
    print(f"Stack junior: {jr.get('score')} {jr.get('band')} (as_of {jr.get('date')}, change {jr.get('change')})"
          if 'error' not in jr else f"Stack junior: {jr['error']}")
    print(f"Mode: {mode} | xyz dex index {mk['dex_index']} | gross ${plan['gross']:,.2f} | "
          f"fitted ${plan['usd_per_point']:,.2f} per junior point")
    hdr = f"{'leg':<11}{'asset':>7}{'target $':>10}{'pos sz':>10}{'pos $':>9}{'mid':>11}{'fund/h':>11}{'cost/h':>11}{'lev':>4}  action / orders"
    print(hdr)
    print('-' * len(hdr))
    for r in plan['rows']:
        o = '; '.join(f'{s} {sz} @ {px} {tif} {ro}'.strip() for s, sz, px, tif, ro in r['orders'])
        print(f"{r['coin']:<11}{r['asset']:>7}{r['target_usd']:>10,.2f}{r['pos_sz']:>10g}{r['pos_usd']:>9,.2f}"
              f"{r['mid']:>11,.6g}{r['funding_h']:>11.7f}{r['our_cost_h']:>11.7f}{r['lev']:>4}  {r['action']}"
              f"{(' | ' + o) if o else ''}")
    for p in plan['problems']:
        print('PROBLEM:', p)


# --- execution --------------------------------------------------------------------------------------------------------

def exchange(cfg, account):
    key = os.environ.get('HL_AGENT_KEY')
    if not key:
        raise Abort('HL_AGENT_KEY not set')
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    try:
        wallet = Account.from_key(key)
    except Exception:
        raise Abort('HL_AGENT_KEY is not a valid private key (value not shown)')
    finally:
        del key
    verify_signer(cfg, wallet.address, account)
    ex = Exchange(wallet, URLS[cfg['network']], account_address=account, perp_dexs=['', DEX], timeout=15)
    return ex, wallet.address


def verify_signer(cfg, agent_addr, account):
    """The SDK does not put account_address into the signed order: HL derives the trading account from the signer.
    So check on chain that the key is an API wallet approved by exactly HL_ACCOUNT, and that HL_ACCOUNT is a plain
    user (vaults and sub-accounts need vault_address handling this script does not do)."""
    url = URLS[cfg['network']] + '/info'
    role = http_json(url, {'type': 'userRole', 'user': agent_addr})
    if not isinstance(role, dict) or role.get('role') != 'agent':
        raise Abort(f'signer {agent_addr} is not an approved API wallet (userRole: {role})')
    owner = str((role.get('data') or {}).get('user', '')).lower()
    if owner != account.lower():
        raise Abort(f'API wallet {agent_addr} belongs to {owner}, not HL_ACCOUNT {account}')
    acct = http_json(url, {'type': 'userRole', 'user': account})
    if not isinstance(acct, dict) or acct.get('role') != 'user':
        raise Abort(f'HL_ACCOUNT role is {acct}; only plain user accounts are supported')


def check_sdk_assets(info, mk):
    """The SDK numbers builder dexes itself (110000 + i * 10000). Make sure it agrees with the docs formula."""
    for coin, L in mk['legs'].items():
        got = info.name_to_asset(coin)
        if got != L['asset']:
            raise Abort(f'SDK asset id {got} for {coin} != {L["asset"]} from perpDexs/meta')


def _send(cfg, ex, o, mk, log, lev_done, problems):
    coin = o['coin']
    if not o['reduce_only'] and coin not in lev_done:
        lev = bl.effective_leverage(cfg['max_leverage'], mk['legs'][coin]['max_leverage'])
        r = ex.update_leverage(lev, coin, is_cross=False)
        log.write('set_leverage', coin=coin, leverage=lev, isolated=True, response=r)
        if not isinstance(r, dict) or r.get('status') != 'ok':
            problems.append(f'update_leverage failed for {coin}: {str(r)[:200]}')
            return None
        lev_done.add(coin)
    r = ex.order(coin, o['is_buy'], o['sz'], o['limit_px'], {'limit': {'tif': o['tif']}}, o['reduce_only'])
    res = bl.parse_order_response(r, 1)[0]
    log.write('order', order=o, response=r, result=res)
    print('ORDER', 'BUY' if o['is_buy'] else 'SELL', o['sz'], coin, '@', o['limit_px'], o['tif'],
          'RO' if o['reduce_only'] else '', '->', 'filled ' + str(res['filled']) if res['ok'] and not res['resting']
          else ('resting oid ' + str(res['resting']) if res['resting'] else 'ERROR ' + str(res['error'])))
    if not res['ok']:
        problems.append(f'{coin}: order rejected: {res["error"]}')
    elif res['resting']:
        problems.append(f'{coin}: order resting (oid {res["resting"]}), not filled yet')
    elif res['filled'] + 1e-12 < o['sz']:
        problems.append(f'{coin}: partial fill {res["filled"]} of {o["sz"]}')
    return res


def execute(cfg, ex, plan, mk, log, account, flatten=False):
    """Two phases. Phase 1 sends reduce-only orders. Then positions are re-read and the plan rebuilt, and phase 2
    sends only opening orders for legs whose fresh plan needs no more closing (a partly filled flip close never
    gets an opening order on top). Returns a list of problems; empty means every order filled in full."""
    problems, lev_done = [], set()
    for o in [o for o in plan['orders'] if o['reduce_only']]:
        _send(cfg, ex, o, mk, log, lev_done, problems)
    if flatten:
        return problems
    if any(o['reduce_only'] for o in plan['orders']):
        time.sleep(1)
    pos2, _, _ = positions(cfg, account)
    mk2 = market(cfg)
    plan2 = build_plan(cfg, mk2, pos2)
    if plan2['problems']:
        return problems + ['phase 2 skipped: ' + '; '.join(plan2['problems'])]
    still_closing = sorted({o['coin'] for o in plan2['orders'] if o['reduce_only']})
    if still_closing:
        # do not add exposure while any leg is still above target: the gross cap assumed those closes fill
        return problems + [f'{c}: still needs a reduce-only close after phase 1' for c in still_closing] + [
            'phase 2 (opening orders) skipped']
    for o in plan2['orders']:
        _send(cfg, ex, o, mk2, log, lev_done, problems)
    return problems


# --- status -----------------------------------------------------------------------------------------------------------

def status(cfg, account, log):
    mk = market(cfg)
    jr = junior(cfg)
    pos, rows, st = positions(cfg, account)
    print(f"Account {account} on {cfg['network']} {DEX} dex")
    ms = st.get('marginSummary', {})
    print(f"Account value ${float(ms.get('accountValue', 0)):,.2f} | withdrawable ${float(st.get('withdrawable', 0)):,.2f}")
    try:
        ab = http_json(URLS[cfg['network']] + '/info', {'type': 'userAbstraction', 'user': account})
        print('Account abstraction mode:', ab)
    except Exception:
        pass
    tot_u = tot_f = 0.0
    print(f"{'leg':<11}{'size':>10}{'entry':>11}{'mark':>11}{'value $':>10}{'uPnL $':>10}{'funding $':>11}  lev")
    for coin in cfg['weights']:
        p = rows.get(coin)
        if not p:
            print(f'{coin:<11}{"flat":>10}')
            continue
        u = float(p.get('unrealizedPnl', 0))
        f = float((p.get('cumFunding') or {}).get('sinceOpen', 0))  # positive = paid by us
        tot_u += u
        tot_f += f
        print(f"{coin:<11}{float(p['szi']):>10g}{float(p.get('entryPx') or 0):>11,.4g}{mk['legs'][coin]['mark']:>11,.4g}"
              f"{float(p.get('positionValue', 0)):>10,.2f}{u:>10,.2f}{f:>11,.2f}  {p.get('leverage')}")
    print(f"Total uPnL ${tot_u:,.2f} | funding paid since open ${tot_f:,.2f} (positive = paid)")
    last = log.entries('rebalance')
    if not last:
        print('Tracking: no live rebalance logged yet, nothing to compare.')
        return
    r0 = last[-1]
    basket = sum(r0['positions'].get(c, 0) * (mk['legs'][c]['mark'] - r0['marks'][c]) for c in r0['marks'])
    j0, j1 = (r0.get('junior') or {}).get('score'), jr.get('score')
    print(f"Tracking since rebalance {r0['ts']}: basket mark-to-market ${basket:,.2f}")
    if j0 is not None and j1 is not None:
        exp = (j1 - j0) * r0['usd_per_point']
        print(f"Junior {j0} -> {j1} ({j1 - j0:+}): fitted basket move ${exp:,.2f}; gap ${basket - exp:,.2f}")
        print('Note: the junior print for as_of D uses the US session of D-1, so the basket leads the index by about a day.')
    log.write('status', positions=pos, upnl=tot_u, funding_since_open=tot_f, junior=jr, basket_mtm=basket)


# --- main -------------------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--config', help='json or yaml config file')
    ap.add_argument('--live', action='store_true', help='send real orders')
    ap.add_argument('--flatten', action='store_true', help='kill switch: close all basket legs, reduce-only')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--allow-off-hours', action='store_true', help='allow --live outside the US cash session')
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    log = Log(cfg['log_path'])
    try:
        account = checked_account(os.environ.get('HL_ACCOUNT'))
    except Abort as e:
        print('ABORT:', e, file=sys.stderr)
        return 2
    try:
        lock_dir = pathlib.Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp')
        with RunLock(lock_dir / f"aistress-hl-tracker-{cfg['network']}-{account or 'noaccount'}.lock"):
            return _run(a, cfg, log, account)
    except Abort as e:
        log.write('abort', reason=str(e))
        print('ABORT:', e, file=sys.stderr)
        return 2


def _run(a, cfg, log, account):
    if a.status:
        if not account:
            raise Abort('HL_ACCOUNT not set')
        status(cfg, account, log)
        return 0
    mk = market(cfg, strict=not a.flatten)
    for w in mk['warnings']:
        print('WARNING:', w)
    jr = junior(cfg)
    if account:
        pos, _, _ = positions(cfg, account)
    else:
        if a.live:
            raise Abort('HL_ACCOUNT not set')
        pos = {c: 0.0 for c in cfg['weights']}
        print('HL_ACCOUNT not set: positions assumed flat (dry-run only).')
    plan = build_plan(cfg, mk, pos, flatten=a.flatten)
    mode = ('LIVE' if a.live else 'DRY-RUN') + (' FLATTEN' if a.flatten else '')
    print_plan(plan, jr, mk, mode)
    log.write('plan', mode=mode, network=cfg['network'], account=account, junior=jr, plan=plan,
              legs=mk['legs'])
    in_session, et = us_cash_session()
    if not in_session:
        print(f'Note: outside the US cash session (now {et:%a %H:%M} ET). xyz single stocks use internal '
              'order-book pricing on weekends and US holidays.')
    if plan['problems'] and not a.flatten:
        raise Abort('; '.join(plan['problems']))
    if not a.live:
        try:
            from hyperliquid.info import Info
        except ImportError:
            print('hyperliquid-python-sdk not installed (pip install -r requirements-hl.txt): SDK asset id check skipped.')
        else:
            check_sdk_assets(Info(URLS[cfg['network']], skip_ws=True, perp_dexs=['', DEX]), mk)
            print('SDK asset ids match perpDexs/meta for every leg.')
        print('Dry-run: no orders sent. Add --live to trade.')
        return 0
    if cfg['require_us_cash_session'] and not in_session and not a.allow_off_hours and not a.flatten:
        raise Abort('outside the US cash session; use --allow-off-hours to override')
    ex, agent_addr = exchange(cfg, account)
    check_sdk_assets(ex.info, mk)
    resting = open_basket_orders(cfg, account)
    if resting and not a.flatten:
        raise Abort(f'{len(resting)} open order(s) on basket legs; run --flatten --live or cancel them first')
    if resting:
        r = ex.bulk_cancel([{'coin': o['coin'], 'oid': o['oid']} for o in resting])
        log.write('cancel_open_orders', n=len(resting), response=r)
        print(f'Cancelled {len(resting)} open basket order(s):', str(r)[:200])
        left = open_basket_orders(cfg, account)
        if left:
            raise Abort(f'{len(left)} basket order(s) still open after cancel')
    if a.flatten:
        # re-read after cancelling: a resting entry may have filled in the meantime
        mk = market(cfg, strict=False)
        pos, _, _ = positions(cfg, account)
        plan = build_plan(cfg, mk, pos, flatten=True)
        print_plan(plan, jr, mk, mode + ' (refreshed)')
    if not plan['orders']:
        left = {c: v for c, v in pos.items() if v}
        if left:
            print('INCOMPLETE: positions with no closable order here:', left, file=sys.stderr)
            return 3
        print('Nothing to do.')
        return 0
    log.write('live_start', account=account, agent_address=agent_addr, n_orders=len(plan['orders']))
    problems = execute(cfg, ex, plan, mk, log, account, flatten=a.flatten)
    time.sleep(2)
    pos_after, _, _ = positions(cfg, account)
    mk_after = market(cfg, strict=False)
    tag = 'flatten' if a.flatten else ('rebalance' if not problems else 'rebalance_incomplete')
    log.write(tag, positions=pos_after, junior=jr, problems=problems,
              marks={c: L['mark'] for c, L in mk_after['legs'].items()}, usd_per_point=plan['usd_per_point'],
              gross=plan['gross'])
    print('Positions after:', pos_after)
    if a.flatten:
        left = {c: v for c, v in pos_after.items() if v}
        if left:
            problems.append(f'still open after flatten: {left}')
    if problems:
        for p_ in problems:
            print('INCOMPLETE:', p_, file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
