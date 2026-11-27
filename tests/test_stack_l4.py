"""L4 fundamentals (stack/l4_fundamentals.py) on synthetic companyfacts and submissions payloads: no network, no data/."""
import copy, io, json, pathlib, sys
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import config, l4_fundamentals as L4  # noqa: E402

BASE = yaml.safe_load(open(ROOT / 'stack' / 'stack-v0.yaml'))
G = 'us-gaap:'


# --- fixture builders ------------------------------------------------------------------------------------------------

def companyfacts(rows, cik=1, name='Alpha Corp'):
    """rows: (tag, start or None, end, val, filed, accn, form) -> a companyfacts-shaped dict (USD units)."""
    facts = {}
    for tag, start, end, val, filed, accn, form in rows:
        tax, t = tag.split(':')
        x = {'end': end, 'val': val, 'accn': accn, 'form': form, 'filed': filed}
        if start:
            x['start'] = start
        facts.setdefault(tax, {}).setdefault(t, {'units': {'USD': []}})['units']['USD'].append(x)
    return {'cik': cik, 'entityName': name, 'facts': facts}


FILED = {1: ('05-05', '10-Q'), 2: ('08-05', '10-Q'), 3: ('11-05', '10-Q'), 4: ('02-20', '10-K')}
QEND = {1: '03-31', 2: '06-30', 3: '09-30', 4: '12-31'}


def filing(y, q):
    md, form = FILED[q]
    return (f'{y + 1}-{md}' if q == 4 else f'{y}-{md}'), f'acc-{y}-q{q}', form


def flows(tag, years, per_quarter):
    """Calendar fiscal years filed the usual way: 10-Qs carry only year-to-date durations (3M, 6M, 9M), the 10-K the
    full year; Q4 must be derived as FY - 9M."""
    rows = []
    for y in years:
        cum = 0
        for q in (1, 2, 3, 4):
            cum += per_quarter
            if q == 4 and y == years[-1]:
                break
            filed, acc, form = filing(y, q)
            rows.append((tag, f'{y}-01-01', f'{y}-{QEND[q]}', cum, filed, acc, form))
    return rows


def instants(tag, years, val, quarters=(1, 2, 3, 4)):
    rows = []
    for y in years:
        for q in quarters:
            if q == 4 and y == years[-1]:
                continue
            filed, acc, form = filing(y, q)
            v = val(y, q) if callable(val) else val
            if v is not None:
                rows.append((tag, None, f'{y}-{QEND[q]}', v, filed, acc, form))
    return rows


def alpha_rows(cash=None):
    yrs = [2024, 2025, 2026]
    cash = cash or {(2024, 4): 1100, (2025, 1): 1050, (2025, 2): 1020, (2025, 3): 1000, (2025, 4): 900,
                    (2026, 1): 800, (2026, 2): 700, (2026, 3): 600}
    rows = (flows(G + 'NetCashProvidedByUsedInOperatingActivities', yrs, 100)
            + flows(G + 'PaymentsToAcquirePropertyPlantAndEquipment', yrs, 300)
            + flows(G + 'Revenues', yrs, 200)
            + instants(G + 'CashAndCashEquivalentsAtCarryingValue', yrs, lambda y, q: cash.get((y, q)))
            + instants(G + 'LongTermDebt', yrs, 2000)
            + instants(G + 'LongTermDebtCurrent', yrs, 250)
            + instants(G + 'OperatingLeaseLiability', yrs, 100)
            + instants(G + 'LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths', yrs, 300, quarters=(4,))
            + instants(G + 'LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo', yrs, 200, quarters=(4,)))
    return rows


def submissions(events, name='Alpha Corp'):
    """events: (filed, form, items string, accession)."""
    cols = {k: [] for k in ('accessionNumber', 'filingDate', 'reportDate', 'form', 'items')}
    for filed, form, items, acc in events:
        cols['accessionNumber'].append(acc)
        cols['filingDate'].append(filed)
        cols['reportDate'].append(filed)
        cols['form'].append(form)
        cols['items'].append(items)
    return {'cik': '1', 'name': name, 'filings': {'recent': cols, 'files': []}}


def method(**l4):
    m = copy.deepcopy(BASE)
    m['layers']['L4'].update(ciks={'AAA': 1, 'BBB': 2}, issuer_groups={'GA': ['AAA'], 'GB': ['BBB']},
                             history_start='2026-01-01')
    m['layers']['L4'].update(l4)
    return m


P = method()['layers']['L4']


# --- point in time ---------------------------------------------------------------------------------------------------

def test_fact_filed_after_as_of_is_invisible():
    F = L4.Facts(companyfacts([(G + 'CashAndCashEquivalentsAtCarryingValue', None, '2026-03-31', 500, '2026-05-10', 'a1', '10-Q')]))
    tag = G + 'CashAndCashEquivalentsAtCarryingValue'
    assert F.instant(tag, '2026-03-31', '2026-05-09') is None
    assert F.instant(tag, '2026-03-31', '2026-05-10')['val'] == 500
    assert F.instant_ends([tag], '2026-05-09') == []


def test_restatement_applies_only_from_its_filing_date():
    tag = G + 'CashAndCashEquivalentsAtCarryingValue'
    F = L4.Facts(companyfacts([(tag, None, '2026-03-31', 100_000_000, '2026-05-10', 'orig', '10-Q'),
                               (tag, None, '2026-03-31', 150_000_000, '2026-08-10', 'restated', '10-Q/A')]))
    before = F.instant(tag, '2026-03-31', '2026-06-01')
    assert (before['val'], before['filed'], before['accn'], before['restated']) == (100_000_000, '2026-05-10', 'orig', False)
    after = F.instant(tag, '2026-03-31', '2026-09-01')
    assert (after['val'], after['filed'], after['restated']) == (150_000_000, '2026-08-10', True)
    assert (after['original_val'], after['original_filed']) == (100_000_000, '2026-05-10')


def test_repeated_comparative_keeps_the_earliest_filing_date():
    """A later 10-Q repeating the number (rounded to millions) is not new information and not a restatement."""
    tag = G + 'NetCashProvidedByUsedInOperatingActivities'
    F = L4.Facts(companyfacts([(tag, '2025-01-01', '2025-09-30', 1_499_051_000, '2025-11-13', 'q3-2025', '10-Q'),
                               (tag, '2025-01-01', '2025-09-30', 1_499_000_000, '2026-11-12', 'q3-2026', '10-Q')]))
    p = F.durations(tag, '2026-11-25')[('2025-01-01', '2025-09-30')]
    assert (p['val'], p['filed'], p['accn'], p['restated']) == (1_499_051_000, '2025-11-13', 'q3-2025', False)


def test_metric_ignores_a_filing_after_the_as_of_date():
    F = L4.Facts(companyfacts(alpha_rows()))
    end, v, det, _ = L4.latest_value(F, 'f2', '2026-11-04')  # the Q3 2026 10-Q is filed on 2026-11-05
    assert end == '2026-06-30' and det['filed'] <= '2026-11-04'
    end2, _, det2, _ = L4.latest_value(F, 'f2', '2026-11-05')
    assert end2 == '2026-09-30' and det2['filed'] == '2026-11-05'


# --- TTM -------------------------------------------------------------------------------------------------------------

def test_ttm_derives_quarters_from_year_to_date_and_q4_from_fy_minus_9m():
    F = L4.Facts(companyfacts(alpha_rows()))
    t = F.ttm([G + 'NetCashProvidedByUsedInOperatingActivities'], '2026-09-30', '2026-11-25')
    assert t['val'] == 400 and t['basis'].startswith('four_quarters')
    assert t['quarter_ends'] == ['2026-09-30', '2026-06-30', '2026-03-31', '2025-12-31']
    q = F.quarters(G + 'NetCashProvidedByUsedInOperatingActivities', '2026-11-25')
    assert q['2025-12-31']['basis'] == 'ytd_difference' and q['2025-12-31']['val'] == 100  # FY 400 - 9M 300
    assert F.ttm([G + 'NetCashProvidedByUsedInOperatingActivities'], '2025-12-31', '2026-11-25')['basis'] == 'annual'


def test_ttm_may_fiscal_year_end():
    """Oracle-like fiscal year June to May, quarter ends Aug, Nov, Feb, May."""
    tag = G + 'Revenues'
    rows = [(tag, '2024-06-01', '2025-05-31', 400, '2025-06-20', 'k25', '10-K'),
            (tag, '2024-06-01', '2025-02-28', 300, '2025-03-10', 'q3', '10-Q'),
            (tag, '2024-06-01', '2024-11-30', 200, '2024-12-10', 'q2', '10-Q'),
            (tag, '2025-06-01', '2025-08-31', 130, '2025-09-10', 'q1n', '10-Q'),
            (tag, '2024-06-01', '2024-08-31', 100, '2024-09-10', 'q1', '10-Q')]
    F = L4.Facts(companyfacts(rows))
    t = F.ttm([tag], '2025-08-31', '2025-09-30')
    # Q1 FY26 130 + Q4 FY25 (400 - 300) + Q3 (300 - 200) + Q2 (200 - 100)
    assert t['val'] == 430
    assert F.ttm([tag], '2025-08-31', '2025-09-09') is None  # Q1 FY26 not filed yet


def test_ttm_annual_only_filer():
    tag = G + 'Revenues'
    F = L4.Facts(companyfacts([(tag, '2024-01-01', '2024-12-31', 50, '2025-04-30', 'a24', '20-F'),
                               (tag, '2025-01-01', '2025-12-31', 80, '2026-04-30', 'a25', '20-F')]))
    assert F.ttm([tag], '2025-12-31', '2026-11-25')['val'] == 80
    assert F.ttm([tag], '2025-12-31', '2026-04-29') is None
    assert L4.quarter_values(F.durations(tag, '2026-11-25')) == {}


def test_negative_derived_revenue_quarter_is_dropped():
    tag = G + 'Revenues'
    F = L4.Facts(companyfacts([(tag, '2024-06-01', '2025-02-28', 177, '2025-04-10', 'q3', '10-Q'),
                               (tag, '2024-06-01', '2025-05-31', 144, '2025-07-30', 'k', '10-K')]))
    assert '2025-05-31' not in F.quarters(tag, '2026-01-01')
    ocf = G + 'NetCashProvidedByUsedInOperatingActivities'  # cash flow may be negative
    F2 = L4.Facts(companyfacts([(ocf, '2024-06-01', '2025-02-28', 177, '2025-04-10', 'q3', '10-Q'),
                                (ocf, '2024-06-01', '2025-05-31', 144, '2025-07-30', 'k', '10-K')]))
    assert F2.quarters(ocf, '2026-01-01')['2025-05-31']['val'] == -33


# --- metrics and flags -----------------------------------------------------------------------------------------------

def test_f1_cash_only_flags_undrawn_missing_and_year_two_from_annual_schedule():
    F = L4.Facts(companyfacts(alpha_rows()))
    v, det = L4.f1_at(F, '2026-09-30', '2026-11-25')
    # current debt 250 + year two 200 (2025-12-31 schedule) + capex 1200 - OCF 400, over cash 600
    assert v == pytest.approx((250 + 200 + 1200 - 400) / 600)
    assert det['undrawn'] is None
    assert any(f.startswith('undrawn_missing') and 'overstates' in f for f in det['flags'])
    assert any(f.startswith('year_two_from_earlier_schedule') for f in det['flags'])
    v4, det4 = L4.f1_at(F, '2025-12-31', '2026-11-25')  # schedule at a 10-K date
    assert det4['debt_due_basis'].startswith('maturity_schedule') and v4 == pytest.approx((300 + 200 + 1200 - 400) / 900)


def test_f1_uses_undrawn_facilities_when_tagged():
    rows = alpha_rows() + [(G + 'LineOfCreditFacilityRemainingBorrowingCapacity', None, '2026-09-30', 400,
                            '2026-11-05', 'acc-2026-q3', '10-Q')]
    v, det = L4.f1_at(L4.Facts(companyfacts(rows)), '2026-09-30', '2026-11-25')
    assert v == pytest.approx(1250 / 1000) and det['undrawn'] == 400
    assert not any(f.startswith('undrawn_missing') for f in det['flags'])


def test_f1_year_two_missing_flag_and_debt_due_missing():
    rows = [r for r in alpha_rows() if 'Maturities' not in r[0]]
    v, det = L4.f1_at(L4.Facts(companyfacts(rows)), '2026-09-30', '2026-11-25')
    assert v == pytest.approx((250 + 1200 - 400) / 600)
    assert any(f.startswith('year_two_missing') for f in det['flags'])
    rows = [r for r in rows if 'LongTermDebtCurrent' not in r[0]]
    v, why = L4.f1_at(L4.Facts(companyfacts(rows)), '2026-09-30', '2026-11-25')
    assert v is None and 'debt_due_missing' in why


def test_f2_and_lease_missing_flag():
    F = L4.Facts(companyfacts(alpha_rows()))
    v, det = L4.f2_at(F, '2026-09-30', '2026-11-25')
    assert v == pytest.approx((2000 + 100 - 600) / 800)
    assert any(f.startswith('finance_lease_missing') for f in det['flags'])
    assert det['filed'] == '2026-11-05' and det['accession'] == 'acc-2026-q3'


def test_partial_debt_rejected_when_an_earlier_full_tag_reported_more():
    tag = G + 'CashAndCashEquivalentsAtCarryingValue'
    rows = [(G + 'LongTermDebt', None, '2026-03-31', 3000, '2026-05-08', 'q1', '10-Q'),
            (G + 'ConvertibleDebt', None, '2026-09-30', 1600, '2026-11-06', 'q3', '10-Q'),
            (G + 'ConvertibleDebt', None, '2027-09-30', 1600, '2027-11-06', 'q3n', '10-Q'),
            (tag, None, '2026-09-30', 10, '2026-11-06', 'q3', '10-Q')]
    F = L4.Facts(companyfacts(rows))
    v, info = L4.debt_at(F, '2026-09-30', '2026-11-25')
    assert v is None and info['flags'][0].startswith('debt_tag_gap')
    v, info = L4.debt_at(F, '2027-09-30', '2027-11-25')  # no full figure in the previous 365 days: used, flagged
    assert v == 1600 and info['quality'] == 'partial' and info['flags'][0].startswith('debt_partial')


# --- 8-K items -------------------------------------------------------------------------------------------------------

EVENTS = [('2026-08-27', '8-K', '2.03,9.01', 'e-out'),        # D - 90: outside the window
          ('2026-08-28', '8-K', '1.01,2.03', 'e-first'),      # D - 89: inside
          ('2026-10-01', '8-K', '1.01', 'e-101'),             # 1.01 only: shown, not scored
          ('2026-10-15', '8-K/A', '2.04', 'e-amend'),         # amendment with 2.04
          ('2026-11-05', '10-Q', '', 'e-10q'),                # not an 8-K
          ('2026-11-25', '8-K', '2.04', 'e-today'),           # D: inside
          ('2026-11-26', '8-K', '2.03', 'e-late'),            # after D: invisible
          ('2025-10-01', '8-K', '2.03', 'e-prior'),           # inside the window a year earlier
          ('2025-06-01', '8-K', '7.01', 'e-old')]


def test_8k_item_counting_window():
    ev = L4.load_events([submissions(EVENTS)])
    c = L4.count_events(ev, '2026-11-25', 90, ['2.03', '2.04'])
    assert c['count'] == 3 and c['amendments'] == 1
    assert {f['accession'] for f in c['filings']} == {'e-first', 'e-amend', 'e-today'}
    assert c['window'] == ['2026-08-28', '2026-11-25']
    v, det = L4.f4_value(ev, '2026-11-25', P, coverage_from='2025-06-01')
    assert v == 3 and det['unscored_item_1.01']['count'] == 2 and det['prior'] == {'as_of': '2025-11-25', 'count': 1}
    assert det['filed'] == '2026-11-25' and det['accession'] == 'e-today'


def test_f4_missing_not_zero_without_8k_history():
    ev = L4.load_events([submissions([('2026-05-01', '6-K', '', 'x'), ('2026-04-30', '20-F', '', 'y')])])
    v, det = L4.f4_value(ev, '2026-11-25', P, coverage_from='2026-04-30')
    assert v is None and '6-K' in det['reason']


def test_f4_prior_missing_when_submissions_history_is_short():
    ev = L4.load_events([submissions([('2026-09-01', '8-K', '2.03', 'a'), ('2026-07-01', '8-K', '8.01', 'b')])])
    v, det = L4.f4_value(ev, '2026-11-25', P, coverage_from='2026-07-01')
    assert v == 1 and det['prior'] is None and 'too late' in det['prior_reason']


def test_submissions_pages_deduplicate_by_accession():
    a = submissions([('2026-01-02', '8-K', '2.03', 'same'), ('2026-01-03', '8-K', '2.04', 'new')])
    page = a['filings']['recent']
    assert len(L4.load_events([a, page])) == 2


# --- scoring ---------------------------------------------------------------------------------------------------------

def test_missing_change_halves_weight_and_staleness_scales_it():
    stale = {'full_weight_days': 120, 'zero_weight_days': 180}
    mp = {'anchor': 1.0, 'scale': 0.5}
    s = L4.score_metric('f1', 2.0, None, mp, 0.5, '2026-09-30', '2026-11-25', stale)
    assert s['anchor_z'] == pytest.approx(2.0) and s['z'] == pytest.approx(2.0) and s['weight'] == pytest.approx(0.5)
    s = L4.score_metric('f1', 2.0, 1.5, mp, 0.5, '2026-06-30', '2026-11-27', stale)  # 150 days: staleness 0.5
    assert s['change_z'] == pytest.approx(1.0) and s['z'] == pytest.approx(1.5)
    assert s['staleness_weight'] == pytest.approx(0.5) and s['weight'] == pytest.approx(0.5)
    s = L4.score_metric('f1', 2.0, 1.5, mp, 0.5, '2026-01-31', '2026-11-27', stale)
    assert s['weight'] == 0 and s['status'] == 'stale' and s['z'] is not None  # the value is kept, never neutralised


def test_issuer_with_missing_metrics_loses_weight_not_score():
    m = method()
    iss = {'id': 'BBB', 'cik': 2, 'group': 'GB'}
    ev = L4.load_events([submissions([('2026-11-01', '8-K', '2.04', 'x'), ('2025-01-01', '8-K', '8.01', 'y')])])
    s = L4.issuer_snapshot(iss, None, ev, '2025-01-01', '2026-11-25', m)
    assert s['metrics_live'] == ['f4'] and s['weight'] == pytest.approx(1 / 3)
    assert s['score'] == pytest.approx(s['metrics']['f4']['z'])
    assert s['metrics']['f3']['status'] == 'not_implemented' and s['metrics']['f3']['z'] is None
    assert s['coverage']['f1'] == 'missing'


def test_layer_point_requires_live_share():
    m = method()
    live = {'issuer': 'AAA', 'group': 'GA', 'score': 1.0, 'weight': 1.0}
    dead = {'issuer': 'BBB', 'group': 'GB', 'score': None, 'weight': 0.0}
    pt = L4.layer_point({'AAA': live, 'BBB': dead}, '2026-11-25', m)
    assert pt['status'] == 'insufficient_live_weight' and pt['Z'] is None and pt['live_share'] == 0.5
    assert pt['Z_unpublished'] == pytest.approx(1.0) and pt['level'] is None  # computed and shown, never published
    pt = L4.layer_point({'AAA': live, 'BBB': dict(dead, score=-1.0, weight=1.0)}, '2026-11-25', m)
    assert pt['status'] == 'ok' and pt['Z'] == pytest.approx(0.0) and pt['weights']['cap_infeasible']
    assert pt['cap'] == 'Watch'  # 2-group rule: no group is Stressed


# --- compute (offline, cached snapshot) ------------------------------------------------------------------------------

def write_cache(root, snap, cik, kind, payload):
    p = root / 'data' / 'raw' / 'sec' / snap / kind / f'CIK{cik:010d}.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))


def beta_rows():
    tag = G + 'Revenues'
    return [(tag, '2025-01-01', '2025-12-31', 80, '2026-04-30', 'b25', '20-F'),
            (G + 'CashAndCashEquivalentsAtCarryingValue', None, '2025-12-31', 50, '2026-04-30', 'b25', '20-F'),
            (G + 'LongTermDebt', None, '2025-12-31', 300, '2026-04-30', 'b25', '20-F')]


def fixture_root(tmp_path, extra_alpha=()):
    write_cache(tmp_path, '2026-11-26', 1, 'companyfacts', companyfacts(alpha_rows() + list(extra_alpha)))
    write_cache(tmp_path, '2026-11-26', 1, 'submissions', submissions(EVENTS))
    write_cache(tmp_path, '2026-11-26', 2, 'companyfacts', companyfacts(beta_rows(), cik=2, name='Beta NV'))
    write_cache(tmp_path, '2026-11-26', 2, 'submissions', submissions([('2026-05-01', '6-K', '', 'x')], name='Beta NV'))
    return tmp_path


def test_compute_offline_from_cache(tmp_path):
    root = fixture_root(tmp_path)
    res, inputs = L4.compute('2026-11-25', method(), {'root': str(root), 'offline': True})
    a, b = res['issuers']['AAA'], res['issuers']['BBB']
    assert a['metrics']['f2']['period_end'] == '2026-09-30' and a['metrics']['f2']['filed'] == '2026-11-05'
    assert a['metrics']['f4']['value'] == 3
    assert b['metrics']['f2']['status'] == 'stale' and b['metrics']['f4']['status'] == 'missing'
    assert 'BBB f4: missing' in res['coverage_gaps']
    assert res['status'] == 'insufficient_live_weight' and res['Z'] is None  # BBB has no live metric: 50% live
    np_ = res['not_published']                                                 # ...but readers can see the value
    assert np_['live_share'] == 0.5 and np_['Z'] is not None and np_['level'] is not None
    assert np_['note'].startswith('not published') and res['published'] is None
    assert res['as_of'] == '2026-09-30' and res['as_of'] <= res['available'] <= res['build_date'] == '2026-11-25'
    assert len(inputs) == 4 and all(len(i['sha256']) == 64 for i in inputs)
    assert res['stubs']['f3'] and res['issuers']['AAA']['metrics']['f3']['status'] == 'not_implemented'
    json.dumps(res, allow_nan=False)


def test_compute_has_no_lookahead(tmp_path):
    """A restatement and a new filing dated after the as-of date leave the as-of result unchanged."""
    late = [(G + 'CashAndCashEquivalentsAtCarryingValue', None, '2026-09-30', 1, '2026-12-01', 'late', '10-Q/A'),
            (G + 'LongTermDebt', None, '2026-12-31', 99999, '2027-02-20', 'late2', '10-K')]
    r1, _ = L4.compute('2026-11-25', method(), {'root': str(fixture_root(tmp_path / 'a')), 'offline': True})
    r2, _ = L4.compute('2026-11-25', method(), {'root': str(fixture_root(tmp_path / 'b', late)), 'offline': True})
    strip = lambda r: json.dumps({k: v for k, v in r.items() if k not in ('_series',)}, sort_keys=True, default=str)
    assert strip(r1).replace(str(tmp_path / 'a'), '') == strip(r2).replace(str(tmp_path / 'b'), '')
    assert r1['_series'] == r2['_series']


def test_compute_series_is_fresh_on_releases_only(tmp_path):
    m = method(issuer_groups={'GA': ['AAA'], 'GB': ['BBB']}, ciks={'AAA': 1, 'BBB': 2})
    m['engine']['min_live_share'] = 0.3
    res, _ = L4.compute('2026-11-24', m, {'root': str(fixture_root(tmp_path)), 'offline': True})
    s = res['_series']
    assert s[-1]['session'] == '2026-11-24' and s[-1]['status'] == 'carried' and res['status'] == 'carried'
    assert {x['session'] for x in s if x['status'] == 'fresh'} >= {'2026-11-05', '2026-10-15'}
    assert res['available'] == '2026-11-05'
    # a carried point holds the last release's value exactly (only the live share follows staleness)
    last_fresh = [x for x in s if x['status'] == 'fresh'][-1]
    assert s[-1]['Z'] == last_fresh['Z'] and res['Z'] == round(last_fresh['Z'], 3)
    later, _ = L4.compute('2027-01-20', m, {'root': str(fixture_root(tmp_path)), 'offline': True})
    assert later['_series'][-1]['Z'] in (None, last_fresh['Z'])          # never a new number without a new filing
    assert later['_series'][-1]['live_share'] <= s[-1]['live_share']


def test_online_needs_sec_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    res, inputs = L4.compute('2026-11-25', method(), {'root': str(fixture_root(tmp_path)), 'offline': False})
    assert res['status'] == 'unavailable' and 'SEC_USER_AGENT' in res['reason'] and inputs == []


def test_online_fetch_uses_env_user_agent_and_dated_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'test-agent contact@example.org')
    seen = []
    payloads = {('companyfacts', 1): companyfacts(alpha_rows()), ('submissions', 1): submissions(EVENTS),
                ('companyfacts', 2): companyfacts(beta_rows(), cik=2), ('submissions', 2): submissions([])}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header('User-agent')))
        kind = 'companyfacts' if '/companyfacts/' in req.full_url else 'submissions'
        cik = int(req.full_url.rsplit('CIK', 1)[1].split('.')[0])
        return Resp(json.dumps(payloads[(kind, cik)]).encode())

    ctx = {'root': str(tmp_path), 'offline': False, 'today': '2026-11-27', 'opener': opener}
    res, inputs = L4.compute('2026-11-25', method(max_requests_per_second=10), ctx)
    assert len(seen) == 4 and all(ua == 'test-agent contact@example.org' for _, ua in seen)
    assert (tmp_path / 'data/raw/sec/2026-11-27/companyfacts/CIK0000000001.json').is_file()
    assert res['sources']['snapshots'] == ['2026-11-27'] and res['issuers']['AAA']['metrics']['f4']['value'] == 3
    seen.clear()
    L4.compute('2026-11-25', method(), ctx)  # same UTC day: served from the snapshot, no request
    assert seen == []


# --- config ----------------------------------------------------------------------------------------------------------

def test_stack_v0_l4_issuers_valid():
    m, _ = config.load()
    issuers, errs = L4.issuers_from(m)
    assert errs == [] and len(issuers) == 8
    assert {i['id']: i['cik'] for i in issuers}['CRWV'] == 1769628


def test_issuer_without_group_is_rejected():
    m = method(ciks={'AAA': 1, 'BBB': 2, 'CCC': 3})
    res, _ = L4.compute('2026-11-25', m, {'root': '/nonexistent', 'offline': True})
    assert res['status'] == 'unavailable' and 'CCC has no group' in res['reason']
