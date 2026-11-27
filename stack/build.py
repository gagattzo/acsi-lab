"""Build one Stack edition for an as-of date.

  python -m stack.build --as-of YYYY-MM-DD [--layers L2,L4] [--offline] [--json out.json] [--weekly] [--config PATH]

Prints the edition as JSON. Every layer reports status (fresh, carried, unavailable, not_implemented, not_requested),
as_of and available dates; nothing carried is shown as fresh. Junior = L1 + L2 (L2 weight 0 until its session gate is
met), senior = L3 + L4; each composite is published only when >= min_live_share of its target weight is live. Depth =
number of layers at Stressed or worse. --weekly adds the optional weekly Stack Z (labelled: optional, not adopted).

Cutpoints, windows, anchors and weights are proposed examples, not calibrated; a human freezes them before any backfill
is evidence.
"""
import argparse, datetime as dt, json, pathlib, sys

from . import config, engine, l1_equity, l2_cds, l3_bdc, l4_fundamentals

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = {'L1': l1_equity, 'L2': l2_cds, 'L3': l3_bdc, 'L4': l4_fundamentals}
NOTE = ('Cutpoints, windows, anchors and weights are proposed examples, not calibrated; a human freezes them before any '
        'backfill is evidence.')


def _weights(comp, results, point=None):
    """Composite weights with the L2 session gate applied (per point when replaying history)."""
    w = dict(comp['layers'])
    if 'L2' in w:
        scored = point.get('scored') if point is not None else (results.get('L2') or {}).get('scored')
        if not scored:
            w['L2'] = 0.0
    return w


def _composite(name, comp, results, method, as_of):
    eng = method['engine']
    kind = 'junior' if name == 'junior' else 'senior'
    w = _weights(comp, results)
    parts = {k: {'z': results.get(k, {}).get('Z'), 'status': results.get(k, {}).get('status')} for k in comp['layers']}
    now = engine.composite(parts, w, eng['min_live_share'], eng['tau'])
    # replay the band over the history of the composite's layers
    series = {k: {p['session']: p for p in results.get(k, {}).get('_series', [])} for k in comp['layers']}
    dates = sorted({d for s in series.values() for d in s if d <= as_of})
    obs = []
    for d in dates:
        ps = {k: {'z': s[d]['Z'], 'status': s[d]['status']} for k, s in series.items() if d in s}
        wd = dict(comp['layers'])
        if 'L2' in wd and not (series.get('L2', {}).get(d) or {}).get('scored'):
            wd['L2'] = 0.0
        c = engine.composite(ps, wd, eng['min_live_share'], eng['tau'])
        obs.append({'level': c['level'], 'status': 'fresh' if c['status'] == 'fresh' else c['status']})
    h = eng['hysteresis']
    bands = engine.replay_bands(obs, kind, config.cuts(method), up_closes=h['junior_up_closes'],
                                up_releases=h['senior_up_releases'], down_margin=h['down_margin'])
    last = bands[-1] if bands else {'band': None, 'moved': None, 'reason': 'no history'}
    out = {'kind': kind, 'layers': list(comp['layers']), 'weights_configured': comp['layers'], 'weights_effective': w}
    out.update({k: v for k, v in now.items()})
    for k in ('Z', 'level'):
        if out.get(k) is not None:
            out[k] = round(out[k], 3 if k == 'Z' else 2)
    out['contributions'] = {k: round(v, 3) for k, v in (out.get('contributions') or {}).items()}
    out['band'] = last['band'] if out['status'] in ('fresh', 'carried') else None
    out['band_reason'] = last['reason'] if out['band'] else out.get('reason')
    if 'L2' in comp['layers'] and w.get('L2') == 0 and comp['layers'].get('L2'):
        out['note'] = 'L2 shown, not scored: its session gate is not met'
    return out


def _weekly(method, results, as_of):
    comp = method['composites'].get('weekly_stack')
    if not comp:
        return {'status': 'not_configured'}
    d = dt.date.fromisoformat(as_of)
    monday = (d - dt.timedelta(days=d.weekday())).isoformat()
    junior_week, senior_now = {}, {}
    for k in comp['layers']:
        r = results.get(k) or {}
        if method['layers'][k]['kind'] == 'junior':
            junior_week[k] = [p['Z'] for p in r.get('_series', []) if monday <= p['session'] <= as_of and p['status'] == 'fresh']
        else:
            senior_now[k] = {'z': r.get('Z'), 'status': r.get('status')}
    eng = method['engine']
    out = engine.weekly_stack(junior_week, senior_now, _weights(comp, results), eng['min_live_share'], eng['tau'])
    out.update(week_start=monday, week_end=as_of, adopted=bool(comp.get('adopted', False)))
    for k in ('Z', 'level'):
        if out.get(k) is not None:
            out[k] = round(out[k], 3 if k == 'Z' else 2)
    out.pop('contributions', None)
    return out


def run(as_of, layers=None, offline=False, config_path=None, weekly=False, root=None):
    """(edition dict, inputs list). The edition holds no timestamps, so the same inputs give the same output."""
    dt.date.fromisoformat(as_of)
    method, cfg_hash = config.load(config_path)
    wanted = list(layers) if layers else list(MODULES)
    bad = [k for k in wanted if k not in MODULES]
    if bad:
        raise SystemExit(f'unknown layer(s): {", ".join(bad)} (known: {", ".join(MODULES)})')
    ctx = {'root': str(root or ROOT), 'offline': offline}
    results, inputs = {}, []
    for k in MODULES:
        if k in wanted:
            r, ins = MODULES[k].compute(as_of, method, ctx)
            inputs.extend(ins)
        else:
            p = method['layers'][k]
            r = {'layer': k, 'name': p['name'], 'kind': p['kind'], 'as_of': as_of, 'status': 'not_requested',
                 'available': None, 'Z': None, 'band': None, '_series': []}
        results[k] = r
    out = {'as_of': as_of, 'method': {'name': method['name'], 'version': method['version'], 'status': method.get('status')},
           'config_sha256': cfg_hash, 'note': NOTE, 'layers_requested': wanted}
    out['junior'] = _composite('junior', method['composites']['junior'], results, method, as_of)
    out['senior'] = _composite('senior', method['composites']['senior'], results, method, as_of)
    out['grid'] = {k: {'band': r.get('band'), 'status': r.get('status')} for k, r in results.items()}
    out['depth'] = engine.depth({k: r.get('band') for k, r in results.items()})
    if weekly:
        out['weekly_stack'] = _weekly(method, results, as_of)
    out['layers'] = {k: {kk: vv for kk, vv in r.items() if not kk.startswith('_')} for k, r in results.items()}
    seen, uniq = set(), []
    for i in inputs:
        if i['path'] not in seen:
            seen.add(i['path'])
            uniq.append(i)
    return out, sorted(uniq, key=lambda i: i['path'])


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m stack.build', description=__doc__.split('\n')[0])
    ap.add_argument('--as-of', required=True, help='YYYY-MM-DD')
    ap.add_argument('--layers', help='comma-separated, e.g. L2,L4 (default: all)')
    ap.add_argument('--offline', action='store_true', help='use cached raw files only')
    ap.add_argument('--json', help='also write the edition and its inputs to this file')
    ap.add_argument('--weekly', action='store_true', help='add the optional weekly Stack Z (not adopted)')
    ap.add_argument('--config', help='method file (default stack/stack-v0.yaml)')
    a = ap.parse_args(argv)
    try:
        out, inputs = run(a.as_of, a.layers.split(',') if a.layers else None, a.offline, a.config, a.weekly)
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    text = json.dumps(out, indent=2, sort_keys=False, allow_nan=False)
    print(text)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(out, inputs=inputs), indent=2, allow_nan=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
