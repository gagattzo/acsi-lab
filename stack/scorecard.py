"""Stack scorecard: replay the Stack over a date range and report what the method promises, not how much it moves.

  python -m stack.scorecard --from D1 --to D2 [--offline] [--layers L1,L2] [--json out.json] [--spot-check N]
                            [--config PATH]

One build at D2 holds every layer's point-in-time series (each point uses only data available on its own date); the
scorecard replays the editions of every weekday from D1 to D2 from them, with the same alignment as stack.build (each
layer's latest value available on or before the day). --spot-check N rebuilds N of those days from scratch and checks
that the replay gives the same layer Z (a test of the replay, slow: one full build per day).

Reported:
  coverage            per layer: share of days live (fresh or carried within its staleness rule), share fresh, mean
                      live weight factor; per composite: share of days published, mean live share
  l2_leave_one_out    the largest move of the L2 layer Z on any day in the range from dropping one print (any print
                      that can reach the range through the window, the change or the z history) or one name
  single_group        per layer: days on which the raw level was Stressed or worse with fewer than 2 groups at Stressed
                      (the 2-group rule held it at Watch), and days the published band was Stressed or worse with
                      fewer than 2 groups (violations: must be 0)
  l1_independence     per leg: correlation of the daily change of the leg's mean z with the same-day QQQ and SMH log
                      returns (residual, scored), next to the same on raw returns (shown for contrast)
  senior_rates        correlation of the weekly change in the senior composite Z with the weekly change in DGS10
                      (FRED, an observation dated t available on the next weekday); acceptance |r| <= 0.3, insufficient
                      data below min_weeks pairs
  band_changes        per layer and composite: band moves (up, down, capped) in the range
  movement            the standard deviation of daily layer Z changes: shown, never scored. More movement is not better

Thresholds (0.3, min_weeks 8, min_pairs 20) are proposed examples, not calibrated.
"""
import argparse, datetime as dt, json, math, pathlib, statistics, sys

from . import build, config, engine, l1_equity, l2_cds

ROOT = pathlib.Path(__file__).resolve().parent.parent
RATES_LIMIT = 0.3
MIN_WEEKS = 8
MIN_PAIRS = 20


# --- small helpers ---------------------------------------------------------------------------------------------------

def weekdays(d1, d2):
    a, b = dt.date.fromisoformat(d1), dt.date.fromisoformat(d2)
    out = []
    while a <= b:
        if a.weekday() < 5:
            out.append(a.isoformat())
        a += dt.timedelta(days=1)
    return out


def corr(xs, ys):
    """Pearson correlation over pairs where both are numbers; None with fewer than 3 pairs or no variance."""
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx = statistics.fmean(x for x, _ in pts)
    my = statistics.fmean(y for _, y in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    syy = sum((y - my) ** 2 for _, y in pts)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _r(x, k=3):
    return None if x is None else round(x, k)


# --- coverage and composites on each day ------------------------------------------------------------------------------

def layer_days(series, kind, days, carry_days):
    """The aligned part of one layer on each day (list of dicts, as build.Aligned.at)."""
    al = build.Aligned(series, kind, carry_days)
    return [al.at(D) for D in days]


def coverage(parts):
    """parts: aligned parts over the days. Share live, share fresh, mean live weight factor (0 when not live)."""
    n = len(parts)
    if not n:
        return {'days': 0, 'share_live': None, 'share_fresh': None, 'mean_live_weight_factor': None}
    live = [p for p in parts if p['status'] in engine.LIVE and p['z'] is not None]
    return {'days': n, 'share_live': _r(len(live) / n), 'share_fresh': _r(sum(1 for p in live if p['status'] == 'fresh') / n),
            'mean_live_weight_factor': _r(sum(p['weight_factor'] for p in live) / n),
            'statuses': {s: sum(1 for p in parts if p['status'] == s) for s in sorted({p['status'] for p in parts})}}


def composites_on_days(name, comp, results, method, days):
    """Composite on each day from the aligned parts (the L2 gate applied per day)."""
    eng = method['engine']
    cd = build._carry_days(method)
    al = {k: build.Aligned(results.get(k, {}).get('_series', []), method['layers'][k]['kind'], cd) for k in comp['layers']}
    out = []
    for D in days:
        parts = {k: a.at(D) for k, a in al.items()}
        w = build._weights(comp, results, parts.get('L2') or {}) if 'L2' in comp['layers'] else dict(comp['layers'])
        out.append(dict(engine.composite(parts, w, eng['min_live_share'], eng['tau']), date=D))
    return out


def composite_coverage(comps):
    n = len(comps)
    pub = [c for c in comps if c['status'] in engine.LIVE]
    return {'days': n, 'share_published': _r(len(pub) / n) if n else None,
            'mean_live_share': _r(statistics.fmean(c['live_share'] for c in comps)) if n else None}


# --- checks on layer series ------------------------------------------------------------------------------------------

def in_range(series, d1, d2):
    return [p for p in series if d1 <= build.known(p) <= d2]


def single_group(series, d1, d2, min_groups=2):
    """Days a layer's raw level was Stressed or worse with fewer than min_groups groups at Stressed (held at Watch by
    the rule), and days its band was Stressed or worse with fewer (violations: must be 0)."""
    hot = ('Stressed', 'Severe')
    pts = [p for p in in_range(series, d1, d2) if p.get('status') == 'fresh']
    held = [p for p in pts if p.get('raw_band') in hot and len(p.get('groups_stressed') or []) < min_groups]
    bad = [p for p in pts if p.get('band') in hot and len(p.get('groups_stressed') or []) < min_groups]
    return {'fresh_points': len(pts), 'would_light_with_one_group': len(held), 'violations': len(bad),
            'violation_dates': [p['session'] for p in bad][:10], 'ok': not bad}


def band_changes(series, d1, d2):
    moves = [p for p in in_range(series, d1, d2) if p.get('band_moved') in ('up', 'down', 'capped')]
    return {'count': len(moves), 'moves': [{'date': build.known(p), 'moved': p['band_moved'], 'band': p.get('band')}
                                           for p in moves][:20]}


def movement(series, d1, d2):
    """Standard deviation of the change in Z between consecutive fresh points: shown, never scored."""
    pts = [p for p in in_range(series, d1, d2) if p.get('status') == 'fresh' and p.get('Z') is not None]
    ch = [b['Z'] - a['Z'] for a, b in zip(pts, pts[1:])]
    return {'sd_change_Z': _r(statistics.pstdev(ch)) if len(ch) > 1 else None, 'changes': len(ch),
            'note': 'shown, not scored: more movement is not better'}


def l1_independence(series, d1, d2, factors=('QQQ', 'SMH'), min_pairs=MIN_PAIRS):
    """Per leg: correlation of the daily change of the leg's mean z with the same-day factor returns, for the residual
    (scored) and raw (contrast) versions. Consecutive sessions only."""
    pts = [p for p in series if p.get('legs_z') is not None]
    out = {}
    legs = sorted({leg for p in pts for leg in p['legs_z']})
    for leg in legs:
        rec = {}
        for key, label in (('legs_z', 'residual'), ('legs_z_raw', 'raw')):
            dz, fr = [], {f: [] for f in factors}
            for a, b in zip(pts, pts[1:]):
                if not d1 <= b['session'] <= d2:
                    continue
                za, zb = a[key].get(leg), b[key].get(leg)
                dz.append(None if za is None or zb is None else zb - za)
                for f in factors:
                    fr[f].append((b.get('factor_returns') or {}).get(f))
            n = sum(1 for x in dz if x is not None)
            rec[label] = {'pairs': n, **{f'corr_{f}': (_r(corr(dz, fr[f])) if n >= min_pairs else None) for f in factors}}
            if n < min_pairs:
                rec[label]['status'] = f'insufficient data: {n} pairs, needs {min_pairs}'
        out[leg] = rec
    vals = [abs(v) for leg in out.values() for k, v in leg['residual'].items() if k.startswith('corr_') and v is not None]
    return {'legs': out, 'max_abs_residual_corr': _r(max(vals)) if vals else None,
            'note': 'the residual should be close to 0 on the factors it removes; raw shown for contrast'}


def fred_available(obs, D):
    """The latest FRED value whose observation date t has next_weekday(t) <= D (t + 1 business day)."""
    for d in sorted((d for d in obs if obs[d] is not None), reverse=True):
        if l1_equity.next_weekday(d) <= D:
            return d, obs[d]
    return None, None


def week_ends(days):
    """Last day of each ISO week among `days`."""
    out = {}
    for D in days:
        y, w, _ = dt.date.fromisoformat(D).isocalendar()
        out[(y, w)] = D
    return [out[k] for k in sorted(out)]


def senior_rates(comps_by_day, dgs10, limit=RATES_LIMIT, min_weeks=MIN_WEEKS):
    """Correlation of the weekly change in senior Z with the weekly change in DGS10. comps_by_day: {day: composite};
    dgs10: {date: value}. A week counts when the senior is published at both week ends."""
    ends = week_ends(sorted(comps_by_day))
    pairs = []
    for a, b in zip(ends, ends[1:]):
        za, zb = comps_by_day[a].get('Z'), comps_by_day[b].get('Z')
        _, ra = fred_available(dgs10, a)
        _, rb = fred_available(dgs10, b)
        if None in (za, zb, ra, rb):
            continue
        pairs.append((zb - za, rb - ra))
    out = {'weeks': len(ends), 'pairs': len(pairs), 'limit_abs_corr': limit, 'min_weeks': min_weeks}
    if len(pairs) < min_weeks:
        return dict(out, status='insufficient_data', corr=None,
                    reason=f'{len(pairs)} weekly pairs with the senior published at both ends, needs {min_weeks}')
    r = corr([x for x, _ in pairs], [y for _, y in pairs])
    if r is None:
        return dict(out, status='insufficient_data', corr=None, reason='no variance in one of the series')
    return dict(out, status='pass' if abs(r) <= limit else 'fail', corr=_r(r))


# --- L2 leave-one-out --------------------------------------------------------------------------------------------------

def l2_leave_one_out(trades, sessions, method, d1, d2):
    """Largest move of the L2 layer Z on any session in [d1, d2] from dropping one print or one name. A day on which
    the value appears or disappears is counted separately (it changes coverage, not the level)."""
    p = method['layers']['L2']
    ents = p['entities']
    idx = [i for i, s in enumerate(sessions) if d1 <= s <= d2]
    if not idx:
        return {'status': 'no sessions in range'}
    by, _ = l2_cds.prints_by_name(trades, sessions, ents)
    names = {e: l2_cds.name_series(by[e], sessions, p)[0] for e in ents}

    def z_on(nm):
        out = {}
        for i in idx:
            out[sessions[i]] = l2_cds.combine([(e, nm[e][i]['z']) for e in nm if nm[e][i]['status'] == 'fresh'], method)[2]
        return out

    base = z_on(names)

    def diff(alt):
        worst, flips = (0.0, None), 0
        for d, z in base.items():
            a = alt[d]
            if (z is None) != (a is None):
                flips += 1
            elif z is not None and abs(a - z) > worst[0]:
                worst = (abs(a - z), d)
        return worst, flips

    by_name = {}
    for e in ents:
        (m, d), flips = diff(z_on({k: v for k, v in names.items() if k != e}))
        by_name[e] = {'max_abs_dZ': _r(m), 'date': d, 'days_value_appears_or_disappears': flips}
    # prints that can reach the range: from (window + change - 1) sessions before d1; the z history reaches further,
    # but only through one change among z_window, which the replay below includes for the prints it drops
    lo = max(0, idx[0] - (p['window_sessions'] + p['change_sessions'] - 1))
    reach = set(sessions[lo: idx[-1] + 1])
    worst = {'max_abs_dZ': 0.0}
    n = 0
    for e in ents:
        for d in sorted(x for x in by[e] if x in reach):
            for k in range(len(by[e][d])):
                n += 1
                alt_by = dict(by[e])
                alt_by[d] = by[e][d][:k] + by[e][d][k + 1:]
                alt = dict(names, **{e: l2_cds.name_series(alt_by, sessions, p)[0]})
                (m, day), flips = diff(z_on(alt))
                if m > worst['max_abs_dZ']:
                    s, kind = by[e][d][k]
                    worst = {'max_abs_dZ': m, 'entity': e, 'print_session': d, 'spread_bp': round(s, 2), 'kind': kind,
                             'moved_on': day}
    worst['max_abs_dZ'] = _r(worst['max_abs_dZ'])
    worst_name = max(by_name.items(), key=lambda kv: kv[1]['max_abs_dZ'] or 0.0)
    return {'sessions_in_range': len(idx), 'prints_tested': n, 'one_print': worst, 'one_name': by_name,
            'max_abs_dZ_one_name': worst_name[1]['max_abs_dZ'], 'worst_name': worst_name[0],
            'note': 'one name dropped can also remove the layer on days it was the only eligible name (counted)'}


# --- the scorecard -----------------------------------------------------------------------------------------------------

def scorecard(d1, d2, results, method, l2_inputs=None, dgs10=None):
    """Everything except the spot check, from a build's layer results (with _series) at d2."""
    days = weekdays(d1, d2)
    cd = build._carry_days(method)
    mg = method['engine']['min_groups_for_stressed']
    out = {'from': d1, 'to': d2, 'weekdays': len(days),
           'note': 'proposed thresholds, not calibrated; movement is shown, never scored', 'layers': {}, 'composites': {}}
    for k, r in results.items():
        s = r.get('_series', [])
        kind = method['layers'][k]['kind']
        out['layers'][k] = {'status_at_end': r.get('status'), 'coverage': coverage(layer_days(s, kind, days, cd)),
                            'single_group': single_group(s, d1, d2, mg), 'band_changes': band_changes(s, d1, d2),
                            'movement': movement(s, d1, d2)}
    comps = {}
    for name in ('junior', 'senior'):
        comp = method['composites'][name]
        cs = composites_on_days(name, comp, results, method, days)
        comps[name] = {c['date']: c for c in cs}
        dates, replay, _ = build.composite_series(name, comp, results, method, d2)
        moves = [(d, c['band_moved']) for d, c in zip(dates, replay) if d1 <= d <= d2 and c['band_moved'] in ('up', 'down', 'capped')]
        out['composites'][name] = dict(composite_coverage(cs), band_changes={'count': len(moves), 'moves': moves[:20]})
    if 'L1' in results and results['L1'].get('_series'):
        out['l1_independence'] = l1_independence(results['L1']['_series'], d1, d2)
    else:
        out['l1_independence'] = {'status': 'L1 has no series in this build'}
    if l2_inputs:
        out['l2_leave_one_out'] = l2_leave_one_out(l2_inputs[0], l2_inputs[1], method, d1, d2)
    else:
        out['l2_leave_one_out'] = {'status': 'L2 inputs not available'}
    if dgs10:
        out['senior_rates'] = senior_rates(comps['senior'], dgs10)
    else:
        out['senior_rates'] = {'status': 'insufficient_data', 'reason': 'DGS10 not available (data/raw/fred/DGS10.csv)'}
    out['checks'] = {
        'single_group_violations': sum(v['single_group']['violations'] for v in out['layers'].values()),
        'senior_rates': out['senior_rates'].get('status'),
        'l1_max_abs_residual_corr': out['l1_independence'].get('max_abs_residual_corr'),
    }
    return out


def spot_check(days, results, method, layers, offline, config_path, root):
    """Rebuild each day from scratch; compare each layer's own Z with the replayed aligned value on that day."""
    cd = build._carry_days(method)
    out = []
    for D in days:
        _, _, res, _ = build.compute_layers(D, layers, offline, config_path, root)
        for k, r in res.items():
            if r.get('status') == 'not_requested':
                continue
            part = build.Aligned(results[k].get('_series', []), method['layers'][k]['kind'], cd).at(D)
            mine = r.get('Z')
            ok = (mine is None and part['z'] is None) or (mine is not None and part['z'] is not None
                                                          and abs(mine - part['z']) < 5e-3)
            out.append({'date': D, 'layer': k, 'build_Z': mine, 'replay_Z': _r(part['z']), 'build_status': r.get('status'),
                        'replay_status': part['status'], 'match': ok})
    return {'days': days, 'checked': len(out), 'mismatches': [x for x in out if not x['match']]}


def run(d1, d2, offline=False, layers=None, config_path=None, root=None, spot=0):
    if d1 > d2:
        raise SystemExit('--from must be on or before --to')
    root = pathlib.Path(root or ROOT)
    method, cfg_hash, results, _ = build.compute_layers(d2, layers, offline, config_path, root)
    l2_inputs = None
    tp = root / 'data' / 'canonical' / 'cds_trades.csv'
    if results.get('L2', {}).get('status') != 'not_requested' and tp.exists():
        trades = l2_cds.load_trades(tp, d2)
        start = min((t['date'] for t in trades), default=None)
        l2_inputs = (trades, l2_cds.raw_sessions(root / 'data' / 'raw' / 'dtcc', d2, start))
    dgs10 = None
    try:
        fred, _, _ = l1_equity.load_fred(['DGS10'], d2, root, offline=True)
        dgs10 = fred.get('DGS10')
    except (OSError, ValueError):
        dgs10 = None
    out = scorecard(d1, d2, results, method, l2_inputs, dgs10)
    out['method'] = {'name': method['name'], 'version': method['version'], 'config_sha256': cfg_hash}
    if spot:
        days = weekdays(d1, d2)
        pick = sorted({days[round(i * (len(days) - 1) / max(1, spot - 1))] for i in range(spot)}) if days else []
        out['spot_check'] = spot_check(pick, results, method, layers, offline, config_path, root)
    return out


def summary(sc):
    L = [f"Stack scorecard {sc['from']} to {sc['to']} ({sc['weekdays']} weekdays), {sc['method']['name']} "
         f"{sc['method']['version']}. {sc['note']}.", '',
         f"{'layer':<5} {'live':>6} {'fresh':>6} {'wf':>6}  {'1-group held':>12} {'violations':>10} {'band moves':>10} "
         f"{'sd dZ (shown)':>13}"]
    for k, v in sc['layers'].items():
        c = v['coverage']
        f = lambda x, fmt='{:.0%}': '-' if x is None else fmt.format(x)
        L.append(f"{k:<5} {f(c['share_live']):>6} {f(c['share_fresh']):>6} {f(c['mean_live_weight_factor'], '{:.2f}'):>6}  "
                 f"{v['single_group']['would_light_with_one_group']:>12} {v['single_group']['violations']:>10} "
                 f"{v['band_changes']['count']:>10} {f(v['movement']['sd_change_Z'], '{:.3f}'):>13}")
    for name, c in sc['composites'].items():
        L.append(f"{name}: published on {c['share_published']:.0%} of days, mean live share {c['mean_live_share']:.0%}, "
                 f"{c['band_changes']['count']} band move(s)")
    lo = sc['l2_leave_one_out']
    if 'one_print' in lo:
        op = lo['one_print']
        L.append(f"L2 leave-one-out: one print moves Z by at most {op['max_abs_dZ']}"
                 + (f" ({op['entity']} {op['kind']} {op['spread_bp']} bp on {op['print_session']}, on {op['moved_on']})" if op.get('entity') else '')
                 + f"; one name by at most {lo['max_abs_dZ_one_name']} ({lo['worst_name']}); {lo['prints_tested']} prints tested")
    else:
        L.append(f"L2 leave-one-out: {lo.get('status')}")
    li = sc['l1_independence']
    if 'legs' in li:
        for leg, v in li['legs'].items():
            r, w = v['residual'], v['raw']
            L.append(f"L1 {leg}: corr of daily change in leg z with QQQ {r.get('corr_QQQ')}, SMH {r.get('corr_SMH')} "
                     f"({r['pairs']} pairs); raw for contrast QQQ {w.get('corr_QQQ')}, SMH {w.get('corr_SMH')}")
    else:
        L.append(f"L1 independence: {li.get('status')}")
    sr = sc['senior_rates']
    L.append(f"Senior vs DGS10 (weekly changes, |r| <= {sr.get('limit_abs_corr', RATES_LIMIT)}): {sr.get('status')}"
             + (f", r = {sr['corr']}" if sr.get('corr') is not None else f" ({sr.get('reason')})"))
    if 'spot_check' in sc:
        s = sc['spot_check']
        L.append(f"Spot check: {s['checked']} layer-days rebuilt from scratch on {', '.join(s['days'])}: "
                 f"{len(s['mismatches'])} mismatch(es)")
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m stack.scorecard', description=__doc__.split('\n')[0])
    ap.add_argument('--from', dest='d1', required=True)
    ap.add_argument('--to', dest='d2', required=True)
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--layers')
    ap.add_argument('--json', help='write the full scorecard here (- prints it instead of the summary)')
    ap.add_argument('--spot-check', type=int, default=0, help='rebuild N days from scratch and compare (slow)')
    ap.add_argument('--config')
    a = ap.parse_args(argv)
    try:
        sc = run(a.d1, a.d2, a.offline, a.layers.split(',') if a.layers else None, a.config, spot=a.spot_check)
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    text = json.dumps(sc, indent=2, allow_nan=False)
    if a.json == '-':
        print(text)
    else:
        print(summary(sc))
        if a.json:
            pathlib.Path(a.json).write_text(text + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
