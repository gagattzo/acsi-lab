"""Score an ACSI method on the canonical tables. The scorecard is the referee: numbers, not votes.

  python harness/score.py methods/<file>.yaml [--baseline methods/v0.1-live.yaml] [--registered YYYY-MM-DD]
                          [--md out.md] [--json out.json]

Needs data/canonical/cds_trades.csv (python harness/dtcc.py). Factor series come from FRED at run time, never stored.

A method may only combine features the pipeline defines (schema/method.yaml): no free code. Tests, all on daily
changes, because the levels of two trending series always look correlated:
  independence   R^2 of the daily change on the Nasdaq's daily return, on the change in high-yield spreads and in the
                 VIX, and on all three together (lower = information they lack). A method that barely moves also has a
                 low R^2, so R^2 only counts as a win if the method still moves at least half as much as the baseline
  movement       how much the series moves on a normal day and how often it moves at all: reported, never judged
  manipulation   how far one trade moves the series (leave-one-out), how often an input rests on under 3 trades, and
                 how often a level is set by a single trade
  freshness      share of trading days with a same-day print for each market input, and share of values carried
  coverage       which inputs were live, carried or held from an edition. A value held from an edition (source: fixed)
                 counts only from its as_of date: it is never painted into earlier days. Before that, the day is scored
                 on the financing side alone, and a change is scored only between days resting on the same inputs
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

FACTORS = {'nasdaq': ('NASDAQCOM', 'return'), 'hy_oas': ('BAMLH0A0HYM2', 'diff'), 'vix': ('VIXCLS', 'diff')}


def load_trades(path):
    """by[key][date] = spreads in bp. key = entity (all priced trades) or entity|quoted_first (the day's quoted trades
    when there are any, else its upfront-implied ones)."""
    raw, cal = {}, set()
    for r in csv.DictReader(open(path)):
        raw.setdefault(r['entity'], {}).setdefault(r['date'], []).append((float(r['spread_bp']), r['kind']))
        cal.add(r['date'])
    by = {}
    for e, days in raw.items():
        for d, xs in days.items():
            by.setdefault(e, {})[d] = [x for x, _ in xs]
            q = [x for x, k in xs if k == 'quoted']
            by.setdefault(e + '|quoted_first', {})[d] = q or [x for x, _ in xs]
    return by, sorted(cal)


def factor_changes(get=None):
    """Daily changes of each factor series, keyed by date. A series that cannot be fetched is left out, not faked."""
    get = get or fred
    out = {}
    for k, (sid, how) in FACTORS.items():
        try:
            lv = get(sid)
        except Exception as e:  # noqa: BLE001
            print(f'note: {sid} unavailable ({e}); {k} test skipped', file=sys.stderr)
            continue
        out[k] = lv
    return out


def fred(series):
    req = urllib.request.Request(FRED.format(series), headers={'User-Agent': UA, 'Accept': '*/*'})
    text = urllib.request.urlopen(req, timeout=60).read().decode()
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        k = [c for c in r if c != 'observation_date'][0]
        if r[k] not in ('', '.'):
            out[r['observation_date']] = float(r[k])
    return out


INT_FEATURES = (('min_trades', 1, 20), ('pool_days', 1, 10), ('max_carry_days', 1, 30), ('window_sessions', 1, 10),
                ('min_prints', 1, 50), ('min_sessions_with_prints', 1, 10), ('max_days_since_print', 1, 10),
                ('level_sessions', 1, 10))
ENUM_FEATURES = {'prints': ('all', 'quoted_first'), 'level': ('median', 'median_of_daily_medians')}


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
        if src == 'fixed':
            try:
                dt.date.fromisoformat(str(i.get('as_of')))
            except ValueError:
                errs.append(f"input {i.get('id')}: a fixed value needs as_of (YYYY-MM-DD), the day it was observed")
        if i.get('side') not in ('financing', 'asset'):
            errs.append(f"input {i.get('id')}: side must be financing or asset")
        for k, lo, hi in INT_FEATURES:
            if k in i and not (isinstance(i[k], int) and not isinstance(i[k], bool) and lo <= i[k] <= hi):
                errs.append(f"input {i.get('id')}: {k} must be an integer from {lo} to {hi}")
        for k, ok in ENUM_FEATURES.items():
            if k in i and i[k] not in ok:
                errs.append(f"input {i.get('id')}: {k} must be one of {', '.join(ok)}")
        if 'window_sessions' in i and 'pool_days' in i:
            errs.append(f"input {i.get('id')}: use window_sessions or pool_days, not both")
        W = i.get('window_sessions', i.get('pool_days', 1))
        if isinstance(i.get('level_sessions'), int) and isinstance(i.get('max_days_since_print', 1), int) and i['level_sessions'] < i.get('max_days_since_print', 1):
            errs.append(f"input {i.get('id')}: level_sessions must be at least max_days_since_print, so the level always has a print")
        for k in ('min_sessions_with_prints', 'max_days_since_print', 'level_sessions'):
            if isinstance(i.get(k), int) and isinstance(W, int) and i[k] > W:
                errs.append(f"input {i.get('id')}: {k} cannot exceed the window ({W} sessions)")
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


def r2_multi(y, X):
    """R^2 of y on several regressors with an intercept (normal equations, Gaussian elimination)."""
    n, k = len(y), len(X[0]) if X else 0
    if n < k + 5 or k == 0:
        return None
    rows = [[1.0] + list(x) for x in X]
    A = [[sum(r[i] * r[j] for r in rows) for j in range(k + 1)] for i in range(k + 1)]
    b = [sum(r[i] * v for r, v in zip(rows, y)) for i in range(k + 1)]
    for c in range(k + 1):  # solve A beta = b
        piv = max(range(c, k + 1), key=lambda r: abs(A[r][c]))
        if abs(A[piv][c]) < 1e-12:
            return None
        A[c], A[piv], b[c], b[piv] = A[piv], A[c], b[piv], b[c]
        for r in range(k + 1):
            if r != c:
                f = A[r][c] / A[c][c]
                A[r] = [a - f * z for a, z in zip(A[r], A[c])]; b[r] -= f * b[c]
    beta = [b[i] / A[i][i] for i in range(k + 1)]
    my = sum(y) / n
    sst = sum((v - my) ** 2 for v in y)
    sse = sum((v - sum(bb * xx for bb, xx in zip(beta, r))) ** 2 for v, r in zip(y, rows))
    return None if sst == 0 else max(0.0, 1 - sse / sst)


# --- the series -------------------------------------------------------------------------------------------------------

def window(i):
    """(sessions in the window, prints needed, sessions with a print needed, last print within this many sessions)."""
    W = int(i.get('window_sessions', i.get('pool_days', 1)))
    return W, int(i.get('min_prints', i.get('min_trades', 1))), int(i.get('min_sessions_with_prints', 1)), int(i.get('max_days_since_print', 1))


def build(method, trades, cal):
    """One row per scored day. Each input is same_day (set by today's prints), pooled (set by the window's prints, none
    today), carried (last level, within max_carry_days), held_from_edition (a fixed value on or after its as_of date)
    or missing. The gap needs two financing inputs and one asset input; without the asset side the day is scored on
    the financing side alone (basis = financing)."""
    ins = method['inputs']
    last, out = {}, []
    for di, d in enumerate(cal):
        live = {'financing': [], 'asset': []}
        flags, levels, loo, thin, single = {}, {}, [], False, False
        for i in ins:
            if str(i['source']) == 'fixed':
                if d >= str(i['as_of']):
                    live[i['side']].append(score(float(i['value']), i['calm'], i['distress']))
                    flags[i['id']] = 'held_from_edition'
                else:
                    flags[i['id']] = 'missing'
                continue
            ent = i['source'].split(':', 1)[1]
            key = ent if i.get('prints', 'all') == 'all' else f"{ent}|{i['prints']}"
            W, minp, mins, since = window(i)
            per = [trades.get(key, {}).get(x, []) for x in cal[max(0, di - W + 1): di + 1]]
            pool = [v for day in per for v in day]
            ok_window = lambda days: (sum(len(x) for x in days) >= minp and sum(1 for x in days if x) >= mins
                                      and any(days[-k] for k in range(1, min(since, len(days)) + 1)))
            eligible = ok_window(per)
            before = last.get(i['id'])
            mdm = i.get('level') == 'median_of_daily_medians'
            ls = int(i.get('level_sessions', W))
            lvl_of = (lambda days: statistics.median([statistics.median(x) for x in days[-ls:] if x])) if mdm else \
                     (lambda days: statistics.median([v for x in days[-ls:] for v in x]))
            if eligible:
                last[i['id']] = (d, lvl_of(per))
            got = last.get(i['id'])
            if not got or (dt.date.fromisoformat(d) - dt.date.fromisoformat(got[0])).days > int(i.get('max_carry_days', 10)):
                flags[i['id']] = 'missing'
                thin = True
                continue
            flags[i['id']] = ('same_day' if per[-1] else 'pooled') if eligible else 'carried'
            s = score(got[1], i['calm'], i['distress'])
            levels[i['id']] = round(got[1], 1)
            live[i['side']].append(s)
            if eligible:
                # Leave one TRADE out (not one day) and apply the method's own rule to the rest: if the window still
                # qualifies, its level; if not, the level the input would have carried (none: nothing to compare).
                worst = 0.0
                for a, day in enumerate(per):
                    for k in range(len(day)):
                        rest = per[:a] + [day[:k] + day[k + 1:]] + per[a + 1:]
                        alt = lvl_of(rest) if ok_window(rest) else (before[1] if before else None)
                        if alt is not None:
                            worst = max(worst, abs(score(alt, i['calm'], i['distress']) - s))
                loo.append((i['side'], worst))
            single = single or (eligible and len(pool) == 1)
            thin = thin or not per[-1] or len(pool) < 3
        nf, na = len(live['financing']), len(live['asset'])
        if nf < 2:
            continue
        F = sum(live['financing']) / nf
        A = sum(live['asset']) / na if na else None
        infl = max([x / (nf if side == 'financing' else max(na, 1)) for side, x in loo], default=0.0)
        out.append({'date': d, 'F': F, 'A': A, 'G': None if A is None else F - A, 'basis': 'gap' if na else 'financing',
                    'value': F - A if na else F, 'inputs': tuple(sorted(k for k, v in flags.items() if v != 'missing')),
                    'flags': flags, 'levels': levels, 'influence': infl, 'thin': thin, 'single': single})
    return out


def changes(s):
    """Daily changes between consecutive scored days that rest on the same inputs and the same basis: a change of
    composition (an input dropping out, an edition value starting to count) is not a market move."""
    return [(a, b, b['value'] - a['value']) for a, b in zip(s, s[1:]) if a['inputs'] == b['inputs'] and a['basis'] == b['basis']]


def fchange(series, how, a, b):
    if a not in series or b not in series:
        return None
    return series[b] / series[a] - 1 if how == 'return' else series[b] - series[a]


def tests(s, fac):
    if len(s) < 6:
        return None
    ch = changes(s)
    dg = [c for _, _, c in ch]
    out = {'days': len(s), 'from': s[0]['date'], 'to': s[-1]['date'], 'changes': len(dg)}
    cols = {}
    for k, (sid, how) in FACTORS.items():
        if k not in fac:
            continue
        cols[k] = [fchange(fac[k], how, a['date'], b['date']) for a, b, _ in ch]
    nd = [(y, x) for y, x in zip(dg, cols.get('nasdaq', [])) if x is not None]
    fit = ols([y for y, _ in nd], [x for _, x in nd]) if nd else None
    out['r2'] = None if not fit else round(fit['r2'], 3)
    out['beta_per_1pct'] = None if not fit else round(fit['beta'] / 100, 2)
    for k in ('hy_oas', 'vix'):
        pts = [(y, x) for y, x in zip(dg, cols.get(k, [])) if x is not None]
        f = ols([y for y, _ in pts], [x for _, x in pts]) if pts else None
        out['r2_' + k] = None if not f else round(f['r2'], 3)
    keys = [k for k in FACTORS if k in cols]
    joint = [(y, [cols[k][j] for k in keys]) for j, y in enumerate(dg) if all(cols[k][j] is not None for k in keys)]
    r = r2_multi([y for y, _ in joint], [x for _, x in joint]) if len(keys) > 1 and joint else None
    out['r2_joint'] = None if r is None else round(r, 3)
    infl = sorted(x['influence'] for x in s)
    out.update({
        'sd_daily': round(statistics.pstdev(dg), 2) if len(dg) > 1 else None,
        'share_moved_0_1': round(sum(1 for x in dg if abs(x) >= 0.1) / len(dg), 2) if dg else None,
        'max_one_trade': round(infl[-1], 2), 'p95_one_trade': round(infl[min(len(infl) - 1, int(0.95 * len(infl)))], 2),
        'share_thin_days': round(sum(1 for x in s if x['thin']) / len(s), 2),
        'share_single_trade_days': round(sum(1 for x in s if x['single']) / len(s), 2),
    })
    return out


def scorecard(method, trades, cal, fac, sources, registered=None):
    s = build(method, trades, cal)
    if len(s) < 30:
        raise SystemExit('fewer than 30 scored days: not enough data to judge')
    full = tests(s, fac)
    half = len(s) // 2
    fresh, carried = {}, {}
    for i in method['inputs']:
        if str(i['source']).startswith('dtcc:'):
            key = i['source'].split(':', 1)[1]
            fresh[i['id']] = round(sum(1 for d in cal if d in trades.get(key, {})) / len(cal), 2)
            carried[i['id']] = round(sum(1 for x in s if x['flags'].get(i['id']) == 'carried') / len(s), 2)
    held = [i['id'] for i in method['inputs'] if str(i['source']) == 'fixed']
    coverage = {
        'days_gap': sum(1 for x in s if x['basis'] == 'gap'), 'days_financing_only': sum(1 for x in s if x['basis'] == 'financing'),
        'held_from_edition': {i['id']: str(i['as_of']) for i in method['inputs'] if str(i['source']) == 'fixed'},
        'last_day': s[-1]['flags'], 'last_basis': s[-1]['basis'],
    }
    lic = []
    for i in method['inputs']:
        sid = i['source'].split(':')[0] if str(i['source']) != 'fixed' else i.get('source_id', 'edition')
        src = sources.get(sid)
        lic.append({'input': i['id'], 'source': sid, 'redistributable': bool(src and src.get('redistributable'))})
    fwd = None
    if registered:
        after = [x for x in s if x['date'] > registered]
        fwd = {'registered': registered, 'days': len(after), 'tests': tests(after, fac) if len(after) >= MIN_FORWARD_DAYS else None}
    last_gap = next((x for x in reversed(s) if x['G'] is not None), None)
    return {
        'method': method['name'], 'description': ' '.join(str(method.get('description', '')).split()),
        'gap_now': None if last_gap is None else round(last_gap['G'], 1), 'gap_date': None if last_gap is None else last_gap['date'],
        'financing_now': round(s[-1]['F'], 1), 'full': full,
        'halves': [tests(s[:half], fac), tests(s[half:], fac)],
        'forward': fwd, 'freshness': fresh, 'carried': carried, 'coverage': coverage, 'licence': lic, 'held': held,
    }


# --- report -------------------------------------------------------------------------------------------------------------

ROWS = [  # (label, key, format, better)
    ('Independence: R² of daily change on Nasdaq', 'r2', '{:.3f}', 'low_r2'),
    ('Independence: R² on the change in high-yield spreads', 'r2_hy_oas', '{:.3f}', 'low_r2'),
    ('Independence: R² on the change in the VIX', 'r2_vix', '{:.3f}', 'low_r2'),
    ('Independence: R² on all three together', 'r2_joint', '{:.3f}', 'low_r2'),
    ('Beta: points per 1% Nasdaq move', 'beta_per_1pct', '{:+.2f}', None),
    ('Movement: st. dev. of daily change (points)', 'sd_daily', '{:.2f}', None),
    ('Movement: share of days it moves 0.1+', 'share_moved_0_1', '{:.0%}', None),
    ('Manipulation: most one trade moved it (points)', 'max_one_trade', '{:.2f}', 'low'),
    ('Manipulation: 95th percentile, one trade', 'p95_one_trade', '{:.2f}', 'low'),
    ('Manipulation: days resting on an input with under 3 trades', 'share_thin_days', '{:.0%}', 'low'),
    ('Manipulation: days a level is set by a single trade', 'share_single_trade_days', '{:.0%}', 'low'),
]


def fmt(v, f):
    return '—' if v is None else f.format(v)


def mark(v, bv, better, t, bt):
    if not better or v is None or bv is None or v == bv:
        return ''
    if better == 'low_r2' and (t.get('sd_daily') or 0) < 0.5 * (bt.get('sd_daily') or 0):
        return ' (barely moves: not a win)'
    return ' ✓' if v < bv else ' ✗'


def to_md(c, b=None):
    t, bt = c['full'], (b['full'] if b else None)
    cov = c['coverage']
    gap = f"Gap on {c['gap_date']}: **{c['gap_now']:+.1f}**" if c['gap_now'] is not None else 'No day with a live asset side'
    L = [f"### Scorecard: `{c['method']}`", '',
         f"{t['days']} scored days, {t['from']} to {t['to']}; {t['changes']} daily changes on unchanged inputs. {gap}."
         + (f" Baseline `{b['method']}`: {b['gap_now']:+.1f}." if b and b['gap_now'] is not None else ''), '',
         f"Coverage: the full gap on {cov['days_gap']} days, the financing side alone on {cov['days_financing_only']}"
         + (f" (held from an edition, counted only from its date: {', '.join(f'{k} {v}' for k, v in cov['held_from_edition'].items())})" if cov['held_from_edition'] else '')
         + '. Nothing is painted into days before it was observed.', '',
         '| Test | This method | ' + ('Baseline | ' if b else '') + 'First half | Second half |',
         '| --- | --- | ' + ('--- | ' if b else '') + '--- | --- |']
    for label, k, f, better in ROWS:
        v = t.get(k); cell = fmt(v, f)
        if b:
            cell += mark(v, bt.get(k), better, t, bt)
        halves = [fmt(h.get(k), f) if h else '—' for h in c['halves']]
        L.append(f'| {label} | {cell} | ' + (f'{fmt(bt.get(k), f)} | ' if b else '') + ' | '.join(halves) + ' |')
    for k, v in c['freshness'].items():
        L.append(f"| Freshness: trading days with a same-day trade, {k} | {v:.0%} | " + (f"{b['freshness'].get(k, 0):.0%} | " if b else '') + '| |')
    for k, v in c['carried'].items():
        L.append(f"| Freshness: scored days {k} is carried, not set that day | {v:.0%} | " + (f"{b['carried'].get(k, 0):.0%} | " if b else '') + '| |')
    bad = [l for l in c['licence'] if not l['redistributable']]
    L.append(f"| Licence: inputs we may not redistribute | {', '.join(l['input'] for l in bad) or 'none'} | " + ('| ' if b else '') + '| |')
    L.append('')
    fw = c.get('forward')
    if fw:
        if fw['tests']:
            ft = fw['tests']
            L.append(f"**Forward test** (only days after {fw['registered']}, data nobody could fit): {fw['days']} days, R² {fmt(ft['r2'], '{:.3f}')}, "
                     f"most one trade moved it {fmt(ft['max_one_trade'], '{:.2f}')}, thin days {fmt(ft['share_thin_days'], '{:.0%}')}.")
        else:
            L.append(f"**Forward test:** registered {fw['registered']}; {fw['days']} trading days so far, {MIN_FORWARD_DAYS} needed before it counts. "
                     'Adoption waits for it.')
    L.append('✓ better than the baseline, ✗ worse. Movement is reported, not judged. A method that wins in only one half is fitted to that half, not better.')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('method'); ap.add_argument('--baseline'); ap.add_argument('--registered')
    ap.add_argument('--md'); ap.add_argument('--json')
    ap.add_argument('--trades', default=str(ROOT / 'data' / 'canonical' / 'cds_trades.csv'))
    a = ap.parse_args()
    trades, cal = load_trades(a.trades)
    fac = factor_changes()
    if 'nasdaq' not in fac:
        raise SystemExit('NASDAQCOM unavailable from FRED: cannot score independence')
    sources = {s['id']: s for s in yaml.safe_load(open(ROOT / 'sources.yaml'))['sources']}
    c = scorecard(check_method(yaml.safe_load(open(a.method))), trades, cal, fac, sources, a.registered)
    b = scorecard(check_method(yaml.safe_load(open(a.baseline))), trades, cal, fac, sources) if a.baseline and a.baseline != a.method else None
    md = to_md(c, b)
    print(md)
    if a.md:
        open(a.md, 'w').write(md + '\n')
    if a.json:
        json.dump({'method': c, 'baseline': b}, open(a.json, 'w'), indent=2)


if __name__ == '__main__':
    main()
