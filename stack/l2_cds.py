"""L2 traded credit: single-name 5Y CDS from data/canonical/cds_trades.csv (built by python harness/dtcc.py).

Per name, on each session t (a session is a DTCC end-of-day file date present in data/raw/dtcc):
  daily median    median of the session's priced spreads (spread_bp; quoted and upfront-implied, or quoted_first)
  level           median of the daily medians over the last window_sessions sessions
  change          level(t) - level(t - change_sessions); the earlier level must itself be eligible, so a thin base
                  cannot fake a move
  z               robust z of the change against the name's own previous changes (z_window obs, z_min_history needed)
  eligible        >= min_prints prints on >= min_sessions_with_prints of the window's sessions, and a print in the last
                  max_sessions_since_print sessions. An ineligible name is left out that day (status says why); its
                  last value is never carried as fresh.
Groups: names map to borrower groups (method `groups`); a group's z is the mean of its eligible names' z. Layer
Z = sum of capped group weights x group z (20% cap, equal weights and cap_infeasible below 5 groups). The band is
replayed over every session up to the as-of date with junior hysteresis and the 2-group rule.

Session gate (enforced here: `scored`). L2 carries weight 0 in every composite until it has had a live layer value
(at least one eligible name with a z) on min_live_sessions sessions. Raw DTCC files on disk do not count: anyone can
download a year of them after the fact, so a gate on files would open the day the archive is backfilled. Both counts
are reported (gate.sessions_live, gate.raw_files).
Upfront-implied spreads follow the harness convention (flat hazard, 40% recovery, 4.5% rate) and are labelled.
No lookahead: rows dated after the as-of date and sessions after it are never read.

Per name, first_quoted_session and changes_before_first_quote_in_history are reported because the harness picks the
sign of an unsigned upfront from the previous level when a day has no quote: for a name trading below its coupon, an
upfront-only start can sit on the wrong sign until the first quoted print (seen on META before April 2026).
TODO: only ORCL, CRWV and META exist in the canonical table (see the note in stack/stack-v0.yaml).
"""
import csv, datetime as dt, pathlib, statistics

from . import engine
from .config import cuts as config_cuts
from .fetch import file_record

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRADES = ROOT / 'data' / 'canonical' / 'cds_trades.csv'
RAW = ROOT / 'data' / 'raw' / 'dtcc'
MANIFEST = ROOT / 'manifests' / 'dtcc_sha256.csv'
CONVENTION = ('upfront-implied spreads: flat hazard, 40% recovery, 4.5% flat rate, sign of the unsigned upfront chosen '
              'nearest the day\'s quoted median or the previous level (harness/dtcc.py)')


# --- inputs ----------------------------------------------------------------------------------------------------------

def load_trades(path=TRADES, as_of=None):
    """Canonical trade rows with date <= as_of: dicts with date, entity, kind, spread_bp (float)."""
    out = []
    for r in csv.DictReader(open(path)):
        if as_of and r['date'] > as_of:
            continue
        out.append({'date': r['date'], 'entity': r['entity'], 'kind': r['kind'], 'spread_bp': float(r['spread_bp'])})
    return out


def raw_sessions(raw_dir=RAW, as_of=None, start=None):
    """Session dates: weekday DTCC files present (non-empty) in raw_dir, start <= date <= as_of."""
    out = []
    for p in sorted(pathlib.Path(raw_dir).glob('*.zip')):
        try:
            d = dt.date.fromisoformat(p.stem)
        except ValueError:
            continue
        if d.weekday() >= 5 or p.stat().st_size == 0:
            continue
        s = d.isoformat()
        if (as_of and s > as_of) or (start and s < start):
            continue
        out.append(s)
    return out


# --- one name ----------------------------------------------------------------------------------------------------------

def _pick(xs, mode):
    if mode == 'quoted_first':
        q = [x for x in xs if x[1] == 'quoted']
        return q or xs
    return xs


def name_series(prints, sessions, p):
    """prints: {date: [(spread_bp, kind)]} for one name. Returns one dict per session (oldest first)."""
    W, C = p['window_sessions'], p['change_sessions']
    minp, mins, since = p['min_prints'], p['min_sessions_with_prints'], p['max_sessions_since_print']
    mode = p.get('prints', 'all')
    daily = []
    for d in sessions:
        use = _pick(prints.get(d, []), mode)
        daily.append({'median': statistics.median([x for x, _ in use]) if use else None, 'n': len(use),
                      'quoted': sum(1 for _, k in use if k == 'quoted'), 'upfront': sum(1 for _, k in use if k != 'quoted'),
                      'prints': use})
    out, history, first_quote = [], [], None
    for i, d in enumerate(sessions):
        if first_quote is None and daily[i]['quoted']:
            first_quote = d
        rec = {'session': d, 'daily_median_bp': daily[i]['median'], 'daily_prints': daily[i]['n']}
        if i < W - 1:
            rec.update(eligible=False, status='ineligible', reasons=[f'fewer than {W} sessions archived'])
            out.append(rec)
            continue
        win = daily[i - W + 1: i + 1]
        n = sum(x['n'] for x in win)
        s = sum(1 for x in win if x['n'])
        recent = any(x['n'] for x in daily[i - since + 1: i + 1])
        reasons = []
        if n < minp:
            reasons.append(f'{n} prints in the last {W} sessions, needs {minp}')
        if s < mins:
            reasons.append(f'prints on {s} of the last {W} sessions, needs {mins}')
        if not recent:
            reasons.append(f'no print in the last {since} sessions')
        nq, nu = sum(x['quoted'] for x in win), sum(x['upfront'] for x in win)
        rec.update(window_prints=n, window_sessions_with_prints=s, window_quoted=nq, window_upfront=nu,
                   kind='quoted' if nq and not nu else ('upfront_implied' if nu and not nq else ('mixed' if nq else None)))
        if reasons:
            rec.update(eligible=False, status='ineligible', reasons=reasons)
            out.append(rec)
            continue
        level = statistics.median([x['median'] for x in win if x['median'] is not None])
        rec.update(eligible=True, level_bp=level)
        base = out[i - C] if i - C >= 0 else None
        if not base or not base.get('eligible'):
            rec.update(status='no_base', reasons=[f'level {C} sessions earlier was not eligible'])
            out.append(rec)
            continue
        change = level - base['level_bp']
        z = engine.robust_z(change, history, window=p['z_window'], min_history=p['z_min_history'], floor=p['z_floor_bp'])
        history.append(change)
        rec.update(base_level_bp=base['level_bp'], change_bp=change, z=z['z'], z_n=z['n'],
                   z_median_bp=z.get('median'), z_scale_bp=z.get('scale'), z_floored=z.get('floored'),
                   status='fresh' if z['status'] == 'ok' else 'insufficient_history')
        if z['status'] != 'ok':
            rec['reasons'] = [f"{z['n']} earlier changes, needs {p['z_min_history']}"]
        out.append(rec)
    for r in out:
        r['first_quoted_session'] = first_quote if first_quote and first_quote <= r['session'] else None
    return out, daily


def leave_one_out(daily, i, W):
    """Largest move of the level at session i from removing any single print in its window (bp), or None."""
    win = daily[i - W + 1: i + 1]
    meds = [x['median'] for x in win if x['median'] is not None]
    if not meds:
        return None
    level = statistics.median(meds)
    worst = 0.0
    for j, x in enumerate(win):
        for k in range(x['n']):
            rest = [v for kk, (v, _) in enumerate(x['prints']) if kk != k]
            m2 = [y['median'] for jj, y in enumerate(win) if jj != j and y['median'] is not None]
            if rest:
                m2.append(statistics.median(rest))
            if m2:
                worst = max(worst, abs(statistics.median(m2) - level))
    return worst


# --- the layer -------------------------------------------------------------------------------------------------------

def prints_by_name(trades, sessions, entities):
    """({entity: {session: [(spread_bp, kind)]}}, trades on dates that are not sessions)."""
    sset = set(sessions)
    by = {e: {} for e in entities}
    outside = 0
    for r in trades:
        if r['entity'] not in by:
            continue
        if r['date'] not in sset:
            outside += 1
            continue
        by[r['entity']].setdefault(r['date'], []).append((r['spread_bp'], r['kind']))
    return by, outside


def combine(fresh, method):
    """One session's layer from its fresh names. fresh: [(entity, z)]. Returns (groups, cap weights or None, Z or
    None): group z = mean of its names' z, Z = capped group weights x group z."""
    eng = method['engine']
    cut, tau = config_cuts(method), eng['tau']
    owner = {e: g for g, es in method['groups'].items() for e in es}
    gz = {}
    for e, z in fresh:
        gz.setdefault(owner[e], []).append((e, z))
    groups = {g: {'names': [e for e, _ in v], 'z': sum(z for _, z in v) / len(v)} for g, v in gz.items()}
    for g in groups.values():
        g['level'] = engine.logistic(g['z'], tau)
        g['band'] = engine.BANDS[engine.raw_band(g['level'], cut)]
    if not groups:
        return groups, None, None
    capw = engine.cap_weights({g: 1.0 for g in groups}, eng['group_cap'])
    return groups, capw, sum(capw['weights'][g] * groups[g]['z'] for g in groups)


def layer_series(trades, sessions, method):
    """Per-session layer values for every session (oldest first) and per-name series."""
    p = method['layers']['L2']
    eng = method['engine']
    cut = config_cuts(method)
    tau = eng['tau']
    ents = p['entities']
    by, outside = prints_by_name(trades, sessions, ents)
    names, dailies = {}, {}
    for e in ents:
        names[e], dailies[e] = name_series(by[e], sessions, p)
    series, live_so_far = [], 0
    for i, d in enumerate(sessions):
        groups, capw, Z = combine([(e, names[e][i]['z']) for e in ents if names[e][i]['status'] == 'fresh'], method)
        live_so_far += 1 if groups else 0
        pt = {'session': d, 'raw_files': i + 1, 'sessions_live': live_so_far,
              'scored': live_so_far >= p['min_live_sessions'], 'groups': groups}
        if groups:
            capb, why, hot = engine.two_group_cap({g: v['level'] for g, v in groups.items()}, cut, eng['min_groups_for_stressed'])
            lv = engine.logistic(Z, tau)
            pt.update(status='fresh', Z=Z, level=lv, cap=capb, cap_reason=why, groups_stressed=hot, weights=capw)
        else:
            pt.update(status='unavailable', Z=None, level=None, cap=None, cap_reason=None, groups_stressed=[], weights=None)
        series.append(pt)
    h = eng['hysteresis']
    bands = engine.replay_bands(series, 'junior', cut, up_closes=h['junior_up_closes'], down_margin=h['down_margin'])
    for pt, b in zip(series, bands):
        pt.update(band=b['band'], raw_band=b['raw_band'], band_moved=b['moved'], band_reason=b['reason'])
    return series, names, dailies, outside


def _r(x, k=2):
    return None if x is None else round(x, k)


def _gate(p, live, files, met):
    return {'min_live_sessions': p['min_live_sessions'], 'sessions_live': live, 'raw_files': files, 'met': met,
            'counts': 'sessions with a live layer value from eligible names; raw files on disk do not count'}


def _role(gate):
    if gate['met']:
        return 'scored sleeve'
    return (f"observation only: weight 0 in every composite until L2 has a live value on {gate['min_live_sessions']} "
            f"sessions ({gate['sessions_live']} so far; {gate['raw_files']} raw files on disk, which do not count)")


def snapshot(as_of, trades, sessions, method):
    """The L2 result for as_of (dict). `_series` holds the per-session points for composites (not published)."""
    p = method['layers']['L2']
    sessions = [s for s in sessions if s <= as_of]
    trades = [t for t in trades if t['date'] <= as_of]
    base = {'layer': 'L2', 'name': p['name'], 'kind': 'junior', 'build_date': as_of, 'as_of': None,
            'convention': CONVENTION, 'prints': p.get('prints', 'all'), 'sessions_archived': len(sessions)}
    if not sessions:
        gate = _gate(p, 0, 0, False)
        return dict(base, gate=gate, scored=False, role=_role(gate), status='unavailable',
                    reason='no DTCC sessions on or before the as-of date', session=None,
                    available=None, Z=None, level=None, published=None, band=None, _series=[])
    series, names, dailies, outside = layer_series(trades, sessions, method)
    i = len(sessions) - 1
    pt = series[-1]
    gate = _gate(p, pt['sessions_live'], pt['raw_files'], pt['scored'])
    base.update(gate=gate, scored=gate['met'], role=_role(gate))
    d = sessions[-1]
    status = pt['status'] if d == as_of or pt['status'] != 'fresh' else 'carried'
    W = p['window_sessions']
    per = {}
    owner = {e: g for g, es in method['groups'].items() for e in es}
    for e in p['entities']:
        r = names[e][i]
        last_i = max((k for k, x in enumerate(dailies[e]) if x['n']), default=None)
        last = None if last_i is None else sessions[last_i]
        hist_unanchored = sum(1 for x in names[e][:i] if x.get('change_bp') is not None and x['first_quoted_session'] is None)
        q = {'group': owner[e], 'status': r['status'] if status != 'carried' or r['status'] != 'fresh' else 'carried',
             'reasons': r.get('reasons', []), 'session': d, 'last_print_session': last,
             'sessions_since_print': None if last_i is None else i - last_i,
             'daily_median_bp': _r(r['daily_median_bp']), 'daily_prints': r['daily_prints'],
             'window_prints': r.get('window_prints'), 'window_sessions_with_prints': r.get('window_sessions_with_prints'),
             'window_quoted_prints': r.get('window_quoted'), 'window_upfront_prints': r.get('window_upfront'),
             'kind': r.get('kind'), 'level_bp': _r(r.get('level_bp')), 'base_level_bp': _r(r.get('base_level_bp')),
             'change_bp': _r(r.get('change_bp')), 'z': _r(r.get('z'), 3), 'z_history_n': r.get('z_n'),
             'z_median_bp': _r(r.get('z_median_bp')), 'z_scale_bp': _r(r.get('z_scale_bp')), 'z_floored': r.get('z_floored'),
             'loo_bp': _r(leave_one_out(dailies[e], i, W)) if r.get('eligible') else None,
             'first_quoted_session': r['first_quoted_session'],
             'changes_before_first_quote_in_history': hist_unanchored}
        if q['kind'] == 'upfront_implied':
            q['note'] = 'every print in the window is implied from an upfront (a model), none is a quoted spread'
        per[e] = q
    groups = {}
    for g, v in pt['groups'].items():
        groups[g] = {'names': v['names'], 'z': _r(v['z'], 3), 'level': _r(v['level']), 'band': v['band']}
    attribution = {}
    if pt['weights']:
        w = pt['weights']
        for g, v in pt['groups'].items():
            attribution[g] = {'weight': _r(w['weights'][g], 4), 'contribution': _r(w['weights'][g] * v['z'], 3),
                              'uncapped_weight': _r(w['uncapped'][g], 4), 'uncapped_contribution': _r(w['uncapped'][g] * v['z'], 3)}
    has = pt['Z'] is not None
    out = dict(base, status=status, session=d, as_of=d if has else None, available=d if has else None,
               Z=_r(pt['Z'], 3), level=_r(pt['level']),
               published=engine.published(pt['level']), raw_band=pt['raw_band'], band=pt['band'],
               band_status='fresh' if status == 'fresh' else 'held (no fresh value: the band cannot move)',
               band_moved=pt['band_moved'], band_reason=pt['band_reason'],
               two_group={'cap': pt['cap'], 'reason': pt['cap_reason'], 'groups_stressed_or_worse': pt['groups_stressed']},
               cap_infeasible=bool(pt['weights'] and pt['weights']['cap_infeasible']),
               groups_live=len(pt['groups']), groups_configured=len({owner[e] for e in p['entities']}),
               groups=groups, attribution=attribution, names=per, trades_outside_sessions=outside,
               _series=[{'session': s['session'], 'known': s['session'], 'Z': s['Z'], 'level': s['level'],
                         'status': s['status'], 'scored': s['scored'], 'sessions_live': s['sessions_live'],
                         'band': s['band'], 'raw_band': s['raw_band'], 'band_moved': s['band_moved'],
                         'groups_stressed': s['groups_stressed'],
                         'group_z': {g: v['z'] for g, v in s['groups'].items()}} for s in series])
    if pt['status'] != 'fresh':
        out['reason'] = 'no eligible name with enough history on this session'
    return out


def compute(as_of, method, ctx):
    """Build-facing entry point: (result, inputs). ctx: {'root': repo path, 'offline': bool}."""
    root = pathlib.Path(ctx.get('root') or ROOT)
    tpath = root / 'data' / 'canonical' / 'cds_trades.csv'
    if not tpath.exists():
        return ({'layer': 'L2', 'name': method['layers']['L2']['name'], 'kind': 'junior', 'build_date': as_of,
                 'as_of': None, 'status': 'unavailable', 'reason': 'data/canonical/cds_trades.csv missing: run python harness/dtcc.py',
                 'available': None, 'Z': None, 'band': None, '_series': []}, [])
    trades = load_trades(tpath, as_of)
    start = min((t['date'] for t in trades), default=None)
    raw = root / 'data' / 'raw' / 'dtcc'
    sessions = raw_sessions(raw, as_of, start)
    manifest = {}
    mpath = root / 'manifests' / 'dtcc_sha256.csv'
    if mpath.exists():
        manifest = {r['date']: r['sha256'] for r in csv.DictReader(open(mpath))}
    inputs = [file_record(tpath, root)]
    mismatched = []
    for s in sessions:
        rec = file_record(raw / f'{s}.zip', root)
        if s in manifest and manifest[s] != rec['sha256']:
            mismatched.append(s)
        rec['manifest_match'] = manifest.get(s) == rec['sha256'] if s in manifest else None
        inputs.append(rec)
    res = snapshot(as_of, trades, sessions, method)
    res['inputs_summary'] = {'cds_trades_sha256': inputs[0]['sha256'], 'raw_files': len(sessions),
                             'raw_files_not_matching_manifest': mismatched}
    return res, inputs


__all__ = ['load_trades', 'raw_sessions', 'name_series', 'layer_series', 'snapshot', 'compute']
