#!/usr/bin/env python3
"""Public, synthetic counterexamples for a proposed AI credit stress reference.

No market data, dependencies, network requests, or production index changes.
These examples demonstrate arithmetic failure modes, not an empirical backtest
or executable manipulation strategy. All prices, spreads and weights are made up.
"""
import json
import math
import statistics


def mean(values):
    return statistics.fmean(values)


def run():
    # Four distressed bonds remain above both central order statistics.
    spreads = [200 + 10 * i for i in range(20)] + [1100, 1200, 1300, 1400]
    widened = spreads[:20] + [x + 100 for x in spreads[20:]]
    median_delta = statistics.median(widened) - statistics.median(spreads)
    mean_delta = mean(widened) - mean(spreads)
    assert median_delta == 0
    assert math.isclose(mean_delta, 100 * 4 / 24)

    # Same two-sided price, different customer trade-side composition.
    buys_first = [99.0] * 2 + [101.0] * 8
    sells_later = [99.0] * 8 + [101.0] * 2
    naive_change = statistics.median(sells_later) - statistics.median(buys_first)
    side_balanced_mid = (99.0 + 101.0) / 2
    assert naive_change == -2
    assert side_balanced_mid == 100

    # A daily full-session median can also miss a late, genuine repricing.
    early_and_late = [100.0] * 8 + [90.0] * 2
    assert statistics.median(early_and_late) == 100

    # Dropping a stressed component and renormalizing creates false relief.
    before = 0.4 * 1500 + 0.6 * 300
    survivor_only = 300.0
    assert before == 780

    # Holding a defaulted slot until the roll only postpones disappearance.
    default_slot_weight = 0.1
    roll_change = default_slot_weight * (300 - 3000)
    event_transition_change = default_slot_weight * (2000 - 4000)
    assert roll_change == -270
    assert event_transition_change == -200

    # No fresh bond observation, but the benchmark rate changes.
    stale_yield = 10.0
    yesterday_spread = (stale_yield - 4.0) * 100
    mixed_date_spread = (stale_yield - 3.5) * 100
    assert mixed_date_spread - yesterday_spread == 50

    # Fixed group weights preserve the effect of a genuine common shock.
    common_exposure_weight = 0.4  # Illustration, not a recommended calibration.
    assert common_exposure_weight * 100 == 40

    # Dynamic market-value weights give a deteriorating bond less influence.
    equal_par_start = 100 / (100 + 100)
    equal_par_after_price_drop = 50 / (50 + 100)
    assert equal_par_after_price_drop < equal_par_start

    return {
        'synthetic_only': True,
        'median_ignores_four_distressed_bonds': {
            'shock_bp_each': 100, 'median_change_bp': median_delta,
            'equal_bond_mean_change_bp': mean_delta},
        'customer_side_mix': {
            'unchanged_bid': 99, 'unchanged_ask': 101,
            'naive_median_price_change': naive_change,
            'side_balanced_mid_change': 0,
            'limitation': 'Both sides must be contemporaneous; this is not a production estimator.'},
        'late_repricing': {'early_price': 100, 'late_price': 90, 'full_day_median': 100},
        'default_or_missing_component_removal': {
            'before_bp': before, 'survivor_only_bp': survivor_only,
            'mechanical_change_bp': survivor_only - before,
            'note': 'Undefined post-default yield is not zero stress.'},
        'mixed_date_rate_artifact': {'apparent_widening_bp': 50, 'fresh_bond_trades': 0},
        'default_proxy_replacement': {'group_weight': default_slot_weight,
            'old_proxy_bp': 3000, 'replacement_bp': 300,
            'mechanical_index_change_bp': roll_change},
        'default_transition': {'group_weight': default_slot_weight,
            'pre_default_bp': 4000, 'prescribed_proxy_bp': 2000,
            'mechanical_index_change_bp': event_transition_change,
            'note': 'No recovery-to-spread formula or post-default price is endorsed.'},
        'fixed_common_exposure_weight': {'weight': common_exposure_weight,
            'group_shock_bp': 100, 'index_change_bp': 40},
        'dynamic_market_value_weight': {'before': equal_par_start,
            'after_price_halves': equal_par_after_price_drop},
        'scale_does_not_change_percentage_move': {'original_return': 800 / 600 - 1,
            'scaled_return': 80 / 60 - 1},
    }


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, allow_nan=False))
