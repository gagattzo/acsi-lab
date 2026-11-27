"""Stack scorecard (stack/scorecard.py) on synthetic series: no network, no data/."""
import copy, datetime as dt, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import config, scorecard as sc  # noqa: E402

METHOD, _ = config.load()


def pt(session, z=0.0, status='fresh', **kw):
    return dict({'session': session, 'known': session, 'Z': z, 'status': status}, **kw)


def test_corr_needs_pairs_and_variance():
    assert sc.corr([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert sc.corr([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert sc.corr([1, 2], [1, 2]) is None and sc.corr([1, 1, 1], [1, 2, 3]) is None
    assert sc.corr([1, None, 3, 4], [1, 5, 3, 4]) == pytest.approx(1.0)  # pairs with a gap are dropped


def test_coverage_counts_live_fresh_and_weight_factor():
    parts = [{'status': 'fresh', 'z': 1.0, 'weight_factor': 1.0}, {'status': 'carried', 'z': 1.0, 'weight_factor': 0.5},
             {'status': 'stale', 'z': None, 'weight_factor': 0.0}, {'status': 'missing', 'z': None, 'weight_factor': 0.0}]
    c = sc.coverage(parts)
    assert c['share_live'] == 0.5 and c['share_fresh'] == 0.25 and c['mean_live_weight_factor'] == pytest.approx(0.375)
    assert c['statuses'] == {'carried': 1, 'fresh': 1, 'missing': 1, 'stale': 1}


def test_single_group_counts_held_days_and_violations():
    s = [pt('2026-11-02', raw_band='Stressed', band='Watch', groups_stressed=['A']),          # held by the rule
         pt('2026-11-03', raw_band='Severe', band='Severe', groups_stressed=['A', 'B']),      # two groups: allowed
         pt('2026-11-04', raw_band='Normal', band='Normal', groups_stressed=[]),
         pt('2026-11-05', raw_band='Stressed', band='Stressed', groups_stressed=['A']),       # a violation
         pt('2026-11-06', status='carried', raw_band=None, band='Stressed', groups_stressed=['A'])]  # carried: not a day
    r = sc.single_group(s, '2026-11-01', '2026-11-30')
    assert r['fresh_points'] == 4 and r['would_light_with_one_group'] == 2 and r['violations'] == 1
    assert r['violation_dates'] == ['2026-11-05'] and not r['ok']
    assert sc.single_group(s[:3], '2026-11-01', '2026-11-30')['ok']


def test_band_changes_and_movement_are_counted_in_range_only():
    s = [pt('2026-10-30', 0.0, band_moved='up'), pt('2026-11-02', 1.0, band_moved='init'),
         pt('2026-11-03', 3.0, band_moved='up', band='Watch'), pt('2026-11-04', 2.0, band_moved='down', band='Normal'),
         pt('2026-11-05', 2.0, band_moved='capped', band='Watch')]
    assert sc.band_changes(s, '2026-11-01', '2026-11-30')['count'] == 3
    m = sc.movement(s, '2026-11-01', '2026-11-30')
    assert m['changes'] == 3 and m['sd_change_Z'] is not None and 'not scored' in m['note']


def test_l1_independence_separates_residual_from_raw():
    days = sc.weekdays('2026-06-01', '2026-08-31')
    s, z, zr = [], 0.0, 0.0
    for i, d in enumerate(days):
        q = ((i * 7) % 11 - 5) / 100.0          # a factor return
        z += ((i * 5) % 7 - 3) / 10.0            # residual z: its own path, unrelated to q
        zr += -30 * q                            # raw z: falls when the market rises
        s.append({'session': d, 'legs_z': {'E1': z}, 'legs_z_raw': {'E1': zr}, 'factor_returns': {'QQQ': q, 'SMH': q}})
    r = sc.l1_independence(s, days[0], days[-1])
    e1 = r['legs']['E1']
    assert e1['raw']['corr_QQQ'] == pytest.approx(-1.0, abs=1e-6)
    assert abs(e1['residual']['corr_QQQ']) < 0.3 and r['max_abs_residual_corr'] < 0.3
    short = sc.l1_independence(s[:10], days[0], days[9])
    assert short['legs']['E1']['residual']['corr_QQQ'] is None and 'insufficient' in short['legs']['E1']['residual']['status']


def test_fred_value_is_available_on_the_next_weekday():
    obs = {'2026-11-19': 4.0, '2026-11-20': 4.1}               # Thursday, Friday
    assert sc.fred_available(obs, '2026-11-20') == ('2026-11-19', 4.0)
    assert sc.fred_available(obs, '2026-11-23') == ('2026-11-20', 4.1)


def weekly(n, senior, rate):
    """n Fridays from 2026-01-09: senior composite Z and DGS10 per week (DGS10 dated the Thursday before)."""
    comps, fred = {}, {}
    d = dt.date(2026, 1, 9)
    for i in range(n):
        comps[d.isoformat()] = {'Z': senior(i)}
        fred[(d - dt.timedelta(days=1)).isoformat()] = rate(i)
        d += dt.timedelta(days=7)
    return comps, fred


def test_senior_rates_reports_insufficient_data_with_few_weeks():
    comps, fred = weekly(5, lambda i: i * 0.1, lambda i: 4 + i * 0.01)
    r = sc.senior_rates(comps, fred)
    assert r['status'] == 'insufficient_data' and r['corr'] is None and r['pairs'] == 4


def test_senior_rates_passes_when_unrelated_and_fails_when_it_tracks_rates():
    wig = lambda i: ((i * 7) % 5 - 2) / 10.0
    comps, fred = weekly(30, lambda i: sum(wig(k) for k in range(i)), lambda i: 4 + ((i * 3) % 4) * 0.05)
    ok = sc.senior_rates(comps, fred)
    assert ok['pairs'] == 29 and ok['status'] in ('pass', 'fail') and ok['corr'] is not None
    comps, fred = weekly(30, lambda i: 10 * (4 + ((i * 3) % 4) * 0.05), lambda i: 4 + ((i * 3) % 4) * 0.05)
    bad = sc.senior_rates(comps, fred)
    assert bad['status'] == 'fail' and bad['corr'] == pytest.approx(1.0)
    # unpublished weeks are skipped, not filled
    comps[sorted(comps)[3]]['Z'] = None
    assert sc.senior_rates(comps, fred)['pairs'] == 27


def l2_method():
    m = copy.deepcopy(METHOD)
    m['groups'] = {'GA': ['AAA'], 'GB': ['BBB']}
    m['layers']['L2'].update(entities=['AAA', 'BBB'], z_min_history=5, min_live_sessions=1)
    return m


def test_l2_leave_one_out_finds_the_print_and_the_name_that_move_the_layer():
    days = sc.weekdays('2026-01-05', '2026-03-13')
    trades = []
    for i, d in enumerate(days):
        for k in range(3):
            trades.append({'date': d, 'entity': 'AAA', 'kind': 'quoted', 'spread_bp': 100.0 + (i % 3) + k})
            trades.append({'date': d, 'entity': 'BBB', 'kind': 'quoted', 'spread_bp': 200.0 + (i % 4)})
    # one far print on a thin day for AAA: only 2 prints that day, so the median of the day moves
    thin = days[-8]
    trades = [t for t in trades if not (t['entity'] == 'AAA' and t['date'] == thin)]
    trades += [{'date': thin, 'entity': 'AAA', 'kind': 'quoted', 'spread_bp': 101.0},
               {'date': thin, 'entity': 'AAA', 'kind': 'upfront', 'spread_bp': 900.0}]
    r = sc.l2_leave_one_out(trades, days, l2_method(), days[-10], days[-1])
    op = r['one_print']
    assert op['entity'] == 'AAA' and op['print_session'] == thin and op['max_abs_dZ'] > 0
    assert r['one_name']['AAA']['max_abs_dZ'] > 0 and r['one_name']['BBB']['max_abs_dZ'] > 0
    assert r['prints_tested'] > 0 and r['sessions_in_range'] == 10


def test_scorecard_on_synthetic_results_reports_every_section():
    days = sc.weekdays('2026-11-02', '2026-11-27')
    res = {'L1': {'status': 'fresh', '_series': [pt(d, 0.1, raw_band='Normal', band='Normal', groups_stressed=[],
                                                      legs_z={'E1': 0.0}, legs_z_raw={'E1': 0.0},
                                                      factor_returns={'QQQ': 0.0, 'SMH': 0.0}) for d in days]},
           'L2': {'status': 'fresh', '_series': [pt(d, 2.0, scored=False, raw_band='Severe', band='Watch',
                                                      groups_stressed=['GA']) for d in days]},
           'L3': {'status': 'carried', '_series': [pt('2026-10-31', 0.5, weight_factor=0.6)]},
           'L4': {'status': 'insufficient_live_weight', '_series': [pt('2026-11-12', None, status='insufficient_live_weight')]}}
    out = sc.scorecard(days[0], days[-1], res, METHOD)
    assert out['layers']['L1']['coverage']['share_live'] == 1.0
    assert out['layers']['L2']['single_group']['would_light_with_one_group'] == len(days)
    assert out['checks']['single_group_violations'] == 0
    assert out['layers']['L3']['coverage']['share_fresh'] == 0 and out['layers']['L3']['coverage']['share_live'] == 1.0
    assert out['composites']['senior']['share_published'] == 0.0            # 0.5 x 0.6 live of 1.0: below 70%
    assert out['composites']['junior']['share_published'] == 1.0            # L2 unscored: L1 alone is the target
    assert out['senior_rates']['status'] == 'insufficient_data' and out['l2_leave_one_out']['status']
    assert 'sd_change_Z' in out['layers']['L1']['movement'] and 'not scored' in out['note'] + out['layers']['L1']['movement']['note']
