"""L2 traded credit (stack/l2_cds.py) on synthetic tapes; one integration test on the built canonical table, skipped
when data/ has not been built."""
import copy, datetime as dt, pathlib, sys
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import build, l2_cds  # noqa: E402

BASE = yaml.safe_load(open(ROOT / 'stack' / 'stack-v0.yaml'))
P = {'window_sessions': 5, 'change_sessions': 5, 'min_prints': 5, 'min_sessions_with_prints': 3,
     'max_sessions_since_print': 2, 'z_window': 252, 'z_min_history': 5, 'z_floor_bp': 5.0, 'prints': 'all'}


def weekdays(n, start='2026-01-05'):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def method(**over):
    m = copy.deepcopy(BASE)
    m['groups'] = {'GA': ['AAA'], 'GB': ['BBB'], 'GC': ['CCC']}
    m['layers']['L2'].update(P, entities=['AAA', 'BBB'], min_live_sessions=10)
    m['layers']['L2'].update(over)
    return m


def tape(entity, days, spreads, kind='quoted'):
    """spreads: one list of bp values per day (or a number = that value 3 times)."""
    out = []
    for d, xs in zip(days, spreads):
        xs = [xs] * 3 if isinstance(xs, (int, float)) else xs
        out += [{'date': d, 'entity': entity, 'kind': kind, 'spread_bp': float(x)} for x in xs]
    return out


def one(prints_by_day, days, **over):
    p = dict(P, **over)
    prints = {d: [(x, 'quoted') for x in xs] for d, xs in zip(days, prints_by_day) if xs}
    return l2_cds.name_series(prints, days, p)[0]


# --- eligibility and the level ---------------------------------------------------------------------------------------

def test_eligibility_needs_5_prints_on_3_sessions_and_a_recent_print():
    days = weekdays(5)
    assert one([[], [], [], [1, 1, 1], [1, 1]], days)[-1]['status'] == 'ineligible'          # 5 prints, 2 sessions
    r = one([[1], [], [1, 1], [1, 1], []], days)[-1]                                         # 5 on 3, last 1 session ago
    assert r['eligible'] and r['window_prints'] == 5 and r['window_sessions_with_prints'] == 3
    r = one([[1, 1], [1, 1], [1], [], []], days)[-1]                                         # last print 2 sessions ago
    assert r['status'] == 'ineligible' and any('no print in the last 2' in x for x in r['reasons'])
    r = one([[1], [1, 1], [1], [], []], days)[-1]                                            # 4 prints
    assert r['status'] == 'ineligible' and any('4 prints' in x for x in r['reasons'])


def test_eligibility_needs_a_full_window():
    r = one([[1, 1], [1, 1], [1, 1]], weekdays(3))
    assert all(x['status'] == 'ineligible' for x in r) and 'fewer than 5 sessions' in r[-1]['reasons'][0]


def test_level_is_the_median_of_daily_medians():
    r = one([[100, 100, 300], [], [200, 200], [400], []], weekdays(5))[-1]
    assert r['level_bp'] == 200  # daily medians 100, 200, 400; the median of all 6 prints would be 200 too only by luck
    r = one([[100, 100, 100, 100, 900], [], [300], [310], []], weekdays(5))[-1]
    assert r['level_bp'] == 300  # one busy day does not dominate: median of all prints would be 100


def test_change_needs_an_eligible_base_and_z_needs_history():
    days = weekdays(40)
    r = one([[100] * 3] * 40, days)
    assert [x['status'] for x in r[:4]] == ['ineligible'] * 4
    assert [x['status'] for x in r[4:9]] == ['no_base'] * 5          # base 5 sessions back not eligible yet
    assert [x['status'] for x in r[9:14]] == ['insufficient_history'] * 5
    assert r[14]['status'] == 'fresh' and r[14]['z_n'] == 5 and r[14]['change_bp'] == 0


def test_z_of_the_change_against_the_names_own_history():
    days = weekdays(40)
    spreads = [[100] * 3] * 30 + [[150] * 3] * 10
    r = one(spreads, days)
    i = 32  # third session at 150: the median of the last five daily medians moves to 150
    assert r[i]['level_bp'] == 150 and r[i]['base_level_bp'] == 100 and r[i]['change_bp'] == 50
    assert r[i]['z'] == pytest.approx(50 / 5.0) and r[i]['z_floored']  # flat history: the 5 bp floor sets the scale


# --- the layer -------------------------------------------------------------------------------------------------------

def test_ineligible_name_is_left_out_not_carried():
    days = weekdays(40)
    tr = tape('AAA', days, [100] * 40) + tape('BBB', days[:30], [200] * 30)
    out = l2_cds.snapshot(days[-1], tr, days, method())
    assert out['names']['AAA']['status'] == 'fresh'
    assert out['names']['BBB']['status'] == 'ineligible' and out['names']['BBB']['z'] is None
    assert out['names']['BBB']['last_print_session'] == days[29] and out['names']['BBB']['sessions_since_print'] == 10
    assert list(out['groups']) == ['GA'] and out['groups_live'] == 1 and 'GB' not in out['attribution']


def test_no_lookahead_rows_after_the_as_of_date_change_nothing():
    days = weekdays(45)
    tr = tape('AAA', days[:40], [100] * 40) + tape('BBB', days[:40], [200] * 40)
    later = tape('AAA', days[40:], [5000] * 5) + tape('BBB', days[40:], [9000] * 5)
    a = l2_cds.snapshot(days[39], tr, days[:40], method())
    b = l2_cds.snapshot(days[39], tr + later, days, method())
    assert a == b


def test_sessions_after_the_as_of_date_are_not_counted():
    days = weekdays(30)
    out = l2_cds.snapshot(days[19], tape('AAA', days, [100] * 30), days, method())
    assert out['sessions_archived'] == 20 and out['session'] == days[19]


def test_session_gate_counts_live_sessions_not_raw_files():
    # 30 raw files, but the first live layer value needs a 5-session window, a 5-session change and 5 earlier changes
    # (z_min_history 5): sessions 15 to 30 are live, 16 of them.
    days = weekdays(30)
    tr = tape('AAA', days, [100] * 30) + tape('BBB', days, [200] * 30)
    files = l2_cds.snapshot(days[-1], tr, days, method(min_live_sessions=30))
    g = files['gate']
    assert g['raw_files'] == 30 and g['sessions_live'] == 16 and not g['met'] and not files['scored']
    assert files['role'].startswith('observation only') and '30 raw files' in files['role']
    assert build._weights({'layers': {'L1': 0.6, 'L2': 0.4}}, {'L2': files}) == {'L1': 0.6, 'L2': 0.0}
    live = l2_cds.snapshot(days[-1], tr, days, method(min_live_sessions=16))
    assert live['scored'] and build._weights({'layers': {'L1': 0.6, 'L2': 0.4}}, {'L2': live}) == {'L1': 0.6, 'L2': 0.4}
    assert [p['scored'] for p in live['_series']] == [False] * 29 + [True]
    assert [p['sessions_live'] for p in live['_series']][-3:] == [14, 15, 16]


def test_session_gate_ignores_sessions_where_no_name_was_eligible():
    # a backfilled archive with a long hole: files on disk for every day, prints only on some
    days = weekdays(40)
    spreads = [100] * 20 + [[]] * 10 + [100] * 10
    tr = tape('AAA', days, spreads) + tape('BBB', days, spreads)
    out = l2_cds.snapshot(days[-1], tr, days, method(min_live_sessions=10))
    live = [p['session'] for p in out['_series'] if p['status'] == 'fresh']
    assert out['gate']['raw_files'] == 40 and out['gate']['sessions_live'] == len(live) < 40
    assert out['scored'] == (len(live) >= 10)


def test_quoted_and_upfront_implied_prints_are_labelled():
    days = weekdays(20)
    tr = tape('AAA', days, [100] * 20, kind='upfront') + tape('BBB', days, [200] * 20)
    tr += tape('BBB', days[-1:], [[205]], kind='upfront')
    out = l2_cds.snapshot(days[-1], tr, days, method())
    a, b = out['names']['AAA'], out['names']['BBB']
    assert a['kind'] == 'upfront_implied' and 'implied from an upfront' in a['note'] and a['first_quoted_session'] is None
    assert b['kind'] == 'mixed' and b['window_upfront_prints'] == 1 and b['window_quoted_prints'] == 15
    assert 'flat hazard, 40% recovery' in out['convention']


def test_quoted_first_uses_the_days_quoted_prints_when_there_are_any():
    days = weekdays(5)
    prints = {d: [(100.0, 'quoted'), (300.0, 'upfront'), (300.0, 'upfront')] for d in days}
    r_all = l2_cds.name_series(prints, days, dict(P, prints='all'))[0][-1]
    r_q = l2_cds.name_series(prints, days, dict(P, prints='quoted_first', min_prints=5))[0][-1]
    assert r_all['level_bp'] == 300 and r_q['level_bp'] == 100 and r_q['window_prints'] == 5


def test_a_weekend_as_of_carries_fridays_value_and_says_so():
    days = weekdays(25)  # ends on a Friday
    assert dt.date.fromisoformat(days[-1]).weekday() == 4
    tr = tape('AAA', days, [100] * 25) + tape('BBB', days, [200] * 25)
    sat = (dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1)).isoformat()
    out = l2_cds.snapshot(sat, tr, days, method())
    assert out['status'] == 'carried' and out['session'] == days[-1] and out['available'] == days[-1]
    assert out['names']['AAA']['status'] == 'carried' and out['band_status'].startswith('held')


def test_one_stressed_name_cannot_light_the_layer():
    days = weekdays(60)
    spreads = [[100 + (i % 3)] * 3 for i in range(50)] + [[100 + 40 * k] * 3 for k in range(1, 11)]
    tr = tape('AAA', days, spreads) + tape('BBB', days, [[200 + (i % 3)] * 3 for i in range(60)])
    out = l2_cds.snapshot(days[-1], tr, days, method())
    assert out['groups']['GA']['band'] in ('Stressed', 'Severe') and out['groups']['GB']['band'] not in ('Stressed', 'Severe')
    assert out['two_group']['cap'] == 'Watch' and 'needs 2' in out['two_group']['reason']
    assert out['band'] in ('Calm', 'Normal', 'Watch') and out['cap_infeasible']
    assert all(p['band'] in ('Calm', 'Normal', 'Watch') for p in l2_cds.layer_series(tr, days, method())[0] if p['band'])


def test_attribution_shows_capped_and_uncapped_weights():
    days = weekdays(20)
    tr = tape('AAA', days, [100] * 20) + tape('BBB', days, [200] * 20)
    out = l2_cds.snapshot(days[-1], tr, days, method())
    for g in ('GA', 'GB'):
        a = out['attribution'][g]
        assert a['weight'] == 0.5 and a['uncapped_weight'] == 0.5 and 'uncapped_contribution' in a


def test_trades_on_days_without_a_dtcc_file_are_counted_not_used():
    days = weekdays(20)
    tr = tape('AAA', days, [100] * 20) + [{'date': '2026-01-10', 'entity': 'AAA', 'kind': 'quoted', 'spread_bp': 999.0}]
    out = l2_cds.snapshot(days[-1], tr, days, method())
    assert out['trades_outside_sessions'] == 1


def test_leave_one_out_is_reported_per_name():
    days = weekdays(20)
    tr = tape('AAA', days, [[100, 100, 100]] * 19 + [[100, 400]]) + tape('BBB', days, [200] * 20)
    out = l2_cds.snapshot(days[-1], tr, days, method())
    assert out['names']['AAA']['loo_bp'] is not None and out['names']['AAA']['loo_bp'] >= 0


def test_raw_sessions_are_weekday_files_present(tmp_path):
    for d in ('2026-11-20', '2026-11-21', '2026-11-23', '2026-11-24', '2026-11-25'):
        (tmp_path / f'{d}.zip').write_bytes(b'PK')
    (tmp_path / '2026-11-24.zip').write_bytes(b'')  # an empty file left by an old version is not a session
    (tmp_path / 'conflicts').mkdir()
    (tmp_path / 'conflicts' / '2026-11-25-abc.zip').write_bytes(b'PK')
    assert l2_cds.raw_sessions(tmp_path, as_of='2026-11-24') == ['2026-11-20', '2026-11-23']
    assert l2_cds.raw_sessions(tmp_path, as_of='2026-11-30', start='2026-11-21') == ['2026-11-23', '2026-11-25']


# --- the built data (skips until harness/dtcc.py has run) -------------------------------------------------------------

CANON = ROOT / 'data' / 'canonical' / 'cds_trades.csv'


@pytest.mark.skipif(not CANON.exists(), reason='run python harness/dtcc.py first')
def test_canonical_l2_builds_for_a_recent_day():
    as_of = max(r['date'] for r in l2_cds.load_trades(CANON))
    out, inputs = build.run(as_of, ['L2'], offline=True)
    l2 = out['layers']['L2']
    assert l2['status'] in ('fresh', 'carried', 'unavailable') and l2['sessions_archived'] > 0
    assert inputs[0]['path'] == 'data/canonical/cds_trades.csv' or any(i['path'].endswith('cds_trades.csv') for i in inputs)
    assert l2['inputs_summary']['raw_files_not_matching_manifest'] == []
    for e, n in l2['names'].items():
        assert n['status'] in ('fresh', 'ineligible', 'no_base', 'insufficient_history', 'carried'), e
        assert n['status'] != 'fresh' or n['z'] is not None
