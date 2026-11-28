"""The side of the coupon for a two-reading unsigned upfront comes only from a dated public mark (attested_side).

The mark never sets the level, never decides near the coupon, never comes from the future and lapses after
max_age_days. These cases are synthetic; research/findings/2026-11-28-coreweave-public-marks.md has the evidence."""
import copy
import json

import pytest

from test_dtcc import DAY, NO_MARKS, row, CRWV, dtcc


def marks(*items, **params):
    doc = copy.deepcopy(NO_MARKS)
    doc.update(params)
    doc['marks'] = [{'entity': e, 'date': d, 'reported_bp': bp, 'reported_by': 'test', 'url': 'https://example.org/'}
                    for e, d, bp in items]
    return dtcc.validate_attestations(doc)


WIDE = marks(('CRWV', '2026-11-06', 1155.0))
UP = dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': '2309720'})  # 23.1% on a 5% coupon


def decode(rows, att, day=DAY):
    quality = {}
    levels, trades = dtcc.parse_day(rows, day, {}, quality=quality, attestations=att)
    return levels.get('CRWV'), trades, quality['CRWV']


def readings(amount, coupon=.05, day=DAY):
    ex, exp = dtcc.ts(f'{day}T15:00:00Z'), dtcc.ts('2031-12-20T00:00:00Z')
    step = ex + dtcc.DAY
    T, accr = (exp - step) / (365.25 * dtcc.DAY), (step - dtcc.last_imm(ex)) / (360 * dtcc.DAY)
    return [dtcc.upfront_solution(s * amount, coupon, T, accr)['spread'] for s in (1, -1)]


def test_a_wide_recent_mark_keeps_the_reading_above_the_coupon():
    hi, lo = readings(.230972)
    lv, trades, q = decode([row(**UP)], WIDE)
    assert lv['official_bp'] == round(hi * 10000, 1) and lv['n_sign_attested'] == 1 and lv['n_ambiguous'] == 0
    assert trades[0]['sign_by'] == 'attested_side' and trades[0]['sign_mark_date'] == '2026-11-06'
    assert q['sign_attested'] == 1 and 'ambiguous_upfront_sign' not in q['reasons']
    assert lo < .005 < .05 < hi


def test_a_tight_recent_mark_keeps_the_reading_below_the_coupon():
    hi, lo = readings(.230972)
    lv, _, _ = decode([row(**UP)], marks(('CRWV', '2026-11-20', 200.0)))
    assert lv['official_bp'] == round(lo * 10000, 1)


def test_the_mark_decides_the_side_but_never_the_level():
    a, _, _ = decode([row(**UP)], marks(('CRWV', '2026-11-20', 800.0)))
    b, _, _ = decode([row(**UP)], marks(('CRWV', '2026-11-20', 5000.0)))
    assert a['official_bp'] == b['official_bp']


@pytest.mark.parametrize('att,reason', [
    (NO_MARKS, 'no_public_mark'),
    (marks(('CRWV', '2026-11-02', 1155.0)), 'public_mark_stale'),        # 22 days before the trade
    (marks(('CRWV', '2026-11-20', 700.0)), 'public_mark_near_coupon'),   # under 1.5 x 500 bp
    (marks(('CRWV', '2026-11-20', 340.0)), 'public_mark_near_coupon'),   # over 500 / 1.5 bp
    (marks(('CRWV', '2026-11-25', 1314.0)), 'no_public_mark'),           # a later mark never decides an earlier trade
    (marks(('CRWV', '2026-11-06', 1155.0), ('CRWV', '2026-11-20', 600.0)), 'public_mark_near_coupon'),  # latest wins
    (marks(('ORCL', '2026-11-20', 1155.0)), 'no_public_mark'),           # another name's mark
])
def test_no_decision_without_a_recent_decisive_mark_of_the_same_name(att, reason):
    lv, trades, q = decode([row(**UP)], att)
    assert lv is None and trades == []
    assert q['ambiguous'] == 1 and q['sign_attested'] == 0
    assert {'ambiguous_upfront_sign', reason, 'no_accepted_price'} <= set(q['reasons'])


def test_a_mark_exactly_max_age_days_old_still_decides():
    lv, _, _ = decode([row(**UP)], marks(('CRWV', '2026-11-03', 1155.0)))  # 21 days
    assert lv is not None and lv['n_sign_attested'] == 1


def test_readings_near_the_coupon_stay_ambiguous_even_with_a_wide_mark():
    amount = .02  # 2% upfront on a 5% coupon: readings about 550 and 455 bp
    hi, lo = readings(amount)
    assert hi < 2 * lo
    up = dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': str(int(amount * 1e7))})
    lv, _, q = decode([row(**up)], WIDE)
    assert lv is None and 'readings_near_coupon' in q['reasons']


def test_one_feasible_root_and_quotes_are_untouched_by_marks():
    one = dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': '3000000'})
    quoted = dict(CRWV, **{'Dissemination Identifier': '3', 'Spread-Leg 1': '0.1200'})
    for att in (NO_MARKS, WIDE, marks(('CRWV', '2026-11-20', 200.0))):
        lv, trades, _ = decode([row(**one), row(**quoted)], att)
        assert lv['official_trades'] == 2 and lv['n_sign_attested'] == 0
        assert sorted(t['sign_by'] if t['kind'] == 'upfront' else 'quoted' for t in trades) == ['quoted', 'trade']


def test_the_published_marks_file_is_valid_and_has_one_mark_per_name_and_day():
    doc = dtcc.load_attestations()
    assert doc['max_age_days'] == 21 and doc['decisive_factor'] == 1.5 and doc['min_reading_ratio'] == 2.0
    keys = [(m['entity'], m['date']) for m in doc['marks']]
    assert len(keys) == len(set(keys)) and keys == sorted(keys)


@pytest.mark.parametrize('bad', [
    {'schema': 'other'},
    {'max_age_days': 0}, {'decisive_factor': 1}, {'min_reading_ratio': True},
    {'marks': [{'entity': 'XXXX', 'date': '2026-11-20', 'reported_bp': 1, 'reported_by': 'x', 'url': 'https://e/'}]},
    {'marks': [{'entity': 'CRWV', 'date': '2026-11-31', 'reported_bp': 1, 'reported_by': 'x', 'url': 'https://e/'}]},
    {'marks': [{'entity': 'CRWV', 'date': '2026-11-20', 'reported_bp': 0, 'reported_by': 'x', 'url': 'https://e/'}]},
    {'marks': [{'entity': 'CRWV', 'date': '2026-11-20', 'reported_bp': 1, 'reported_by': 'x', 'url': 'http://e/'}]},
    {'marks': [{'entity': 'CRWV', 'date': '2026-11-20', 'reported_bp': 1, 'reported_by': 'x', 'url': 'https://e/'}] * 2},
])
def test_a_malformed_marks_file_is_rejected(bad):
    doc = dict(json.loads(dtcc.SIGN_ATTESTATIONS_PATH.read_text()), **bad)
    with pytest.raises(ValueError):
        dtcc.validate_attestations(doc)
