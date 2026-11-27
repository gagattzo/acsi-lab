"""Run: python -m hl.testnet_check (needs requirements-hl.txt).
Optional: send one IOC order to HL TESTNET from a throwaway, unfunded key generated here.
Expected: rejection naming the recovered signer address. Proves the action wire format, HIP-3 asset id and signature
are accepted by the server. Never touches mainnet and uses no key of ours."""
import sys, pathlib
from eth_account import Account
from hyperliquid.exchange import Exchange
from hl import tracker as ht

cfg = ht.load_config(None); cfg['network'] = 'testnet'; cfg['max_mid_oracle_dev'] = 0.5; cfg['weights'] = {'xyz:ORCL': -1.0, 'xyz:XYZ100': 1.0}  # testnet: thin books, NBIS delisted
mk = ht.market(cfg)
acct = Account.create()
ex = Exchange(acct, ht.URLS['testnet'], perp_dexs=['', 'xyz'])
ht.check_sdk_assets(ex.info, mk)
L = mk['legs']['xyz:ORCL']
print('testnet xyz dex index', mk['dex_index'], 'ORCL asset', L['asset'], 'SDK agrees')
px = ht.bl.limit_price(L['mid'], False, 0.005, L['sz_decimals'])
r = ex.order('xyz:ORCL', False, 0.1, px, {'limit': {'tif': 'Ioc'}}, False)
print('throwaway address', acct.address)
print('response', r)
print('signer recovered by server matches' if acct.address.lower() in str(r).lower() else 'check response manually')
