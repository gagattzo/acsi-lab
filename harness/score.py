"""Score an ACSI method on a year of free daily data. The scorecard is the referee: numbers, not votes.

  python harness/score.py methods/v0.1-live.yaml [--baseline methods/v0.1-live.yaml] [--md out.md] [--json out.json]

Needs data/cds_daily.csv (run harness/dtcc.py first). Nasdaq comes from FRED at run time and is never stored here.

What it measures, on daily changes (levels of trending series always look correlated, so levels are not tested):
  independence   R^2 and beta of the daily gap change on the Nasdaq's daily return. Lower R^2 = more information
                 that the Nasdaq does not already carry.
  movement       how much the gap moves on a normal day, and how often it moves at all.
  manipulation   how far the gap moves if the single most influential trade of the day is removed, and how often
                 an input rests on fewer than 3 trades.
  freshness      share of weekdays on which each market input had a trade that day.
  licence        every source must be free to redistribute (sources.yaml).
"""
import argparse, csv, datetime as dt, io, json, math, pathlib, statistics, sys, urllib.request
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRED = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'


def load_cds(path):
    by = {}
    for r in csv.DictReader(open(path)):
        by.setdefault(r['entity'], {})[r['date']] = {'bp': float(r['bp']), 'trades': int(r['trades']), 'loo': float(r['loo_bp']) if r['loo_bp'] else 0.0}
    return by


def fred(series):
    req = urllib.request.Request(FRED.format(series), headers={'User-Agent': 'Mozilla/5.0 (compatible; acsi-lab/0.1; +https://aistress.si)', 'Accept': '*/*'})
    text = urllib.request.urlopen(req, timeout=60).read().decode()
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        k = [c for c in r if c != 'observation_date'][0]
        if r[k] not in ('', '.'):
            out[r['observation_date']] = float(r[k])
    return out


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
        return {'n': n, 'beta': 0.0, 'r2': 0.0, 'corr': 0.0}
    corr = sxy / math.sqrt(sxx * syy)
    return {'n': n, 'beta': sxy / sxx, 'r2': corr * corr, 'corr': corr}


def build(method, cds):
    days = sorted({d for e in cds.values() for d in e})
    ins = method['inputs']
    series, detail = [], []
    last = {}
    for d in days:
        sides = {'financing': [], 'asset': []}
        infl, thin, ok = 0.0, False, True
        for i in ins:
            src = str(i['source'])
            if src == 'fixed':
                sides[i['side']].append(score(float(i['value']), i['calm'], i['distress']))
                continue
            kind, key = src.split(':', 1)
            if kind != 'dtcc':
                raise SystemExit(f'unknown source {src}: add a loader in harness/score.py with your proposal')
            pt = cds.get(key, {}).get(d)
            if pt:
                last[key] = (d, pt)
            got = last.get(key)
            if not got or (dt.date.fromisoformat(d) - dt.date.fromisoformat(got[0])).days > int(i.get('max_carry_days', 10)):
                ok = False
                break
            v = got[1]['bp']
            s = score(v, i['calm'], i['distress'])
            sides[i['side']].append(s)
            if pt:
                n_side = sum(1 for j in ins if j['side'] == i['side'])
                infl = max(infl, max(abs(score(v + pt['loo'], i['calm'], i['distress']) - s), abs(score(v - pt['loo'], i['calm'], i['distress']) - s)) / n_side)
                thin = thin or pt['trades'] < 3
            else:
                thin = True
        if not ok or len(sides['financing']) < 2 or not sides['asset']:
            continue
        F = sum(sides['financing']) / len(sides['financing'])
        A = sum(sides['asset']) / len(sides['asset'])
        series.append({'date': d, 'F': F, 'A': A, 'G': F - A, 'influence': infl, 'thin': thin})
    return series


def scorecard(method, cds, ndx, sources):
    s = build(method, cds)
    if len(s) < 30:
        raise SystemExit('fewer than 30 scored days: not enough data to judge')
    dg, rr, infl, thin = [], [], [], []
    for a, b in zip(s, s[1:]):
        if a['date'] in ndx and b['date'] in ndx:
            dg.append(b['G'] - a['G']); rr.append(ndx[b['date']] / ndx[a['date']] - 1)
    full, recent = ols(dg, rr), ols(dg[-60:], rr[-60:])
    infl = sorted(x['influence'] for x in s)
    q = lambda p: infl[min(len(infl) - 1, int(p * len(infl)))]
    weekdays = sorted({d for e in cds.values() for d in e})
    fresh = {}
    for i in method['inputs']:
        if str(i['source']).startswith('dtcc:'):
            key = i['source'].split(':', 1)[1]
            fresh[i['id']] = sum(1 for d in weekdays if d in cds.get(key, {})) / len(weekdays)
    lic = []
    for i in method['inputs']:
        sid = str(i['source']).split(':')[0] if str(i['source']) != 'fixed' else i.get('source_id', 'edition')
        src = sources.get(sid)
        lic.append({'input': i['id'], 'source': sid, 'redistributable': bool(src and src.get('redistributable'))})
    return {
        'method': method['name'], 'days': len(s), 'from': s[0]['date'], 'to': s[-1]['date'],
        'gap_now': round(s[-1]['G'], 1),
        'independence': {'r2_full': round(full['r2'], 3), 'beta_full': round(full['beta'] / 100, 2), 'r2_60d': round(recent['r2'], 3) if recent else None, 'n': full['n']},
        'movement': {'sd_daily': round(statistics.pstdev(dg), 2), 'share_days_moved_0_1': round(sum(1 for x in dg if abs(x) >= 0.1) / len(dg), 2)},
        'manipulation': {'max_one_trade': round(infl[-1], 2), 'p95_one_trade': round(q(0.95), 2), 'share_thin_days': round(sum(1 for x in s if x['thin']) / len(s), 2)},
        'freshness': {k: round(v, 2) for k, v in fresh.items()},
        'licence': lic,
    }


def to_md(c, base=None):
    def cell(path, fmt='{}', better='low'):
        v = c
        for p in path: v = v[p]
        out = fmt.format(v)
        if base:
            b = base
            for p in path: b = b[p]
            if isinstance(v, (int, float)) and isinstance(b, (int, float)) and v != b:
                good = (v < b) if better == 'low' else (v > b)
                out += f' ({"better" if good else "worse"} than {fmt.format(b)})'
        return out
    lines = [f"### Scorecard: `{c['method']}`", '', f"{c['days']} scored days, {c['from']} to {c['to']}. Gap on the last day: **{c['gap_now']:+.1f}**.", '',
             '| Test | Result | What good looks like |', '| --- | --- | --- |',
             f"| Independence: R² of daily gap change on Nasdaq, full period | {cell(['independence','r2_full'],'{:.3f}')} | Low. 1.0 would mean the gap is just the Nasdaq |",
             f"| Independence: R², last 60 days | {cell(['independence','r2_60d'],'{:.3f}')} | Low |",
             f"| Beta: gap points per 1% Nasdaq move | {cell(['independence','beta_full'],'{:+.2f}', 'none')} | Reported, not judged |",
             f"| Movement: st. dev. of daily gap change (points) | {cell(['movement','sd_daily'],'{:.2f}','high')} | Moves when markets move; not noise |",
             f"| Movement: share of days the gap moves 0.1+ | {cell(['movement','share_days_moved_0_1'],'{:.0%}','high')} | High |",
             f"| Manipulation: most one trade moved the gap (points) | {cell(['manipulation','max_one_trade'],'{:.2f}')} | Low |",
             f"| Manipulation: 95th percentile, one trade | {cell(['manipulation','p95_one_trade'],'{:.2f}')} | Low |",
             f"| Manipulation: share of days resting on a thin input (<3 trades) | {cell(['manipulation','share_thin_days'],'{:.0%}')} | Low |"]
    for k, v in c['freshness'].items():
        lines.append(f"| Freshness: weekdays with a same-day trade, {k} | {v:.0%} | High |")
    bad = [l for l in c['licence'] if not l['redistributable']]
    lines.append(f"| Licence: inputs from sources we may not redistribute | {', '.join(l['input'] + ' (' + l['source'] + ')' for l in bad) or 'none'} | None |")
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('method'); ap.add_argument('--baseline'); ap.add_argument('--md'); ap.add_argument('--json')
    ap.add_argument('--cds', default=str(ROOT / 'data' / 'cds_daily.csv'))
    a = ap.parse_args()
    cds = load_cds(a.cds)
    ndx = fred('NASDAQCOM')
    sources = {s['id']: s for s in yaml.safe_load(open(ROOT / 'sources.yaml'))['sources']}
    c = scorecard(yaml.safe_load(open(a.method)), cds, ndx, sources)
    b = scorecard(yaml.safe_load(open(a.baseline)), cds, ndx, sources) if a.baseline and a.baseline != a.method else None
    md = to_md(c, b)
    print(md)
    if a.md: open(a.md, 'w').write(md + '\n')
    if a.json: json.dump({'method': c, 'baseline': b}, open(a.json, 'w'), indent=2)


if __name__ == '__main__':
    main()
