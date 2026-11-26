"""Score an ACSI method on the canonical tables. The scorecard is the referee: numbers, not votes.

  python harness/score.py methods/<file>.yaml [--baseline methods/v0.1-live.yaml] [--registered YYYY-MM-DD]
                          [--md out.md] [--json out.json]

Needs data/canonical/cds_trades.csv (python harness/dtcc.py). Nasdaq comes from FRED at run time, never stored.

A method may only combine features the pipeline defines (schema/method.yaml): no free code. Tests, all on daily
changes, because the levels of two trending series always look correlated:
  independence   R^2 of the daily gap change on the Nasdaq's daily return (lower = information the Nasdaq lacks)
  movement       how much the gap moves on a normal day and how often it moves at all
  manipulation   how far one trade moves the gap (leave-one-out), and how often an input rests on under 3 trades
  freshness      share of trading days with a same-day print for each market input
  stability      the same tests on each half of the history: a method that only wins in one half is fitted, not better
  forward        the same tests only on days after the method was registered (first committed): data nobody could fit
  licence        every source must be free to redistribute (sources.yaml)
"""
import argparse, csv, datetime as dt, io, json, math, pathlib, statistics, sys, urllib.request
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRED = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'
UA = 'Mozilla/5.0 (compatible; acsi-lab/0.2; +https://aistress.si/lab)'
MIN_FORWARD_DAYS = 20


# --- inputs ----------------------------------------------------------------------------------------------------------

def load_trades(path):
    by, cal = {}, set()
    for r in csv.DictReader(open(path)):
        by.setdefault(r['entity'], {}).setdefault(r['date'], []).append(float(r['spread_bp']))
        cal.add(r['date'])
    return by, sorted(cal)


def fred(series):
    req = urllib.request.Request(FRED.format(series), headers={'User-Agent': UA, 'Accept': '*/*'})
    text = urllib.request.urlopen(req, timeout=60).read().decode()
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        k = [c for c in r if c != 'observation_date'][0]
        if r[k] not in ('', '.'):
            out[r['observation_date']] = float(r[k])
    return out


def check_method(m):
    spec = yaml.safe_load(open(ROOT / 'schema' / 'method.yaml'))
    errs = [f'unknown top-level key: {k}' for k in m if k not in spec['top']]
    errs += [f'missing top-level key: {k}' for k in spec['required_top'] if k not in m]
    for i in m.get('inputs', []):
        errs += [f"input {i.get('id')}: unknown key {k}" for k in i if k not in spec['input']]
        errs += [f"input {i.get('id')}: missing {k}" for k in spec['required_input'] if k not in i]
        src = str(i.get('source', ''))
        if src != 'fixed' and src.split(':')[0] not in spec['sources']:
            errs.append(f"input {i.get('id')}: source {src} is not a pipeline feature ({', '.join(spec['sources'])}, fixed)")
        if i.get('side') not in ('financing', 'asset'):
            errs.append(f"input {i.get('id')}: side must be financing or asset")
        for k, lo, hi in (('min_trades', 1, 20), ('pool_days', 1, 10), ('max_carry_days', 1, 30)):
            if k in i and not (isinstance(i[k], int) and lo <= i[k] <= hi):
                errs.append(f"input {i.get('id')}: {k} must be an integer from {lo} to {hi}")
    if errs:
        raise SystemExit('method rejected:\n  ' + '\n  '.join(errs))
    return m


def score(x, calm, distress):
    return max(0.0, min(100.0, 100.0 * (x - calm) / (distress - calm)))


def ols(y, x):
    n = len(y)
    if n < 5:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x); syy = sum((b - my) ** 2 for b in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx == 0 or syy == 0:
        return {'n': n, 'beta': 0.0, 'r2': 0.0}
    c = sxy / math.sqrt(sxx * syy)
    return {'n': n, 'beta': sxy / sxx, 'r2': c * c}


# --- the series -------------------------------------------------------------------------------------------------------

def build(method, trades, cal):
    ins = method['inputs']
    n_side = {s: sum(1 for j in ins if j['side'] == s) for s in ('financing', 'asset')}
    last, out = {}, []
    for di, d in enumerate(cal):
        sides = {'financing': [], 'asset': []}
        infl, thin, ok = 0.0, False, True
        for i in ins:
            if str(i['source']) == 'fixed':
                sides[i['side']].append(score(float(i['value']), i['calm'], i['distress']))
                continue
            key = i['source'].split(':', 1)[1]
            pool_dates = cal[max(0, di - int(i.get('pool_days', 1)) + 1): di + 1]
            pool = [x for pd in pool_dates for x in trades.get(key, {}).get(pd, [])]
            today = trades.get(key, {}).get(d, [])
            printed = bool(today) and len(pool) >= int(i.get('min_trades', 1))
            if printed:
                lvl = statistics.median(pool)
                last[i['id']] = (d, lvl)
                s = score(lvl, i['calm'], i['distress'])
                if len(pool) > 1:
                    loo = max(abs(statistics.median(pool[:k] + pool[k + 1:]) - lvl) for k in range(len(pool)))
                    infl = max(infl, max(abs(score(lvl + loo, i['calm'], i['distress']) - s), abs(score(lvl - loo, i['calm'], i['distress']) - s)) / n_side[i['side']])
                else:
                    infl = max(infl, 0.0)
                thin = thin or len(pool) < 3
            else:
                thin = True
            got = last.get(i['id'])
            if not got or (dt.date.fromisoformat(d) - dt.date.fromisoformat(got[0])).days > int(i.get('max_carry_days', 10)):
                ok = False
                break
            sides[i['side']].append(score(got[1], i['calm'], i['distress']))
        if not ok or len(sides['financing']) < 2 or not sides['asset']:
            continue
        F = sum(sides['financing']) / len(sides['financing'])
        A = sum(sides['asset']) / len(sides['asset'])
        out.append({'date': d, 'F': F, 'A': A, 'G': F - A, 'influence': infl, 'thin': thin})
    return out


def tests(s, ndx):
    if len(s) < 6:
        return None
    dg, rr = [], []
    for a, b in zip(s, s[1:]):
        if a['date'] in ndx and b['date'] in ndx:
            dg.append(b['G'] - a['G']); rr.append(ndx[b['date']] / ndx[a['date']] - 1)
    fit = ols(dg, rr) or {'r2': None, 'beta': None, 'n': len(dg)}
    infl = sorted(x['influence'] for x in s)
    return {
        'days': len(s), 'from': s[0]['date'], 'to': s[-1]['date'],
        'r2': None if fit['r2'] is None else round(fit['r2'], 3),
        'beta_per_1pct': None if fit['beta'] is None else round(fit['beta'] / 100, 2),
        'sd_daily': round(statistics.pstdev(dg), 2) if len(dg) > 1 else None,
        'share_moved_0_1': round(sum(1 for x in dg if abs(x) >= 0.1) / len(dg), 2) if dg else None,
        'max_one_trade': round(infl[-1], 2), 'p95_one_trade': round(infl[min(len(infl) - 1, int(0.95 * len(infl)))], 2),
        'share_thin_days': round(sum(1 for x in s if x['thin']) / len(s), 2),
    }


def scorecard(method, trades, cal, ndx, sources, registered=None):
    s = build(method, trades, cal)
    if len(s) < 30:
        raise SystemExit('fewer than 30 scored days: not enough data to judge')
    full = tests(s, ndx)
    half = len(s) // 2
    fresh = {}
    for i in method['inputs']:
        if str(i['source']).startswith('dtcc:'):
            key = i['source'].split(':', 1)[1]
            fresh[i['id']] = round(sum(1 for d in cal if d in trades.get(key, {})) / len(cal), 2)
    lic = []
    for i in method['inputs']:
        sid = i['source'].split(':')[0] if str(i['source']) != 'fixed' else i.get('source_id', 'edition')
        src = sources.get(sid)
        lic.append({'input': i['id'], 'source': sid, 'redistributable': bool(src and src.get('redistributable'))})
    fwd = None
    if registered:
        after = [x for x in s if x['date'] > registered]
        fwd = {'registered': registered, 'days': len(after), 'tests': tests(after, ndx) if len(after) >= MIN_FORWARD_DAYS else None}
    return {
        'method': method['name'], 'gap_now': round(s[-1]['G'], 1), 'full': full,
        'halves': [tests(s[:half], ndx), tests(s[half:], ndx)],
        'forward': fwd, 'freshness': fresh, 'licence': lic,
    }


# --- report -------------------------------------------------------------------------------------------------------------

ROWS = [  # (label, key, format, better)
    ('Independence: R² of daily gap change on Nasdaq', 'r2', '{:.3f}', 'low'),
    ('Beta: gap points per 1% Nasdaq move', 'beta_per_1pct', '{:+.2f}', None),
    ('Movement: st. dev. of daily gap change (points)', 'sd_daily', '{:.2f}', None),
    ('Movement: share of days the gap moves 0.1+', 'share_moved_0_1', '{:.0%}', None),
    ('Manipulation: most one trade moved the gap (points)', 'max_one_trade', '{:.2f}', 'low'),
    ('Manipulation: 95th percentile, one trade', 'p95_one_trade', '{:.2f}', 'low'),
    ('Manipulation: days resting on an input with under 3 trades', 'share_thin_days', '{:.0%}', 'low'),
]


def fmt(v, f):
    return '—' if v is None else f.format(v)


def to_md(c, b=None):
    t, bt = c['full'], (b['full'] if b else None)
    L = [f"### Scorecard: `{c['method']}`", '',
         f"{t['days']} scored days, {t['from']} to {t['to']}. Gap on the last day: **{c['gap_now']:+.1f}**." + (f" Baseline `{b['method']}`: {b['gap_now']:+.1f}." if b else ''), '',
         '| Test | This method | ' + ('Baseline | ' if b else '') + 'First half | Second half |',
         '| --- | --- | ' + ('--- | ' if b else '') + '--- | --- |']
    for label, k, f, better in ROWS:
        v = t[k]; cell = fmt(v, f)
        if b and better and v is not None and bt[k] is not None and v != bt[k]:
            cell += ' ✓' if (v < bt[k]) == (better == 'low') else ' ✗'
        halves = [fmt(h[k], f) if h else '—' for h in c['halves']]
        L.append(f'| {label} | {cell} | ' + (f'{fmt(bt[k], f)} | ' if b else '') + ' | '.join(halves) + ' |')
    for k, v in c['freshness'].items():
        L.append(f"| Freshness: trading days with a same-day trade, {k} | {v:.0%} | " + (f"{b['freshness'].get(k, 0):.0%} | " if b else '') + '| |')
    bad = [l for l in c['licence'] if not l['redistributable']]
    L.append(f"| Licence: inputs we may not redistribute | {', '.join(l['input'] for l in bad) or 'none'} | " + ('| ' if b else '') + '| |')
    L.append('')
    fw = c.get('forward')
    if fw:
        if fw['tests']:
            ft = fw['tests']
            L.append(f"**Forward test** (only days after {fw['registered']}, data nobody could fit): {fw['days']} days, R² {fmt(ft['r2'], '{:.3f}')}, "
                     f"most one trade moved the gap {fmt(ft['max_one_trade'], '{:.2f}')}, thin days {fmt(ft['share_thin_days'], '{:.0%}')}.")
        else:
            L.append(f"**Forward test:** registered {fw['registered']}; {fw['days']} trading days so far, {MIN_FORWARD_DAYS} needed before it counts. "
                     'Adoption waits for it.')
    L.append('✓ better than the baseline, ✗ worse. A method that wins in only one half is fitted to that half, not better.')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('method'); ap.add_argument('--baseline'); ap.add_argument('--registered')
    ap.add_argument('--md'); ap.add_argument('--json')
    ap.add_argument('--trades', default=str(ROOT / 'data' / 'canonical' / 'cds_trades.csv'))
    a = ap.parse_args()
    trades, cal = load_trades(a.trades)
    ndx = fred('NASDAQCOM')
    sources = {s['id']: s for s in yaml.safe_load(open(ROOT / 'sources.yaml'))['sources']}
    c = scorecard(check_method(yaml.safe_load(open(a.method))), trades, cal, ndx, sources, a.registered)
    b = scorecard(check_method(yaml.safe_load(open(a.baseline))), trades, cal, ndx, sources) if a.baseline and a.baseline != a.method else None
    md = to_md(c, b)
    print(md)
    if a.md:
        open(a.md, 'w').write(md + '\n')
    if a.json:
        json.dump({'method': c, 'baseline': b}, open(a.json, 'w'), indent=2)


if __name__ == '__main__':
    main()
