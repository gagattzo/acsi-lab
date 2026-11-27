"""Build one Stack edition for an as-of date.

  python -m stack.build --as-of YYYY-MM-DD [--layers L2,L4] [--offline] [--json out.json | --json -] [--weekly]
                        [--config PATH]

Prints a readable summary; --json writes the full edition and its inputs to a file (--json - prints the JSON instead).

Dates. Every layer reports build_date (the as-of date of the build), as_of (the date its value describes: the session,
the vintage or the period end), available (when the latest input it uses became public) and status (fresh, carried,
stale, insufficient_live_weight, unavailable, not_requested). as_of <= available <= build_date always holds (checked:
`date_check`). Nothing carried is shown as fresh.

Composites. Junior = L1 + L2 (L2 weight 0 until its live-session gate is met), senior = L3 + L4. On each date D a
composite uses each layer's latest value that was available on or before D (L1's residual for t is available on t+1,
L2's session on its own day, L3 and L4 points on their release dates), with that value's status:
  fresh     the value became available on D
  carried   an older value: counts as live only within its staleness rule (junior: engine.junior_carry_days calendar
            days; senior: the layer's own staleness, L3 through its weight_factor, which also scales its weight)
  stale     too old to count
A composite is published only when the live weight (weights x weight factors of live parts) is at least min_live_share
of the target weight; the live share is always shown. Bands are replayed over every date with a new value, so only
fresh values move them. Depth = number of layers at Stressed or worse. --weekly adds the optional weekly Stack Z
(labelled: optional, not adopted).

Cutpoints, windows, anchors and weights are proposed examples, not calibrated; a human freezes them before any backfill
is evidence.
"""
import argparse, bisect, datetime as dt, json, pathlib, sys

from . import config, engine, l1_equity, l2_cds, l3_bdc, l4_fundamentals

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = {'L1': l1_equity, 'L2': l2_cds, 'L3': l3_bdc, 'L4': l4_fundamentals}
NOTE = ('Cutpoints, windows, anchors and weights are proposed examples, not calibrated; a human freezes them before any '
        'backfill is evidence.')
DEFAULT_JUNIOR_CARRY_DAYS = 4


# --- aligning layer values on a date ---------------------------------------------------------------------------------

def known(p):
    """The date a series point could first be computed (L1: its factors' availability; others: the point date)."""
    return p.get('known') or p.get('available') or p['session']


def _days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


class Aligned:
    """A layer's series looked up by date: the latest point known on or before D, with its status on D."""

    def __init__(self, series, kind, carry_days):
        self.pts = sorted(series, key=known)  # stable: several points known on one day keep their order
        self.keys = [known(p) for p in self.pts]
        self.kind, self.carry_days = kind, carry_days

    def at(self, D):
        j = bisect.bisect_right(self.keys, D) - 1
        if j < 0:
            return {'z': None, 'status': 'missing', 'weight_factor': 0.0, 'point': None, 'known': None}
        p, k = self.pts[j], self.keys[j]
        st = p.get('status')
        part = {'z': p.get('Z'), 'weight_factor': float(p.get('weight_factor', 1.0)), 'point': p['session'], 'known': k,
                'scored': p.get('scored'), 'level': p.get('level')}
        if st not in engine.LIVE or p.get('Z') is None:
            return dict(part, status=st or 'missing', z=None)
        if st == 'fresh' and k == D:
            return dict(part, status='fresh')
        age = _days(k, D)
        if self.kind == 'junior' and age > self.carry_days:
            return dict(part, status='stale', z=None,
                        reason=f'last value known {k}, {age} days earlier: junior values carry {self.carry_days} days')
        return dict(part, status='carried', age_days=age)


def _weights(comp, results, point=None):
    """Composite weights with the L2 session gate applied. point: the aligned L2 part on a date (replay), else the
    L2 result of the build."""
    w = dict(comp['layers'])
    if 'L2' in w:
        scored = point.get('scored') if point is not None else (results.get('L2') or {}).get('scored')
        if not scored:
            w['L2'] = 0.0
    return w


def _carry_days(method):
    return method['engine'].get('junior_carry_days', DEFAULT_JUNIOR_CARRY_DAYS)


def composite_series(name, comp, results, method, as_of):
    """(dates, [composite dict per date], [aligned parts per date]) over every date a layer of the composite has a
    point known, up to and including as_of."""
    eng = method['engine']
    kind = 'junior' if name == 'junior' else 'senior'
    al = {k: Aligned(results.get(k, {}).get('_series', []), method['layers'][k]['kind'], _carry_days(method))
          for k in comp['layers']}
    dates = sorted({d for a in al.values() for d in a.keys if d <= as_of} | {as_of})
    comps, parts_by_date = [], []
    for D in dates:
        parts = {k: a.at(D) for k, a in al.items()}
        w = _weights(comp, results, parts.get('L2') or {}) if 'L2' in comp['layers'] else dict(comp['layers'])
        c = engine.composite(parts, w, eng['min_live_share'], eng['tau'])
        c['weights_effective'] = w
        comps.append(c)
        parts_by_date.append(parts)
    h = eng['hysteresis']
    obs = [{'level': c['level'], 'status': c['status']} for c in comps]
    bands = engine.replay_bands(obs, kind, config.cuts(method), up_closes=h['junior_up_closes'],
                                up_releases=h['senior_up_releases'], down_margin=h['down_margin'])
    for c, b in zip(comps, bands):
        c['band'], c['band_moved'], c['band_reason'] = b['band'], b['moved'], b['reason']
    return dates, comps, parts_by_date


def _round(x, k):
    return None if x is None else round(x, k)


def _composite(name, comp, results, method, as_of):
    dates, comps, parts_by_date = composite_series(name, comp, results, method, as_of)
    now, parts = comps[-1], parts_by_date[-1]
    out = {'kind': 'junior' if name == 'junior' else 'senior', 'layers': list(comp['layers']),
           'weights_configured': comp['layers'], 'weights_effective': now['weights_effective']}
    for k in ('status', 'Z', 'level', 'published', 'target_weight', 'live_weight', 'live_share', 'min_live_share',
              'weights_used', 'contributions', 'reason'):
        if k in now:
            out[k] = now[k]
    out['Z'], out['level'] = _round(out.get('Z'), 3), _round(out.get('level'), 2)
    for k in ('live_weight', 'live_share', 'target_weight'):
        out[k] = _round(out[k], 4)
    out['weights_used'] = {k: round(v, 4) for k, v in (out.get('weights_used') or {}).items()}
    out['contributions'] = {k: round(v, 3) for k, v in (out.get('contributions') or {}).items()}
    out['parts'] = {k: {'status': p['status'], 'z': _round(p['z'], 3), 'point': p['point'], 'available': p['known'],
                        'weight_factor': _round(p['weight_factor'], 4), **({'reason': p['reason']} if p.get('reason') else {})}
                    for k, p in parts.items()}
    live = out['status'] in engine.LIVE
    out['band'] = now['band'] if live else None
    out['band_reason'] = (now['band_reason'] if live else out.get('reason'))
    if live and now['status'] == 'carried':
        out['band_reason'] = 'carried value: the band cannot move'
    last_fresh = next((d for d, c in zip(reversed(dates), reversed(comps)) if c['status'] == 'fresh'), None)
    out['last_fresh'] = last_fresh
    if 'L2' in comp['layers'] and comp['layers'].get('L2') and out['weights_effective'].get('L2') == 0:
        out['note'] = 'L2 shown, not scored: its live-session gate is not met'
    return out


def _weekly(method, results, as_of):
    comp = method['composites'].get('weekly_stack')
    if not comp:
        return {'status': 'not_configured'}
    d = dt.date.fromisoformat(as_of)
    monday = (d - dt.timedelta(days=d.weekday())).isoformat()
    eng = method['engine']
    al = {k: Aligned(results.get(k, {}).get('_series', []), method['layers'][k]['kind'], _carry_days(method))
          for k in comp['layers']}
    junior_week, senior_now = {}, {}
    for k, a in al.items():
        if method['layers'][k]['kind'] == 'junior':
            junior_week[k] = [a.at(D)['z'] for D in sorted(set(a.keys)) if monday <= D <= as_of
                              and a.at(D)['status'] == 'fresh']
        else:
            senior_now[k] = a.at(as_of)
    w = _weights(comp, results, al['L2'].at(as_of)) if 'L2' in al else dict(comp['layers'])
    out = engine.weekly_stack(junior_week, senior_now, w, eng['min_live_share'], eng['tau'])
    out.update(week_start=monday, week_end=as_of, adopted=bool(comp.get('adopted', False)), weights_effective=w,
               junior_fresh_days={k: len(v) for k, v in junior_week.items()})
    out['Z'], out['level'] = _round(out.get('Z'), 3), _round(out.get('level'), 2)
    for k in ('live_weight', 'live_share', 'target_weight'):
        out[k] = _round(out[k], 4)
    out['weights_used'] = {k: round(v, 4) for k, v in (out.get('weights_used') or {}).items()}
    out.pop('contributions', None)
    return out


def date_check(layers, as_of):
    """Every layer with a date: as_of <= available <= build date. Returns {'ok': bool, 'problems': [...]}."""
    bad = []
    for k, r in layers.items():
        a, v = r.get('as_of'), r.get('available')
        if v is not None and v > as_of:
            bad.append(f'{k}: available {v} is after the build date {as_of}')
        if a is not None and v is not None and a > v:
            bad.append(f'{k}: as_of {a} is after available {v}')
        if a is not None and a > as_of:
            bad.append(f'{k}: as_of {a} is after the build date {as_of}')
    return {'ok': not bad, 'problems': bad, 'rule': 'as_of <= available <= build date'}


# --- the build ---------------------------------------------------------------------------------------------------------

def compute_layers(as_of, layers=None, offline=False, config_path=None, root=None):
    """(method, config sha256, {layer: result with _series}, inputs). The layers' raw output, before composites."""
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
            r = {'layer': k, 'name': p['name'], 'kind': p['kind'], 'build_date': as_of, 'as_of': None,
                 'status': 'not_requested', 'available': None, 'Z': None, 'band': None, '_series': []}
        results[k] = r
    seen, uniq = set(), []
    for i in inputs:
        if i['path'] not in seen:
            seen.add(i['path'])
            uniq.append(i)
    return method, cfg_hash, results, sorted(uniq, key=lambda i: i['path'])


def edition(as_of, method, cfg_hash, results, wanted, weekly=False):
    out = {'as_of': as_of, 'method': {'name': method['name'], 'version': method['version'], 'status': method.get('status')},
           'config_sha256': cfg_hash, 'note': NOTE, 'layers_requested': wanted}
    out['junior'] = _composite('junior', method['composites']['junior'], results, method, as_of)
    out['senior'] = _composite('senior', method['composites']['senior'], results, method, as_of)
    out['grid'] = {k: {'band': r.get('band'), 'status': r.get('status'), 'Z': r.get('Z'), 'as_of': r.get('as_of'),
                       'available': r.get('available')} for k, r in results.items()}
    out['depth'] = engine.depth({k: r.get('band') for k, r in results.items()})
    if weekly:
        out['weekly_stack'] = _weekly(method, results, as_of)
    out['layers'] = {k: {kk: vv for kk, vv in r.items() if not kk.startswith('_')} for k, r in results.items()}
    out['date_check'] = date_check(out['layers'], as_of)
    return out


def run(as_of, layers=None, offline=False, config_path=None, weekly=False, root=None):
    """(edition dict, inputs list). The edition holds no timestamps, so the same inputs give the same output."""
    method, cfg_hash, results, inputs = compute_layers(as_of, layers, offline, config_path, root)
    return edition(as_of, method, cfg_hash, results, list(layers) if layers else list(MODULES), weekly), inputs


# --- the printed summary ---------------------------------------------------------------------------------------------

def _f(x, fmt='{:+.2f}'):
    return '-' if x is None else fmt.format(x)


def summary(out):
    m = out['method']
    L = [f"Stack edition for {out['as_of']}: {m['name']} {m['version']} ({m.get('status')}), "
         f"config sha256 {out['config_sha256'][:12]}", out['note'], '',
         f"{'layer':<5} {'name':<19} {'status':<24} {'Z':>7} {'level':>5}  {'band':<9} {'as_of':<10}  {'available':<10}"]
    for k, r in out['layers'].items():
        L.append(f"{k:<5} {r.get('name', ''):<19} {r.get('status') or '-':<24} {_f(r.get('Z')):>7} "
                 f"{_f(r.get('published'), '{}'):>5}  {r.get('band') or '-':<9} {r.get('as_of') or '-':<10}  "
                 f"{r.get('available') or '-':<10}")
    L.append('')
    for k, r in out['layers'].items():
        notes = []
        if r.get('reason'):
            notes.append(str(r['reason']))
        tg = r.get('two_group') or {}
        if tg.get('cap') and r.get('raw_band') in ('Stressed', 'Severe'):
            notes.append(tg['reason'])
        elif r.get('band') in ('Stressed', 'Severe'):
            notes.append(f"groups at Stressed or worse: {', '.join(tg.get('groups_stressed_or_worse') or []) or 'none'}")
        if r.get('cap_infeasible'):
            notes.append(f"cap_infeasible: {r.get('groups_live')} borrower group(s) live, the 20% cap needs 5 (equal weights)")
        if k == 'L2' and r.get('gate'):
            g = r['gate']
            notes.append(f"gate: {g['sessions_live']} of {g['min_live_sessions']} live sessions "
                         f"({g['raw_files']} raw files on disk, not counted): {'met' if g['met'] else 'not met, weight 0'}")
        if k == 'L3' and r.get('release_point'):
            notes.append(f"vintage {r['as_of']}, filed by {r['available']}, released {r['release_point']}, "
                         f"weight factor {r.get('weight_factor')}")
        if k == 'L4' and r.get('not_published'):
            np_ = r['not_published']
            notes.append(f"not published: Z {_f(np_['Z'])}, level {_f(np_['level'], '{:.1f}')}, live share "
                         f"{np_['live_share']:.0%} (needs {np_['min_live_share']:.0%})")
        if r.get('band') and r.get('status') not in ('fresh',) and r.get('band_status'):
            notes.append(f"band {r['band']}: {r['band_status']}")
        for n in notes:
            L.append(f"  {k}: {n}")
    L.append('')
    for name in ('junior', 'senior'):
        c = out[name]
        w = ', '.join(f'{k} {v:g}' for k, v in c['weights_effective'].items())
        parts = ', '.join(f"{k} {p['status']}" + (f" (wf {p['weight_factor']:g})" if p['weight_factor'] not in (None, 1.0) and p['status'] in engine.LIVE else '')
                          for k, p in c['parts'].items())
        head = (f"{name.capitalize()}: {c['status']}, Z {_f(c['Z'])}, level {_f(c.get('published'), '{}')}, band "
                f"{c.get('band') or '-'}")
        L.append(f"{head}; live weight {c['live_share']:.0%} of target (needs {c['min_live_share']:.0%}); "
                 f"weights {w}; parts {parts}")
        if c.get('note'):
            L.append(f"  {c['note']}")
        if c.get('band_reason') and c['status'] not in engine.LIVE:
            L.append(f"  not published: {c['band_reason']}")
    d = out['depth']
    L.append(f"Depth: {d['depth']} layer(s) at Stressed or worse ({', '.join(d['layers_stressed_or_worse']) or 'none'})"
             + (f"; no band: {', '.join(d['layers_without_band'])}" if d['layers_without_band'] else ''))
    if 'weekly_stack' in out:
        ws = out['weekly_stack']
        if ws.get('status') == 'not_configured':
            L.append('Weekly Stack (optional, not adopted): not configured')
        else:
            L.append(f"Weekly Stack (optional, not adopted), week {ws['week_start']} to {ws['week_end']}: {ws['status']}, "
                     f"Z {_f(ws.get('Z'))}, level {_f(ws.get('published'), '{}')}; live weight {ws['live_share']:.0%}; "
                     f"junior fresh days {ws['junior_fresh_days']}")
    dc = out['date_check']
    L.append(f"Date check ({dc['rule']}): " + ('ok' if dc['ok'] else 'FAILED: ' + '; '.join(dc['problems'])))
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m stack.build', description=__doc__.split('\n')[0])
    ap.add_argument('--as-of', required=True, help='YYYY-MM-DD')
    ap.add_argument('--layers', help='comma-separated, e.g. L2,L4 (default: all)')
    ap.add_argument('--offline', action='store_true', help='use cached raw files only')
    ap.add_argument('--json', help='write the edition and its inputs to this file (- prints the JSON instead of the summary)')
    ap.add_argument('--weekly', action='store_true', help='add the optional weekly Stack Z (not adopted)')
    ap.add_argument('--config', help='method file (default stack/stack-v0.yaml)')
    a = ap.parse_args(argv)
    try:
        out, inputs = run(a.as_of, a.layers.split(',') if a.layers else None, a.offline, a.config, a.weekly)
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    full = json.dumps(dict(out, inputs=inputs), indent=2, allow_nan=False)
    if a.json == '-':
        print(full)
    else:
        print(summary(out))
        if a.json:
            pathlib.Path(a.json).write_text(full + '\n')
    return 0 if out['date_check']['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
