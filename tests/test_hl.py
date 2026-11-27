import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest
from hl import basket_logic as bl  # noqa: E402
from hl import tracker as ht  # noqa: E402

W = bl.DEFAULT_WEIGHTS


def test_targets_sum_to_gross_and_signs():
    t = bl.target_notionals(W, 1000)
    assert abs(sum(abs(v) for v in t.values()) - 1000) < 1e-9
    assert t['xyz:ORCL'] < 0 and t['xyz:IREN'] < 0 and t['xyz:NBIS'] < 0 and t['xyz:XYZ100'] > 0
    assert round(t['xyz:ORCL'], 2) == -210.16


def test_usd_per_point_matches_fit():
    assert round(bl.usd_per_point(W, 10000), 2) == 32.63


def test_round_size_floors():
    assert bl.round_size(1.23456, 3) == 1.234
    assert bl.round_size(-6.29, 1) == 6.2
    assert bl.round_size(0.00009, 4) == 0.0


@pytest.mark.parametrize('px,szd,buy,want', [
    (26878.7, 4, True, 26878.0),     # 5 sig figs -> integer
    (26878.2, 4, False, 26879.0),    # sells round up
    (109.4567, 3, True, 109.45),     # 5 sig figs -> 2 decimals
    (14.08767, 1, False, 14.088),    # 5 sig figs -> 3 decimals (max 5 for szDecimals 1)
    (0.123456789, 4, True, 0.12),    # max decimals 6 - 4 = 2
    (99999.7, 0, False, 100000.0),   # rounding up adds a digit
])
def test_round_price(px, szd, buy, want):
    assert bl.round_price(px, szd, buy) == want


def test_limit_price_never_exceeds_cap():
    mid = 109.33
    buy = bl.limit_price(mid, True, 0.005, 3)
    sell = bl.limit_price(mid, False, 0.005, 3)
    assert buy <= mid * 1.005 and buy > mid
    assert sell >= mid * 0.995 and sell < mid


def test_plan_open_from_flat():
    o, note = bl.plan_leg('xyz:ORCL', -210.16, 0.0, 109.33, 3)
    assert note == 'open' and len(o) == 1
    assert o[0]['is_buy'] is False and o[0]['reduce_only'] is False and o[0]['sz'] == 1.922


def test_plan_within_band_skips():
    o, note = bl.plan_leg('xyz:ORCL', -210.0, -1.85, 109.33, 3, band=0.10)  # off by ~$7.7
    assert o == [] and note.startswith('within band')


def test_plan_min_order_skips_small_target_delta():
    o, note = bl.plan_leg('xyz:IREN', -30.0, -1.5, 14.0, 1, band=0.0, min_order_usd=10)  # delta ~$9
    assert o == []


def test_plan_shrink_is_reduce_only():
    o, note = bl.plan_leg('xyz:ORCL', -100.0, -3.0, 100.0, 3)
    assert note == 'shrink' and o[0]['reduce_only'] and o[0]['is_buy'] and o[0]['sz'] == 2.0


def test_plan_grow_not_reduce_only():
    o, note = bl.plan_leg('xyz:ORCL', -300.0, -1.0, 100.0, 3)
    assert note == 'grow' and not o[0]['reduce_only'] and not o[0]['is_buy'] and o[0]['sz'] == 2.0


def test_plan_flip_closes_then_opens():
    o, note = bl.plan_leg('xyz:ORCL', -200.0, 1.0, 100.0, 3)
    assert note == 'flip' and len(o) == 2
    assert o[0]['reduce_only'] and not o[0]['is_buy'] and o[0]['sz'] == 1.0
    assert not o[1]['reduce_only'] and not o[1]['is_buy'] and o[1]['sz'] == 2.0


def test_flatten_reduce_only_all_legs():
    o = bl.plan_flatten({'xyz:ORCL': -1.5, 'xyz:XYZ100': 0.02, 'xyz:IREN': 0.0},
                        {'xyz:ORCL': 100, 'xyz:XYZ100': 26000, 'xyz:IREN': 14}, {'xyz:ORCL': 3, 'xyz:XYZ100': 4, 'xyz:IREN': 1})
    assert len(o) == 2 and all(x['reduce_only'] for x in o)
    d = {x['coin']: x for x in o}
    assert d['xyz:ORCL']['is_buy'] and not d['xyz:XYZ100']['is_buy']


def test_funding_cost_sign():
    assert bl.funding_cost_hourly(0.0001, +1) == 0.0001   # long pays positive funding
    assert bl.funding_cost_hourly(0.0001, -1) == -0.0001  # short receives it


def test_leverage_capped():
    assert bl.effective_leverage(20, 10) == 10 and bl.effective_leverage(0, 10) == 1


def _mk(funding=0.00001):
    legs = {}
    for i, (c, szd, mid) in enumerate([('xyz:XYZ100', 4, 26750.0), ('xyz:ORCL', 3, 109.3), ('xyz:NBIS', 2, 92.8),
                                        ('xyz:IREN', 1, 14.05)]):
        legs[c] = {'asset': 110000 + i, 'sz_decimals': szd, 'max_leverage': 10, 'only_isolated': c != 'xyz:XYZ100',
                   'mid': mid, 'oracle': mid, 'mark': mid, 'funding_hourly': funding, 'open_interest': 1}
    return {'dex_index': 1, 'legs': legs}


def test_build_plan_cap_and_funding_abort():
    cfg = ht.load_config(None)
    cfg['gross_usd'] = 10000
    with pytest.raises(ht.Abort):
        ht.build_plan(cfg, _mk(), {c: 0.0 for c in W})
    cfg['gross_usd'] = 1000
    plan = ht.build_plan(cfg, _mk(funding=0.001), {c: 0.0 for c in W})   # long XYZ100 would pay 0.1%/h
    assert any('XYZ100' in p for p in plan['problems'])
    plan = ht.build_plan(cfg, _mk(), {c: 0.0 for c in W})
    assert not plan['problems'] and len(plan['orders']) == 4
    assert all(o['usd'] >= cfg['min_order_usd'] for o in plan['orders'])


def test_reduce_only_orders_sorted_first():
    cfg = ht.load_config(None)
    pos = {'xyz:XYZ100': 0.0, 'xyz:ORCL': -5.0, 'xyz:NBIS': 0.0, 'xyz:IREN': 0.0}  # ORCL too big -> shrink
    plan = ht.build_plan(cfg, _mk(), pos)
    assert plan['orders'][0]['reduce_only'] and plan['orders'][0]['coin'] == 'xyz:ORCL'


def test_log_refuses_secret(tmp_path, monkeypatch):
    monkeypatch.setenv('HL_AGENT_KEY', '0x' + 'ab' * 32)
    log = ht.Log(str(tmp_path / 'l.jsonl'))
    log.write('ok', x=1)
    with pytest.raises(ht.Abort):
        log.write('bad', x='ab' * 32)


def test_signature_recovers_agent_address():
    """Offline: an order action signed with a throwaway key recovers to that key's address (no network)."""
    pytest.importorskip('hyperliquid')
    from eth_account import Account
    from hyperliquid.utils.signing import sign_l1_action, recover_agent_or_user_from_l1_action, order_request_to_order_wire, order_wires_to_order_action
    acct = Account.create()
    wire = order_request_to_order_wire({'coin': 'xyz:ORCL', 'is_buy': False, 'sz': 1.922, 'limit_px': 108.78,
                                        'order_type': {'limit': {'tif': 'Ioc'}}, 'reduce_only': False}, 110011)
    action = order_wires_to_order_action([wire], None)
    sig = sign_l1_action(acct, action, None, 1764200000000, None, True)
    assert recover_agent_or_user_from_l1_action(action, sig, None, 1764200000000, None, True) == acct.address


# --- review fixes -------------------------------------------------------------------------------------------------------

def test_zero_target_closes_dust():
    o, note = bl.plan_leg('xyz:IREN', 0.0, -0.4, 14.0, 1)   # $5.60 position, below band and minimum
    assert note == 'close' and o == [{'coin': 'xyz:IREN', 'is_buy': True, 'sz': 0.4, 'reduce_only': True, 'usd': 0.4 * 14.0}]


def test_parse_order_response():
    ok = {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'filled': {'totalSz': '0.5', 'avgPx': '1', 'oid': 1}}]}}}
    assert bl.parse_order_response(ok, 1)[0] == {'ok': True, 'filled': 0.5, 'resting': None, 'error': None}
    rest = {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'resting': {'oid': 7}}]}}}
    assert bl.parse_order_response(rest, 1)[0]['resting'] == 7
    err = {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'error': 'Insufficient margin'}]}}}
    assert not bl.parse_order_response(err, 1)[0]['ok']
    assert not bl.parse_order_response({'status': 'err', 'response': 'bad'}, 1)[0]['ok']
    assert not bl.parse_order_response(None, 1)[0]['ok']


def test_projected_gross():
    assert bl.projected_gross({'a': 1.0, 'b': -2.0}, [{'coin': 'a', 'is_buy': True, 'sz': 1.0}], {'a': 10, 'b': 5}) == 30


@pytest.mark.parametrize('k,v', [('gross_usd', -1000), ('exposure_scale', -1), ('slippage', 0.5), ('min_order_usd', 1),
                                 ('order_style', 'gtc'), ('weights', {'ORCL': -1}), ('gross_usd', float('nan'))])
def test_config_validation_rejects(tmp_path, k, v):
    p = tmp_path / 'c.json'
    p.write_text(__import__('json').dumps({k: v}) if v == v else '{"gross_usd": NaN}')
    with pytest.raises(ht.Abort):
        ht.load_config(str(p))


def test_negative_scale_cannot_bypass_cap():
    cfg = ht.load_config(None)
    cfg['exposure_scale'] = -100
    with pytest.raises(ht.Abort):
        ht.validate_config(cfg)


def test_account_must_be_address_and_is_not_echoed():
    fake_key = '0x' + 'ab' * 32
    with pytest.raises(ht.Abort) as e:
        ht.checked_account(fake_key)
    assert 'ab' * 8 not in str(e.value)
    assert ht.checked_account('0x' + 'A' * 40) == '0x' + 'a' * 40


def test_verify_signer(monkeypatch):
    acct, agent = '0x' + '1' * 40, '0x' + '2' * 40
    roles = {agent: {'role': 'agent', 'data': {'user': '0x' + '3' * 40}}, acct: {'role': 'user'}}
    monkeypatch.setattr(ht, 'http_json', lambda url, body: roles[body['user']])
    with pytest.raises(ht.Abort, match='belongs to'):
        ht.verify_signer({'network': 'mainnet'}, agent, acct)
    roles[agent]['data']['user'] = acct
    ht.verify_signer({'network': 'mainnet'}, agent, acct)
    roles[acct] = {'role': 'subAccount'}
    with pytest.raises(ht.Abort):
        ht.verify_signer({'network': 'mainnet'}, agent, acct)


def _mk1():
    L = {'mid': 100.0, 'oracle': 100.0, 'mark': 100.0, 'sz_decimals': 2, 'max_leverage': 10, 'only_isolated': True,
         'funding_hourly': 0.0, 'asset': 110011}
    return {'dex_index': 1, 'legs': {'xyz:ORCL': dict(L)}, 'warnings': []}


class FakeEx:
    def __init__(self, fill_frac):
        self.fill_frac, self.sent, self.filled = fill_frac, [], []

    def update_leverage(self, *a, **k):
        return {'status': 'ok'}

    def order(self, coin, is_buy, sz, px, ot, ro):
        self.sent.append((coin, is_buy, sz, ro))
        f = sz * (self.fill_frac if ro else 1)
        self.filled.append(f if is_buy else -f)
        return {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'filled': {'totalSz': str(f)}}]}}}


def _cfg(tmp_path):
    cfg = ht.load_config(None)
    cfg.update(weights={'xyz:ORCL': -1.0}, gross_usd=500.0, log_path=str(tmp_path / 'l.jsonl'))
    return cfg


def test_partial_flip_close_skips_open(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    state = {'pos': 5.0}                              # long 5 ($500), target short $500
    ex = FakeEx(fill_frac=0.4)                        # close only 40% fills
    monkeypatch.setattr(ht, 'market', lambda cfg, strict=True: _mk1())
    monkeypatch.setattr(ht, 'positions', lambda cfg, a: ({'xyz:ORCL': state['pos'] + sum(ex.filled)}, {}, {}))
    monkeypatch.setattr(ht.time, 'sleep', lambda s: None)
    plan = ht.build_plan(cfg, _mk1(), {'xyz:ORCL': 5.0})
    assert [o['reduce_only'] for o in plan['orders']] == [True, False]
    probs = ht.execute(cfg, ex, plan, _mk1(), ht.Log(cfg['log_path']), '0x' + '1' * 40)
    assert len(ex.sent) == 1 and ex.sent[0][3] is True    # no opening sell on top of a leftover long
    assert any('partial fill' in p for p in probs) and any('phase 2 (opening orders) skipped' in p for p in probs)


def test_full_flip_close_then_open(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    ex = FakeEx(fill_frac=1.0)
    monkeypatch.setattr(ht, 'market', lambda cfg, strict=True: _mk1())
    monkeypatch.setattr(ht, 'positions', lambda cfg, a: ({'xyz:ORCL': 5.0 + sum(ex.filled)}, {}, {}))
    monkeypatch.setattr(ht.time, 'sleep', lambda s: None)
    plan = ht.build_plan(cfg, _mk1(), {'xyz:ORCL': 5.0})
    probs = ht.execute(cfg, ex, plan, _mk1(), ht.Log(cfg['log_path']), '0x' + '1' * 40)
    assert probs == [] and [(b, s, ro) for c, b, s, ro in ex.sent] == [(False, 5.0, True), (False, 5.0, False)]


def test_rejected_order_reported(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    class Rej(FakeEx):
        def order(self, *a):
            self.sent.append(a)
            return {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'error': 'Insufficient margin'}]}}}
    ex = Rej(1.0)
    monkeypatch.setattr(ht, 'market', lambda cfg, strict=True: _mk1())
    monkeypatch.setattr(ht, 'positions', lambda cfg, a: ({'xyz:ORCL': 0.0}, {}, {}))
    plan = ht.build_plan(cfg, _mk1(), {'xyz:ORCL': 0.0})
    probs = ht.execute(cfg, ex, plan, _mk1(), ht.Log(cfg['log_path']), '0x' + '1' * 40)
    assert any('rejected' in p for p in probs)


def test_flatten_ignores_entry_guards():
    cfg = ht.load_config(None)
    cfg.update(weights={'xyz:ORCL': -1.0}, gross_usd=1e6)    # over the cap
    mk = _mk1()
    mk['legs']['xyz:ORCL']['funding_hourly'] = 0.01          # funding breach
    plan = ht.build_plan(cfg, mk, {'xyz:ORCL': -2.0}, flatten=True)
    assert plan['problems'] == [] and plan['orders'][0]['reduce_only'] and plan['orders'][0]['is_buy']


def test_run_lock(tmp_path):
    with ht.RunLock(tmp_path / '.lock'):
        with pytest.raises(ht.Abort):
            with ht.RunLock(tmp_path / '.lock'):
                pass


def test_failed_close_on_one_leg_blocks_opens_on_others(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.update(weights={'xyz:ORCL': -1.0, 'xyz:B': 1.0}, gross_usd=1000.0, max_total_notional_usd=1100.0)
    mk = _mk1()
    mk['legs']['xyz:B'] = dict(mk['legs']['xyz:ORCL'], asset=110001)
    pos = {'xyz:ORCL': 30.0, 'xyz:B': 0.0}            # big stale long that must be closed first
    sent = []
    monkeypatch.setattr(ht, 'positions', lambda cfg, a: (dict(pos), {}, {}))   # close never fills
    monkeypatch.setattr(ht, 'market', lambda cfg, strict=True: mk)
    monkeypatch.setattr(ht.time, 'sleep', lambda s: None)
    monkeypatch.setattr(ht, '_send', lambda cfg, ex, o, mk, log, lev, probs: sent.append(o))
    plan = ht.build_plan(cfg, mk, dict(pos))
    probs = ht.execute(cfg, None, plan, mk, None, '0x' + '1' * 40)
    assert all(o['reduce_only'] for o in sent) and any('phase 2' in p for p in probs)


def test_flatten_market_skips_leg_with_missing_price(monkeypatch):
    cfg = ht.load_config(None)
    cfg['weights'] = {'xyz:A': 1.0, 'xyz:B': -1.0}
    def fake(url, body=None, **k):
        t = body['type']
        if t == 'perpDexs':
            return [None, {'name': 'xyz'}]
        if t == 'metaAndAssetCtxs':
            u = [{'name': 'xyz:A', 'szDecimals': 2, 'maxLeverage': 10}, {'name': 'xyz:B', 'szDecimals': 2, 'maxLeverage': 10}]
            c = [{'oraclePx': '100', 'markPx': '100', 'funding': '0', 'openInterest': '1'},
                 {'oraclePx': None, 'markPx': '100', 'funding': '0', 'openInterest': '1'}]
            return [{'universe': u}, c]
        return {'xyz:A': '100', 'xyz:B': '100'}
    monkeypatch.setattr(ht, 'http_json', fake)
    with pytest.raises(ht.Abort):
        ht.market(cfg)
    mk = ht.market(cfg, strict=False)
    assert list(mk['legs']) == ['xyz:A'] and any('xyz:B' in w for w in mk['warnings'])
