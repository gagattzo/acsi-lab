"""Tests for the scoring harness: the eligibility rule, edition values never painted into history, composition changes
never scored as moves, and the one-trade attack measured against what the input would otherwise have shown."""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'harness'))
import score  # noqa: E402

CAL = [f'2026-01-{d:02d}' for d in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16)]


def method(**cds):
    base = {'max_carry_days': 10}
    base.update(cds)
    return {'name': 't', 'inputs': [
        {'id': 'F1', 'side': 'financing', 'source': 'dtcc:CRWV', 'calm': 500, 'distress': 1000, **base},
        {'id': 'F2', 'side': 'financing', 'source': 'dtcc:ORCL', 'calm': 100, 'distress': 1000, **base},
        {'id': 'A1', 'side': 'asset', 'source': 'fixed', 'value': 3.10, 'as_of': '2026-01-14', 'calm': 3.56, 'distress': 2.06},
    ]}


def tape(crwv, orcl):
    return {'CRWV': {d: v for d, v in zip(CAL, crwv) if v}, 'ORCL': {d: v for d, v in zip(CAL, orcl) if v}}


def test_edition_value_is_never_painted_into_earlier_days():
    t = tape([[700]] * 10, [[300, 301, 302]] * 10)
    s = score.build(method(), t, CAL)
    before = [x for x in s if x['date'] < '2026-01-14']
    after = [x for x in s if x['date'] >= '2026-01-14']
    assert before and all(x['basis'] == 'financing' and x['G'] is None and x['flags']['A1'] == 'missing' for x in before)
    assert after and all(x['basis'] == 'gap' and x['flags']['A1'] == 'held_from_edition' for x in after)


def test_a_change_of_inputs_is_not_scored_as_a_move():
    t = tape([[700]] * 10, [[300, 301, 302]] * 10)
    s = score.build(method(), t, CAL)
    ch = score.changes(s)
    assert len(ch) == len(s) - 2  # the day the edition value starts to count is not a change
    assert all(abs(c) < 1e-9 for _, _, c in ch)


def test_one_print_today_sets_the_level_by_default():
    t = tape([[700]] * 9 + [[900]], [[300, 301, 302]] * 10)
    s = score.build(method(), t, CAL)
    assert s[-1]['levels']['F1'] == 900 and s[-1]['flags']['F1'] == 'same_day' and s[-1]['single']


def test_single_trade_attack_is_measured_against_the_carried_level():
    # Leave-one-out within the day says 0 when there is one trade; the real counterfactual is yesterday's level.
    t = tape([[700]] * 9 + [[900]], [[300, 301, 302]] * 10)
    s = score.build(method(), t, CAL)
    assert abs(s[-1]['influence'] - (40.0 / 2)) < 1e-6  # 700 -> 900 bp is 40 points on F1, half of it on F


def test_eligibility_rule_blocks_a_lone_print():
    m = method(window_sessions=5, min_prints=5, min_sessions_with_prints=3, max_days_since_print=2)
    t = tape([[700, 701]] * 5 + [None] * 4 + [[900]], [[300, 301, 302]] * 10)
    s = score.build(m, t, CAL)
    last = s[-1]
    assert last['flags']['F1'] == 'carried' and last['levels']['F1'] < 800


def test_eligibility_rule_needs_a_recent_print():
    m = method(window_sessions=5, min_prints=5, min_sessions_with_prints=3, max_days_since_print=2)
    t = tape([[700, 701, 702]] * 7 + [None] * 3, [[300, 301, 302]] * 10)
    s = score.build(m, t, CAL)
    assert s[-1]['flags']['F1'] == 'carried'


def test_median_of_daily_medians_is_not_moved_by_one_busy_day():
    m = method(window_sessions=5, min_prints=5, min_sessions_with_prints=3, level='median_of_daily_medians')
    t = tape([[700]] * 9 + [[900, 900, 900, 900, 900]], [[300, 301, 302]] * 10)
    s = score.build(m, t, CAL)
    assert s[-1]['levels']['F1'] == 700


def test_quoted_first_uses_quoted_trades_when_there_are_any(tmp_path):
    p = tmp_path / 't.csv'
    p.write_text('date,entity,dissemination_id,exec_ts,kind,spread_bp,coupon,coupon_normalised,upfront_pct,notional_capped\n'
                 '2026-01-05,ORCL,1,2026-01-05T15:00:00Z,quoted,300,,False,,False\n'
                 '2026-01-05,ORCL,2,2026-01-05T15:00:00Z,upfront,320,0.01,False,1.0,False\n'
                 '2026-01-06,ORCL,3,2026-01-06T15:00:00Z,upfront,330,0.01,False,1.0,False\n')
    by, cal = score.load_trades(p)
    assert by['ORCL']['2026-01-05'] == [300.0, 320.0] and by['ORCL|quoted_first']['2026-01-05'] == [300.0]
    assert by['ORCL|quoted_first']['2026-01-06'] == [330.0]


def test_method_checker_rejects_bad_files():
    import pytest
    bad = method(window_sessions=3, min_sessions_with_prints=4)
    with pytest.raises(SystemExit):
        score.check_method(bad)
    undated = method()
    del undated['inputs'][2]['as_of']
    with pytest.raises(SystemExit):
        score.check_method(undated)
    assert score.check_method(method(window_sessions=5, min_prints=5, level_sessions=2, max_days_since_print=2))


def test_multiple_regression_recovers_an_exact_fit():
    X = [(i, (i * 7) % 5) for i in range(20)]
    y = [2 * a - 3 * b + 1 for a, b in X]
    assert abs(score.r2_multi(y, X) - 1.0) < 1e-9
