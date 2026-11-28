"""Synthetic pricing integrity cases; no market-fit or performance thresholds."""
import math

import pytest

from test_dtcc import DAY, row, CRWV, dtcc


def classify(rows, previous=None):
    quality = {}
    levels, trades = dtcc.parse_day(rows, DAY, previous or {}, quality=quality)
    return levels, trades, quality


@pytest.mark.parametrize('hazard', [0, 0.001, 0.02, 0.3, 3])
def test_upfront_inversion_recovers_a_supported_root(hazard):
    c, t, accr = .05, 5.06, .02
    p, v = dtcc.legs(hazard, t)
    solved = dtcc.upfront_solution(p - c * v - c * accr, c, t, accr)
    assert solved['error'] is None
    assert solved['spread'] == pytest.approx(p / v, abs=1e-10)


def test_solver_does_not_saturate_at_either_domain_boundary():
    assert dtcc.upfront_solution(-.5, .05, 5.06, .02)['error'] == 'no_nonnegative_hazard_root'
    assert dtcc.upfront_solution(.7, .05, 5.06, .02)['error'] == 'outside_model_domain'
    assert dtcc.spread_from_upfront(.7, .05, 5.06, .02) is None


@pytest.mark.parametrize('args', [(math.nan, .05, 5, 0), (.1, 0, 5, 0), (.1, .05, 0, 0), (.1, .05, 5, -.1)])
def test_solver_validates_inputs(args):
    assert dtcc.upfront_solution(*args)['error'] == 'invalid_model_input'


def test_sub_100bp_candidate_is_feasible_and_cannot_be_discarded():
    hi = dtcc.upfront_solution(.2, .05, 5.06, .02)
    lo = dtcc.upfront_solution(-.2, .05, 5.06, .02)
    assert 0 < lo['spread'] < .01
    for reference in [None, .005, .12]:
        assert dtcc.resolve_upfront(hi, lo, .05, reference, .2) == (None, 'ambiguous')


def test_numerical_domain_failure_never_proves_the_other_sign():
    root = {'spread': .05, 'error': None}
    assert dtcc.resolve_upfront(root, {'spread': None, 'error': 'outside_model_domain'}) == (None, 'model_domain_error')
    assert dtcc.resolve_upfront(root, None) == (None, 'model_domain_error')
    assert dtcc.resolve_upfront(root, {'spread': None, 'error': 'no_nonnegative_hazard_root'}) == (.05, 'trade')


def test_ambiguous_day_has_diagnostics_but_no_price_or_canonical_trade():
    rows = [row(**dict(CRWV, **{'Other payment amount': '2000000'}))]
    levels, trades, quality = classify(rows, {'CRWV': 1300})
    assert 'CRWV' not in levels and trades == []
    q = quality['CRWV']
    assert q['accepted_trades'] == 0 and q['ambiguous'] == 1
    assert q['candidate_interval_bp']['low'] < 100 < q['candidate_interval_bp']['high']
    assert q['reasons'] == ['ambiguous_upfront_sign', 'no_accepted_price']


def test_quote_does_not_resolve_another_trades_upfront_sign():
    up = row(**dict(CRWV, **{'Other payment amount': '2000000'}))
    quote = row(**dict(CRWV, **{'Dissemination Identifier': '2', 'Spread-Leg 1': '.005'}))
    levels, trades, quality = classify([up, quote])
    assert levels['CRWV']['official_bp'] == 50 and levels['CRWV']['official_trades'] == 1
    assert levels['CRWV']['n_sign_from_reference'] == 0
    assert len(trades) == 1 and quality['CRWV']['ambiguous'] == 1


def test_zero_upfront_and_unique_feasible_sign_can_price():
    for amount in ['0', '3000000']:
        levels, trades, quality = classify([row(**dict(CRWV, **{'Other payment amount': amount}))])
        assert levels['CRWV']['official_trades'] == 1 and len(trades) == 1
        assert quality['CRWV']['ambiguous'] == 0


def test_outside_domain_day_is_counted_even_without_a_level():
    levels, trades, quality = classify([row(**dict(CRWV, **{'Other payment amount': '7000000'}))])
    assert levels == {} and trades == []
    assert quality['CRWV']['model_domain_errors'] == 1
    assert 'outside_model_domain' in quality['CRWV']['reasons']


def test_quote_under_10bp_is_a_reporting_error_with_no_ceiling():
    # research/findings/2026-11-28-decoder-choices-evidence.md: in a year, 34 eligible quotes fell under 10 bp, all
    # 0.81-9.0 bp on high-yield names (CoreWeave printed at 1.2 bp and 4.5 bp). None was above 5,000 bp.
    assert dtcc.quote_status('.0001') == (None, 'quote_below_10bp')
    assert dtcc.quoted_spread('.001') == .001 and dtcc.quoted_spread('10') == .001        # 10 bp itself is kept
    assert dtcc.quoted_spread('365') == .0365 and dtcc.quoted_spread('.45') == .45
    assert dtcc.quoted_spread('60000') == 6.0                                                 # no ceiling
    levels, _, quality = classify([row(**{'Spread-Leg 1': '.00045'})])
    assert levels == {} and quality['ORCL']['invalid_quotes'] == 1 and 'quote_below_10bp' in quality['ORCL']['reasons']
    for value in ['2', 'NaN', 'Infinity', '-1']:
        levels, _, quality = classify([row(**{'Spread-Leg 1': value, 'Other payment amount': '1030000'})])
        assert levels == {} and quality['ORCL']['invalid_quotes'] == 1
        assert 'invalid_or_unresolved_quote_units' in quality['ORCL']['reasons']


def test_quote_floor_never_rules_out_an_upfront_low_root():
    # The 10 bp rule judges reported quotes only. An unsigned upfront whose low root sits under 10 bp keeps both roots
    # and stays ambiguous, with or without a rejected quote that day.
    ex, exp = dtcc.ts(f'{DAY}T15:00:00Z'), dtcc.ts('2031-12-20T00:00:00Z')
    step = ex + dtcc.DAY
    T, accr = (exp - step) / (365.25 * dtcc.DAY), (step - dtcc.last_imm(ex)) / (360 * dtcc.DAY)
    amount = next(a for a in range(2_200_000, 2_500_000, 500)
                  if 0 < (dtcc.upfront_solution(-a / 1e7, .05, T, accr)['spread'] or 0) < dtcc.QUOTE_FLOOR)
    up = row(**dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': str(amount)}))
    junk = row(**dict(CRWV, **{'Dissemination Identifier': '3', 'Spread-Leg 1': '.00045'}))
    for rows in ([up], [up, junk]):
        levels, _, quality = classify(rows)
        assert 'CRWV' not in levels and quality['CRWV']['ambiguous'] == 1
        assert quality['CRWV']['candidate_interval_bp']['low'] < 10


@pytest.mark.parametrize('change', [{'Expiration Date': '2030-12-20'}, {'Fixed rate-Leg 1': '.03'}, {'Underlier ID-Leg 1': 'LX123456'}])
def test_ineligible_basket_match_cannot_quarantine_a_valid_quote(change):
    quote = row(**{'Spread-Leg 1': '.0208', 'Other payment amount': '197460'})
    unrelated = row(**dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': '197460', **change}))
    levels, _, quality = classify([quote, unrelated])
    assert levels['ORCL']['official_bp'] == 208
    assert quality['ORCL']['basket_legs'] == 0
