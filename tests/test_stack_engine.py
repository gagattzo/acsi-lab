"""Shared engine of the Stack index (stack/engine.py). Synthetic numbers only: no network, no data/."""
import math, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import engine as E  # noqa: E402


# --- robust z and anchors --------------------------------------------------------------------------------------------

def test_robust_z_uses_median_and_mad_of_previous_obs():
    hist = list(range(11))  # median 5, MAD 3
    r = E.robust_z(14, hist, window=252, min_history=5, floor=0.1)
    assert r['status'] == 'ok' and r['median'] == 5
    assert r['z'] == pytest.approx(9 / (1.4826 * 3)) and not r['floored']


def test_robust_z_window_keeps_only_the_last_obs():
    hist = [1000.0] * 50 + [0.0, 1.0, 2.0]
    r = E.robust_z(2.0, hist, window=3, min_history=3, floor=0.1)
    assert r['median'] == 1.0 and r['n'] == 3


def test_robust_z_needs_min_history():
    r = E.robust_z(1.0, [0.0] * 9, window=252, min_history=10, floor=1)
    assert r['status'] == 'insufficient_history' and r['z'] is None and r['n'] == 9


def test_robust_z_floor_sets_the_scale_when_mad_is_zero():
    r = E.robust_z(3.0, [1.0] * 30, window=252, min_history=10, floor=0.5)
    assert r['floored'] and r['scale'] == 0.5 and r['z'] == pytest.approx(4.0)


def test_robust_z_rejects_a_non_positive_floor():
    with pytest.raises(ValueError):
        E.robust_z(1.0, [0.0] * 30, floor=0)


def test_anchor_z_sign_makes_higher_mean_more_stress():
    assert E.anchor_z(0.91, 0.97, 0.03, higher_is_stress=False) == pytest.approx(2.0)  # METHODOLOGIES example
    assert E.anchor_z(1.30, 1.00, 0.10) == pytest.approx(3.0)
    assert E.anchor_z(None, 1, 1) is None
    with pytest.raises(ValueError):
        E.anchor_z(1, 1, 0)


# --- logistic and the published integer -----------------------------------------------------------------------------

def test_logistic_is_centred_and_unclipped():
    assert E.logistic(0, 2.0) == 50.0
    assert E.logistic(2.0, 2.0) == pytest.approx(100 / (1 + math.exp(-1)))
    assert E.logistic(-3, 2) + E.logistic(3, 2) == pytest.approx(100)
    assert 0 <= E.logistic(-2000, 2) < 1e-6 and 100 - 1e-6 < E.logistic(2000, 2) <= 100.0  # no overflow
    assert E.published(E.logistic(-2000, 2)) == 1 and E.published(E.logistic(2000, 2)) == 99


def test_published_integer_is_held_inside_1_to_99():
    assert E.published(E.logistic(-40, 2)) == 1
    assert E.published(E.logistic(40, 2)) == 99
    assert E.published(99.6) == 99 and E.published(0.2) == 1
    assert E.published(50.5) == 51 and E.published(64.49) == 64
    assert E.published(None) is None


# --- bands and hysteresis --------------------------------------------------------------------------------------------

def fresh(lv, **kw):
    return dict(level=lv, status='fresh', **kw)


def carried(lv):
    return {'level': lv, 'status': 'carried'}


def bands(obs, mode='junior'):
    return [r['band'] for r in E.replay_bands(obs, mode)]


def test_raw_band_cutpoints():
    assert [E.BANDS[E.raw_band(x)] for x in (0, 34.99, 35, 49.9, 50, 64.9, 65, 79.9, 80, 99)] == [
        'Calm', 'Calm', 'Normal', 'Normal', 'Watch', 'Watch', 'Stressed', 'Stressed', 'Severe', 'Severe']


def test_first_fresh_value_sets_the_band_and_carried_values_before_it_set_nothing():
    assert bands([carried(90), fresh(40)]) == [None, 'Normal']


def test_junior_needs_two_consecutive_fresh_closes_to_move_up():
    assert bands([fresh(40), fresh(70), fresh(40), fresh(70), fresh(70)]) == ['Normal', 'Normal', 'Normal', 'Normal', 'Stressed']


def test_junior_moves_up_to_the_lowest_band_both_closes_reached():
    assert bands([fresh(40), fresh(85), fresh(55)]) == ['Normal', 'Normal', 'Watch']


def test_senior_moves_up_on_one_new_release():
    assert bands([fresh(40), fresh(85)], mode='senior') == ['Normal', 'Severe']


def test_carried_values_cannot_trigger_a_band():
    # one fresh close above, then the same level carried: no second qualifying close
    assert bands([fresh(40), fresh(70), carried(70), carried(70)]) == ['Normal'] * 4
    # a carried day breaks the run of consecutive closes
    assert bands([fresh(40), fresh(70), carried(70), fresh(70)]) == ['Normal'] * 4
    # senior: a carried value is not a new release
    assert bands([fresh(40), carried(90), {'level': 90, 'status': 'stale'}], mode='senior') == ['Normal'] * 3


def test_carried_values_cannot_clear_a_band():
    assert bands([fresh(70), carried(10), {'level': 10, 'status': 'stale'}, {'level': None, 'status': 'missing'}]) == ['Stressed'] * 4
    assert bands([fresh(70), carried(10)], mode='senior') == ['Stressed'] * 2


def test_moving_down_needs_four_points_below_the_cut():
    # Stressed starts at 65: 61.5 (3.5 below) holds, 61.0 (4 below) moves to Watch
    assert bands([fresh(70), fresh(61.5)]) == ['Stressed', 'Stressed']
    assert bands([fresh(70), fresh(61.0)]) == ['Stressed', 'Watch']
    # 47 is in Normal but only 3 below the Watch cut, so it stops at Watch; 45.9 crosses both cuts by the margin
    assert bands([fresh(70), fresh(47)]) == ['Stressed', 'Watch']
    assert bands([fresh(70), fresh(45.9)]) == ['Stressed', 'Normal']
    # one fresh close is enough going down, for junior and senior alike
    assert bands([fresh(85), fresh(20)], mode='senior') == ['Severe', 'Calm']


def test_cap_holds_the_state_and_says_why():
    obs = [fresh(40), fresh(85, cap='Watch', cap_reason='one group'), fresh(85, cap='Watch', cap_reason='one group'), fresh(85), fresh(85)]
    r = E.replay_bands(obs)
    assert [x['band'] for x in r] == ['Normal', 'Normal', 'Watch', 'Watch', 'Severe']
    assert r[1]['cap_reason'] == 'one group' and r[2]['cap_reason'] == 'one group' and 'cap_reason' not in r[3]
    # a state already above the cap is pulled down to it on a fresh observation
    r = E.replay_bands([fresh(85), fresh(85, cap='Watch', cap_reason='one group')], mode='senior')
    assert r[-1]['band'] == 'Watch' and r[-1]['moved'] == 'capped'


def test_replay_is_deterministic():
    obs = [fresh(x) for x in (40, 66, 70, 62, 59, 81, 82, 30)]
    assert E.replay_bands(obs) == E.replay_bands(list(obs))


# --- staleness, composites -------------------------------------------------------------------------------------------

def test_staleness_weight_full_to_120_then_linear_to_zero_at_180():
    assert [E.staleness_weight(d) for d in (0, 120, 150, 165, 180, 400)] == [1.0, 1.0, 0.5, 0.25, 0.0, 0.0]
    assert E.staleness_weight(None) == 0.0


def test_composite_needs_70_percent_of_target_weight_live():
    w = {'L1': 0.6, 'L2': 0.4}
    only_l2 = E.composite({'L1': {'z': None, 'status': 'not_implemented'}, 'L2': {'z': 1.0, 'status': 'fresh'}}, w)
    assert only_l2['status'] == 'insufficient_live_weight' and only_l2['Z'] is None and only_l2['live_share'] == pytest.approx(0.4)
    only_l1 = E.composite({'L1': {'z': 1.0, 'status': 'fresh'}, 'L2': {'z': None, 'status': 'ineligible'}}, w)
    assert only_l1['status'] == 'insufficient_live_weight'  # 60% < 70%
    both = E.composite({'L1': {'z': 1.0, 'status': 'fresh'}, 'L2': {'z': 2.0, 'status': 'fresh'}}, w)
    assert both['Z'] == pytest.approx(1.4) and both['live_share'] == 1.0 and both['published'] == E.published(E.logistic(1.4))


def test_composite_exactly_70_percent_is_published():
    r = E.composite({'a': {'z': 1.0, 'status': 'fresh'}, 'b': {'z': None, 'status': 'missing'}}, {'a': 0.7, 'b': 0.3})
    assert r['status'] == 'fresh' and r['Z'] == pytest.approx(1.0)


def test_zero_weight_part_is_shown_not_scored():
    r = E.composite({'L1': {'z': 1.0, 'status': 'fresh'}, 'L2': {'z': 9.0, 'status': 'fresh'}}, {'L1': 0.6, 'L2': 0.0})
    assert r['Z'] == pytest.approx(1.0) and r['target_weight'] == 0.6 and 'L2' not in r['weights_used']


def test_staleness_factor_reduces_the_live_weight_not_the_value():
    parts = {'L3': {'z': 2.0, 'status': 'carried', 'weight_factor': 0.5}, 'L4': {'z': 1.0, 'status': 'fresh'}}
    r = E.composite(parts, {'L3': 0.5, 'L4': 0.5})
    assert r['live_share'] == pytest.approx(0.75) and r['Z'] == pytest.approx((0.25 * 2 + 0.5 * 1) / 0.75)


def test_composite_of_carried_parts_is_carried():
    r = E.composite({'a': {'z': 1.0, 'status': 'carried'}}, {'a': 1.0})
    assert r['status'] == 'carried'


# --- the 2-group rule, the group cap, Depth --------------------------------------------------------------------------

def test_one_group_cannot_light_a_layer():
    cap, why, hot = E.two_group_cap({'CoreWeave': 95.0, 'Oracle': 55.0, 'Meta': None})
    assert cap == 'Watch' and hot == ['CoreWeave'] and 'CoreWeave' in why


def test_two_groups_at_stressed_lift_the_cap():
    cap, why, hot = E.two_group_cap({'CoreWeave': 95.0, 'Oracle': 66.0})
    assert cap is None and why is None and hot == ['CoreWeave', 'Oracle']


def test_group_cap_redistributes_the_excess_pro_rata():
    r = E.cap_weights({'A': 5, 'B': 2, 'C': 1, 'D': 1, 'E': 0.5, 'F': 0.5})
    w = r['weights']
    assert not r['cap_infeasible'] and sum(w.values()) == pytest.approx(1.0)
    assert max(w.values()) <= 0.2 + 1e-12 and r['capped'] == ['A', 'B']
    assert w['C'] == pytest.approx(0.2) and w['E'] == pytest.approx(0.1)   # pro rata to the uncapped base weights
    assert r['uncapped']['A'] == pytest.approx(0.5)                        # attribution keeps the uncapped view


def test_group_cap_leaves_small_groups_alone():
    r = E.cap_weights({g: 1 for g in 'ABCDEFGHIJ'})
    assert all(v == pytest.approx(0.1) for v in r['weights'].values()) and r['capped'] == []


def test_group_cap_with_exactly_five_groups_is_feasible():
    r = E.cap_weights({'A': 3, 'B': 1, 'C': 1, 'D': 1, 'E': 1})
    assert not r['cap_infeasible'] and all(v == pytest.approx(0.2) for v in r['weights'].values())


def test_group_cap_infeasible_below_five_groups_falls_back_to_equal_weights():
    r = E.cap_weights({'CoreWeave': 10, 'Oracle': 1, 'Meta': 1, 'Zero': 0})
    assert r['cap_infeasible'] and set(r['weights']) == {'CoreWeave', 'Oracle', 'Meta'}
    assert all(v == pytest.approx(1 / 3) for v in r['weights'].values())
    assert r['uncapped']['CoreWeave'] == pytest.approx(10 / 12)


def test_depth_counts_layers_at_stressed_or_worse():
    d = E.depth({'L1': 'Watch', 'L2': 'Stressed', 'L3': 'Severe', 'L4': None})
    assert d['depth'] == 2 and d['layers_stressed_or_worse'] == ['L2', 'L3'] and d['layers_without_band'] == ['L4']
    assert E.depth({'L1': 'Calm', 'L2': 'Normal'})['depth'] == 0


def test_weekly_stack_averages_junior_layers_and_is_labelled():
    r = E.weekly_stack({'L1': [1.0, 3.0], 'L2': []}, {'L3': {'z': 0.0, 'status': 'fresh'}, 'L4': {'z': 1.0, 'status': 'carried'}},
                       {'L1': 0.2, 'L2': 0.2, 'L3': 0.3, 'L4': 0.3})
    assert r['label'] == 'optional, not adopted'
    assert r['live_share'] == pytest.approx(0.8) and r['Z'] == pytest.approx((0.2 * 2 + 0.3 * 0 + 0.3 * 1) / 0.8)
