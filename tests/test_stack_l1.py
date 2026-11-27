"""L1 equity (stack/l1_equity.py) on synthetic prices and FRED series: no network, no data/ needed. One integration
test on the cached raw files, skipped when data/raw/prices has not been populated."""
import copy, datetime as dt, math, pathlib, random, sys
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import build, l1_equity as l1  # noqa: E402

BASE = yaml.safe_load(open(ROOT / 'stack' / 'stack-v0.yaml'))


def weekdays(n, start='2024-01-01'):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def method(**over):
    m = copy.deepcopy(BASE)
    p = m['layers']['L1']
    p.pop('membership', None)
    p.update(e1=['AAA', 'BBB'], e2=['MMM'], e3_lenders=['L1X', 'L2X'], e3_benchmark='BDCB',
             price_start='2000-01-01', fit_sessions=60, fit_min_obs=55, z_window=252, z_min_history=20, z_floor=1e-4,
             factors={'equity': ['QQQ', 'SMH'], 'credit': 'hyg_ief', 'vix': 'fred', 'rates': ['DGS2', 'DGS10'], 'btc': 'none'})
    m['groups'].update({'GroupA': ['AAA'], 'GroupB': ['BBB']})
    p.update(over)
    return m


def world(n=200, seed=7, shock=None):
    """Synthetic prices and FRED series. Names load on the factors plus noise; shock: {ticker: (from_index, daily
    log return added)}."""
    rng = random.Random(seed)
    days = weekdays(n)
    f = {t: [rng.gauss(0, 0.01) for _ in days] for t in ('QQQ', 'SMH', 'HYG', 'IEF')}
    fred = {s: {} for s in ('VIXCLS', 'DGS2', 'DGS10')}
    lv = {'VIXCLS': 20.0, 'DGS2': 4.0, 'DGS10': 4.2}
    for d in days:
        for s in fred:
            lv[s] += rng.gauss(0, 0.05 if s != 'VIXCLS' else 1.0)
            fred[s][d] = lv[s]
    betas = {'AAA': (1.2, 0.5), 'BBB': (0.8, 1.0), 'MMM': (0.3, 2.0), 'L1X': (0.5, 0.0), 'L2X': (0.6, 0.0), 'BDCB': (0.55, 0.0)}
    rets = dict(f)
    for t, (bq, bs) in betas.items():
        rets[t] = [bq * f['QQQ'][i] + bs * f['SMH'][i] + 0.002 * (fred['VIXCLS'][days[i]] - fred['VIXCLS'][days[i - 1]] if i else 0)
                   + rng.gauss(0, 0.01) for i in range(n)]
    for t, (k0, r) in (shock or {}).items():
        rets[t] = [x + (r if i >= k0 else 0.0) for i, x in enumerate(rets[t])]
    prices = {}
    for t, rs in rets.items():
        px, s = 100.0, {}
        for d, r in zip(days, rs):
            px *= math.exp(r)
            s[d] = px
        prices[t] = s
    return days, prices, fred


# --- OLS -------------------------------------------------------------------------------------------------------------

def test_ols_against_a_hand_computed_case():
    # x = 0, 1, 2 and y = 1, 2, 4: slope = sum((x - 1)(y - 7/3)) / sum((x - 1)^2) = 3 / 2, intercept = 7/3 - 3/2 = 5/6
    a, b = l1.ols([[0.0], [1.0], [2.0]], [1.0, 2.0, 4.0])
    assert a == pytest.approx(5 / 6) and b == pytest.approx(1.5)
    X = [[0, 0], [1, 0], [0, 1], [1, 1], [2, 1]]
    y = [0.5 + 2 * x1 - 3 * x2 for x1, x2 in X]
    assert l1.ols(X, y) == pytest.approx([0.5, 2.0, -3.0])


def test_singular_fit_returns_none():
    assert l1.ols([[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]], [1.0, 2.0, 3.0]) is None


def test_residual_for_day_t_is_out_of_sample():
    rng = random.Random(1)
    n, W = 150, 40
    X = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(n)]
    y = [0.1 + 0.7 * a - 0.2 * b + rng.gauss(0, 0.3) for a, b in X]
    res = l1.rolling_residuals(y, X, window=W, min_obs=W, recompute_every=63)
    assert all(r['status'] == 'no_fit' for r in res[:W])
    for i in (W, 63, 64, 100, 126, 149):  # across the rebuilds of the rolling sums
        beta = l1.ols(X[i - W:i], y[i - W:i])  # the previous W rows, i excluded
        assert res[i]['beta'] == pytest.approx(beta, rel=1e-9, abs=1e-12)
        assert res[i]['resid'] == pytest.approx(y[i] - beta[0] - beta[1] * X[i][0] - beta[2] * X[i][1], abs=1e-12)
    y2 = list(y)
    y2[100] += 5.0  # an outlier on day t moves its own residual one for one and leaves its own fit alone
    res2 = l1.rolling_residuals(y2, X, window=W, min_obs=W)
    assert res2[100]['beta'] == pytest.approx(res[100]['beta'])
    assert res2[100]['resid'] == pytest.approx(res[100]['resid'] + 5.0)
    assert res2[101]['beta'] != pytest.approx(res[101]['beta'])  # and it enters the next day's fit


def test_missing_rows_are_skipped_and_counted():
    rng = random.Random(2)
    X = [[rng.gauss(0, 1)] for _ in range(100)]
    y = [x[0] + rng.gauss(0, 0.1) for x in X]
    y[50] = None
    X[55] = None
    res = l1.rolling_residuals(y, X, window=30, min_obs=29)
    assert res[50]['status'] == 'missing' and res[55]['status'] == 'missing'
    assert res[60]['n_fit'] == 28 and res[60]['status'] == 'no_fit'  # rows 30..59, two missing
    assert res[81]['status'] == 'ok' and res[81]['n_fit'] == 29       # rows 51..80, one missing
    assert res[86]['n_fit'] == 30


# --- availability ----------------------------------------------------------------------------------------------------

def test_fred_observation_is_available_the_next_weekday():
    assert l1.next_weekday('2026-11-24') == '2026-11-25'
    assert l1.next_weekday('2026-11-20') == '2026-11-23'  # Friday -> Monday
    col = l1.fred_change_col({'2026-11-19': 4.0, '2026-11-20': 4.1}, ['2026-11-19', '2026-11-20', '2026-11-23'])
    assert col[1] == (pytest.approx(0.1), '2026-11-23', None)
    assert col[2] == (None, None, 'not published')


def test_fred_blank_is_a_closed_market_and_change_is_zero():
    obs = {'2026-11-09': 4.0, '2026-11-10': 4.1, '2026-11-11': None, '2026-11-12': 4.3}
    col = l1.fred_change_col(obs, ['2026-11-10', '2026-11-11', '2026-11-12'])
    assert col[1] == (0.0, '2026-11-12', 'closed')
    assert col[2][0] == pytest.approx(0.2)  # against the last numeric value, 10 Nov


def test_residual_for_d_waits_for_its_fred_observations():
    days, prices, fred = world()
    D = days[-1]
    m = method()
    names, rows = l1.factor_rows(days, prices, fred, m['layers']['L1']['factors'], D)
    assert rows[-1]['x'] is None and 'not yet available' in rows[-1]['missing']
    assert rows[-2]['x'] is not None and rows[-2]['available'] == D
    r = l1.snapshot(D, prices, fred, m)
    assert r['session'] == days[-2] and r['available'] == D and r['status'] == 'fresh'
    assert r['last_price_session'] == D
    # FRED values dated D (published on D+1) and anything after D cannot change the as-of D build
    fred2 = copy.deepcopy(fred)
    for s in fred2:
        fred2[s][D] += 9.0
    later = l1.next_weekday(D)
    prices2 = copy.deepcopy(prices)
    for t in prices2:
        prices2[t][later] = prices2[t][D] * 0.5
    fred2['DGS2'][later] = 0.0
    p2 = {t: {d: v for d, v in s.items() if d <= D} for t, s in prices2.items()}
    r2 = l1.snapshot(D, p2, fred2, m)
    assert r2['Z'] == r['Z'] and r2['names'] == r['names']


def test_weekend_build_carries_the_friday_value():
    days, prices, fred = world(n=203)
    fri = next(d for d in reversed(days) if dt.date.fromisoformat(d).weekday() == 4)
    k = days.index(fri)
    p = {t: {d: v for d, v in s.items() if d <= fri} for t, s in prices.items()}
    sun = (dt.date.fromisoformat(fri) + dt.timedelta(days=2)).isoformat()
    r = l1.snapshot(sun, p, fred, method())
    # the Friday residual needs Friday's FRED, available Monday: on Sunday the last residual is Thursday's
    assert r['session'] == days[k - 1] and r['available'] == fri and r['status'] == 'carried'
    assert r['band_status'].startswith('held')


def test_shifting_a_factor_by_one_day_changes_todays_residual():
    days, prices, fred = world()
    m = method()
    i = len(days) - 2
    base = l1.series_all(days[-1], prices, fred, m)[3]
    smh = prices['SMH']  # not the calendar ticker, so the sessions stay the same
    shifted = dict(prices, SMH={days[k]: smh[days[k - 1]] for k in range(1, len(days))})
    moved = l1.series_all(days[-1], shifted, fred, m)[3]
    for a, b in zip(base, moved):
        assert a['res'][i]['status'] == 'ok' and b['res'][i]['status'] == 'ok'
        assert a['res'][i]['resid'] != pytest.approx(b['res'][i]['resid'], abs=1e-6)


# --- sign, membership, BTC -------------------------------------------------------------------------------------------

def test_falling_equity_residual_is_stress():
    days, prices, fred = world(shock={'AAA': (195, -0.05), 'L1X': (195, -0.03), 'L2X': (195, -0.03)})
    r = l1.snapshot(days[-1], prices, fred, method())
    assert r['names']['AAA']['residual_log'] < 0 and r['names']['AAA']['s'] > 0 and r['names']['AAA']['z'] > 2
    assert r['names']['E3']['s'] > 0 and r['names']['E3']['z'] > 2  # lenders under their benchmark = stress
    calm = l1.snapshot(days[-1], *world()[1:], method())
    assert r['Z'] > calm['Z']
    up = l1.snapshot(days[-1], *world(shock={'AAA': (195, 0.05)})[1:], method())
    assert up['names']['AAA']['z'] < -2


def test_membership_window_stops_stale_prices_leaking():
    days = weekdays(10)
    px = {d: 100.0 + k for k, d in enumerate(days)}
    win = l1.parse_windows([f':{days[3]}', f'{days[6]}:'])
    r = l1.ret_col(l1.member_prices(px, win), days)
    assert r[3] is not None and r[4] is None and r[5] is None and r[6] is None and r[7] is not None
    with pytest.raises(ValueError):
        l1.parse_windows(['2025-01-02'])
    with pytest.raises(ValueError):
        l1.parse_windows(['2025-02-01:2025-01-01'])


def test_btc_uses_the_bar_that_closed_before_the_us_close():
    days = ['2026-11-23', '2026-11-24', '2026-11-25']  # Mon, Tue, Wed
    btc = {'2026-11-21': 100.0, '2026-11-22': 110.0, '2026-11-23': 121.0, '2026-11-24': 133.1, '2026-11-25': 1e9}
    col = l1.btc_lagged_col(btc, days)
    assert col[1] == pytest.approx(math.log(121.0 / 110.0))  # Tue: bar of Mon (closed 00:00 UTC Tue) vs bar of Sun
    assert col[2] == pytest.approx(math.log(133.1 / 121.0))  # Wed's own bar (closes after the US close) is not used


def test_btc_factor_is_added_to_miners_only():
    days, prices, fred = world()
    rng = random.Random(3)
    d0, px, btc = dt.date.fromisoformat(days[0]) - dt.timedelta(days=3), 50000.0, {}
    while d0.isoformat() <= days[-1]:
        px *= math.exp(rng.gauss(0, 0.03))
        btc[d0.isoformat()] = px
        d0 += dt.timedelta(days=1)
    prices['BTC-USD'] = btc
    names = l1.series_all(days[-1], prices, fred, method(factors=dict(method()['layers']['L1']['factors'], btc='lagged')))[3]
    fac = {nm['id']: nm['factors'] for nm in names}
    assert fac['MMM'][-1] == 'BTC-USD (lagged)' and 'BTC-USD (lagged)' not in fac['AAA'] + fac['E3']


# --- history, groups, the layer --------------------------------------------------------------------------------------

def test_a_late_listing_reports_insufficient_history():
    days, prices, fred = world()
    prices['BBB'] = {d: v for d, v in prices['BBB'].items() if d >= days[125]}
    r = l1.snapshot(days[-1], prices, fred, method())
    b = r['names']['BBB']
    assert b['status'] == 'insufficient_history' and b['z'] is None and 'needs 20' in b['reason']
    assert 'BBB' not in r['legs']['E1']['names_live'] and r['status'] == 'fresh'
    prices['BBB'] = {d: v for d, v in prices['BBB'].items() if d >= days[160]}
    b = l1.snapshot(days[-1], prices, fred, method())['names']['BBB']
    assert b['status'] == 'no_fit' and 'needs 55' in b['reason']


def test_one_name_cannot_light_the_layer():
    days, prices, fred = world(shock={'AAA': (190, -0.15)})
    m = method(leg_weights={'E1': 1.0, 'E2': 0.0, 'E3': 0.0}, e1=['AAA'])
    m['engine']['min_live_share'] = 0.5
    r = l1.snapshot(days[-1], prices, fred, m)
    assert r['groups']['GroupA']['band'] in ('Stressed', 'Severe')
    assert r['raw_band'] in ('Stressed', 'Severe') and r['band'] == 'Watch'
    assert r['two_group']['cap'] == 'Watch' and '1 borrower group' in r['two_group']['reason']
    assert r['cap_infeasible'] is True  # a single group cannot be held to 20%


def test_group_cap_and_attribution_publish_raw_and_residual():
    days, prices, fred = world()
    r = l1.snapshot(days[-1], prices, fred, method())
    w = {g: a['weight'] for g, a in r['attribution'].items()}
    assert set(w) == {'GroupA', 'GroupB', 'MMM', 'Exposed lenders (E3)'}
    assert r['cap_infeasible'] is True  # 4 groups < 1 / 0.2
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)
    for a in r['attribution'].values():
        assert a['contribution'] is not None and a['raw_contribution'] is not None and 'uncapped_weight' in a
    assert r['Z'] == pytest.approx(sum(a['contribution'] for a in r['attribution'].values()), abs=2e-3)
    assert r['Z_raw'] is not None and r['raw_note'].endswith('not scored')


def test_live_leg_weight_gate():
    days, prices, fred = world()
    for t in ('AAA', 'BBB'):
        prices.pop(t)
    r = l1.snapshot(days[-1], prices, fred, method())  # E1 0.45 of the weight missing: 55% < 70%
    assert r['status'] == 'insufficient_live_weight' and r['Z'] is None and r['published'] is None
    assert r['names']['AAA']['status'] == 'no_prices'


def test_config_errors_are_reported_not_scored(tmp_path):
    m = method(leg_weights={'E1': 0.5, 'E9': 0.5})
    r, ins = l1.compute('2026-11-25', m, {'root': str(tmp_path), 'offline': True})
    assert r['status'] == 'config_error' and 'E9' in r['reason'] and r['Z'] is None and ins == []


# --- loading ---------------------------------------------------------------------------------------------------------

def _write_cache(root, prices, fred):
    for t, s in prices.items():
        p = root / 'data' / 'raw' / 'prices' / l1._price_name(t)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(l1.prices_csv(s))
    for sid, obs in fred.items():
        p = root / 'data' / 'raw' / 'fred' / f'{sid}.csv'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('observation_date,' + sid + '\n' + ''.join(f'{d},{"" if v is None else v}\n' for d, v in sorted(obs.items())))


def test_compute_offline_from_the_cache(tmp_path):
    days, prices, fred = world()
    _write_cache(tmp_path, prices, fred)
    r, ins = l1.compute(days[-1], method(), {'root': str(tmp_path), 'offline': True})
    assert r['status'] == 'fresh' and r['session'] == days[-2] and r['load_notes'] == []
    assert {i['path'] for i in ins} >= {'data/raw/prices/QQQ.csv', 'data/raw/fred/DGS10.csv'}
    assert all(len(i['sha256']) == 64 and i['source'] for i in ins)
    assert r['sources']['prices'] == 'Yahoo Finance via yfinance, research use, not republishable'


def test_offline_without_cache_gives_no_number(tmp_path):
    r, ins = l1.compute('2026-11-25', method(), {'root': str(tmp_path), 'offline': True})
    assert r['status'] == 'unavailable' and r['Z'] is None and ins == []
    assert any('FRED' in n for n in r['load_notes'])


def test_failed_download_writes_nothing_and_a_good_one_is_cached(tmp_path):
    days, prices, fred = world(n=20)
    _, notes = l1.load_prices(['QQQ'], days[-1], tmp_path, downloader=lambda t, s: {})[0:3:2]
    assert not (tmp_path / 'data' / 'raw' / 'prices').exists() or not any((tmp_path / 'data' / 'raw' / 'prices').iterdir())
    assert any('no prices for QQQ' in n for n in notes)

    def boom(t, s):
        raise RuntimeError('network down')
    _, _, notes = l1.load_prices(['QQQ'], days[-1], tmp_path, downloader=boom)
    assert any('network down' in n for n in notes)
    got, ins, notes = l1.load_prices(['QQQ'], days[-2], tmp_path, downloader=lambda t, s: {'QQQ': prices['QQQ']})
    assert max(got['QQQ']) == days[-2] and ins[0]['path'] == 'data/raw/prices/QQQ.csv' and notes == []
    calls = []
    l1.load_prices(['QQQ'], days[-1], tmp_path, downloader=lambda t, s: calls.append(t) or {})
    assert calls == []  # the cache covers the as-of date: no download


def test_yfinance_is_a_lazy_optional_import(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'yfinance', None)
    with pytest.raises(RuntimeError, match='requirements-stack.txt'):
        l1.yf_download(['QQQ'], '2024-01-01')
    _, _, notes = l1.load_prices(['QQQ'], '2026-11-25', tmp_path)
    assert any('yfinance' in n for n in notes)


def test_parsers_reject_bad_payloads():
    with pytest.raises(ValueError):
        l1.parse_prices(b'<html>rate limited</html>')
    with pytest.raises(ValueError):
        l1.parse_prices(b'date,adj_close\n2026-01-02,-1\n')
    with pytest.raises(ValueError):
        l1.parse_fred(b'<html></html>')
    assert l1.parse_fred(b'observation_date,DGS2\n2026-01-01,\n2026-01-02,4.1\n') == {'2026-01-01': None, '2026-01-02': 4.1}
    assert l1.parse_fred(b'DATE,DGS2\n2026-01-02,.\n2026-01-05,4.1\n')['2026-01-02'] is None


def test_build_runs_l1_alone(tmp_path):
    days, prices, fred = world()
    _write_cache(tmp_path, prices, fred)
    m = method()
    cfg = tmp_path / 'm.yaml'
    cfg.write_text(yaml.safe_dump(m, sort_keys=False))
    out, ins = build.run(days[-1], ['L1'], offline=True, config_path=str(cfg), root=tmp_path)
    assert out['layers']['L1']['status'] == 'fresh' and out['junior']['status'] == 'fresh'
    assert out['junior']['weights_effective']['L2'] == 0.0 and out['grid']['L2']['status'] == 'not_requested'


# --- the cached raw files (skips until a build has populated data/raw/prices) ----------------------------------------

PRICES = ROOT / 'data' / 'raw' / 'prices' / 'QQQ.csv'


@pytest.mark.skipif(not PRICES.exists(), reason='run python -m stack.build --layers L1 online first')
def test_cached_prices_build_has_no_lookahead():
    r, ins = l1.compute('2026-11-25', BASE, {'root': str(ROOT), 'offline': True})
    if r['status'] == 'unavailable':
        pytest.skip(r.get('reason'))
    assert r['session'] < '2026-11-25' and r['available'] <= '2026-11-25'
    assert all(i['sha256'] for i in ins)
