"""Composites in stack/build.py on synthetic layer series: each layer's latest value available on or before the date,
its status passed through (fresh, carried within its staleness rule, stale), the 70% live rule on those weights, the L2
gate per date, bands moved only by fresh values, and the date check. No network, no data/."""
import pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import build, config  # noqa: E402

METHOD, _ = config.load()
JUNIOR, SENIOR = METHOD['composites']['junior'], METHOD['composites']['senior']


def pt(session, z, status='fresh', known=None, **kw):
    return dict({'session': session, 'known': known or session, 'Z': z, 'status': status}, **kw)


def results(**series):
    return {k: {'_series': v, 'scored': (v[-1].get('scored') if v else None)} for k, v in series.items()}


def at(name, res, D):
    comp = JUNIOR if name == 'junior' else SENIOR
    return build._composite(name, comp, res, METHOD, D)


# --- junior ----------------------------------------------------------------------------------------------------------

def test_l1_residual_for_yesterday_is_fresh_on_the_day_it_becomes_available():
    # L1's residual for 24 Nov needs FRED data for 24 Nov, available 25 Nov; L2's 25 Nov session is known on 25 Nov
    res = results(L1=[pt('2026-11-23', 0.5, known='2026-11-24'), pt('2026-11-24', 1.0, known='2026-11-25')],
                  L2=[pt('2026-11-24', 2.0, scored=True), pt('2026-11-25', 2.0, scored=True)])
    c = at('junior', res, '2026-11-25')
    assert c['status'] == 'fresh' and c['live_share'] == 1.0
    assert c['parts']['L1'] == {'status': 'fresh', 'z': 1.0, 'point': '2026-11-24', 'available': '2026-11-25',
                                'weight_factor': 1.0}
    assert c['Z'] == pytest.approx(0.6 * 1.0 + 0.4 * 2.0)


def test_a_layer_that_ends_a_day_earlier_is_carried_not_dropped():
    res = results(L1=[pt('2026-11-23', 1.0, known='2026-11-24')],
                  L2=[pt('2026-11-24', 2.0, scored=True), pt('2026-11-25', 2.0, scored=True)])
    c = at('junior', res, '2026-11-25')
    assert c['parts']['L1']['status'] == 'carried' and c['parts']['L2']['status'] == 'fresh'
    assert c['status'] == 'fresh' and c['live_share'] == 1.0 and c['Z'] == pytest.approx(1.4)


def test_a_junior_value_older_than_its_carry_limit_is_stale_and_not_live():
    days = METHOD['engine']['junior_carry_days']
    res = results(L1=[pt('2026-11-16', 1.0, known='2026-11-17')],
                  L2=[pt('2026-11-25', 2.0, scored=True)])
    c = at('junior', res, '2026-11-25')
    assert days < 8 and c['parts']['L1']['status'] == 'stale' and 'carry' in c['parts']['L1']['reason']
    assert c['status'] == 'insufficient_live_weight' and c['live_share'] == pytest.approx(0.4) and c['Z'] is None
    assert c['band'] is None and 'needs 70%' in c['reason']


def test_l2_weight_is_zero_on_dates_before_its_gate_and_counts_after():
    res = results(L1=[pt('2026-11-24', 1.0), pt('2026-11-25', 1.0)],
                  L2=[pt('2026-11-24', 3.0, scored=False), pt('2026-11-25', 3.0, scored=True)])
    dates, comps, _ = build.composite_series('junior', JUNIOR, res, METHOD, '2026-11-25')
    assert dates == ['2026-11-24', '2026-11-25']
    assert comps[0]['weights_effective']['L2'] == 0.0 and comps[0]['Z'] == pytest.approx(1.0)
    assert comps[1]['weights_effective']['L2'] == 0.4 and comps[1]['Z'] == pytest.approx(0.6 + 1.2)


def test_values_not_yet_known_on_a_date_are_never_used():
    res = results(L1=[pt('2026-11-24', 1.0, known='2026-11-25')], L2=[pt('2026-11-24', 2.0, scored=True)])
    c = at('junior', res, '2026-11-24')
    assert c['parts']['L1']['status'] == 'missing' and c['parts']['L1']['z'] is None


# --- senior ----------------------------------------------------------------------------------------------------------

def test_senior_on_a_date_only_one_layer_updates_carries_the_other_with_its_weight_factor():
    res = results(L3=[pt('2026-10-31', 1.0, weight_factor=0.8)], L4=[pt('2026-11-12', -1.0)])
    c = at('senior', res, '2026-11-12')
    assert c['parts']['L3']['status'] == 'carried' and c['parts']['L4']['status'] == 'fresh'
    assert c['live_share'] == pytest.approx((0.5 * 0.8 + 0.5) / 1.0) and c['status'] == 'fresh'
    assert c['Z'] == pytest.approx((0.4 * 1.0 + 0.5 * -1.0) / 0.9, abs=1e-3)  # published Z is rounded to 3 places


def test_a_stale_or_unpublished_senior_layer_is_not_live():
    res = results(L3=[pt('2026-10-31', 1.0, status='stale', weight_factor=0.0)],
                  L4=[pt('2026-11-12', None, status='insufficient_live_weight')])
    c = at('senior', res, '2026-11-25')
    assert c['parts']['L3']['status'] == 'stale' and c['parts']['L4']['status'] == 'insufficient_live_weight'
    assert c['status'] == 'insufficient_live_weight' and c['live_weight'] == 0


def test_a_low_weight_factor_alone_can_drop_the_composite_below_70_percent():
    res = results(L3=[pt('2026-10-31', 1.0, weight_factor=0.3)], L4=[pt('2026-11-12', 1.0)])
    c = at('senior', res, '2026-11-12')
    assert c['live_share'] == pytest.approx(0.65) and c['status'] == 'insufficient_live_weight'


def test_carried_values_never_move_a_band():
    # a hot L4 release lifts the senior band once (senior: 1 release); later dates on which nothing is new hold it
    res = results(L3=[pt('2026-09-30', 0.0), pt('2026-10-31', 0.0, status='carried'),
                      pt('2026-11-30', 0.0, status='carried')],
                  L4=[pt('2026-10-15', 3.0)])
    dates, comps, _ = build.composite_series('senior', SENIOR, res, METHOD, '2026-11-30')
    by = dict(zip(dates, comps))
    assert by['2026-10-15']['status'] == 'fresh' and by['2026-10-15']['band_moved'] in ('up', 'init')
    assert all(by[d]['status'] == 'carried' and by[d]['band_moved'] is None for d in ('2026-10-31', '2026-11-30'))
    assert by['2026-11-30']['band'] == by['2026-10-15']['band']


# --- the date check and the weekly number ----------------------------------------------------------------------------

def test_date_check_flags_available_after_build_and_as_of_after_available():
    ok = build.date_check({'L3': {'as_of': '2026-09-30', 'available': '2026-10-27'}}, '2026-11-25')
    assert ok['ok']
    bad = build.date_check({'L3': {'as_of': '2026-10-31', 'available': '2026-10-27'},
                            'L4': {'as_of': '2026-09-30', 'available': '2026-11-26'}}, '2026-11-25')
    assert not bad['ok'] and len(bad['problems']) == 2


def test_weekly_averages_the_junior_fresh_days_of_the_week():
    res = results(L1=[pt('2026-11-23', 1.0), pt('2026-11-24', 2.0), pt('2026-11-25', 3.0)],
                  L2=[pt('2026-11-25', 0.0, scored=False)],
                  L3=[pt('2026-10-31', 1.0)], L4=[pt('2026-11-12', 1.0)])
    w = build._weekly(METHOD, res, '2026-11-25')
    assert w['label'] == 'optional, not adopted' and w['junior_fresh_days'] == {'L1': 3, 'L2': 1}
    assert w['weights_effective']['L2'] == 0.0
    assert w['Z'] == pytest.approx((0.2 * 2.0 + 0.3 * 1.0 + 0.3 * 1.0) / 0.8, abs=1e-3)
