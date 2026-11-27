"""Probe the FINRA API (the official programmatic route to TRACE data) with Public credentials.

  FINRA_CLIENT_ID=... FINRA_CLIENT_SECRET=... python research/tools/finra_probe.py [issuer]

Reads credentials from the environment only (never from files or arguments), gets an OAuth token, and reports which
FixedIncomeMarket datasets the credential can read, with their fields and a few rows for one issuer. Writes nothing
to the repository: FINRA data may be redistributed only as the API's Fixed Income terms allow (non-commercial
end users, FINRA attributed, no fee), so raw rows stay out of git.
"""
import base64, json, os, sys, urllib.error, urllib.request

TOKEN_URL = 'https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials'
API = 'https://api.finra.org'
CANDIDATES = ['corporateAndAgencyTradeActivity', 'corporateAndAgencySecurities', 'corporateDebtMarketBreadth',
              'corporate144ADebtMarketBreadth', 'corporate144ADebtMarketSentiment']


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    cid, sec = os.environ.get('FINRA_CLIENT_ID'), os.environ.get('FINRA_CLIENT_SECRET')
    if not cid or not sec:
        sys.exit('Set FINRA_CLIENT_ID and FINRA_CLIENT_SECRET (a Public credential from the FINRA API Console).')
    basic = base64.b64encode(f'{cid}:{sec}'.encode()).decode()
    st, body = call(TOKEN_URL, data=b'', headers={'Authorization': 'Basic ' + basic}, method='POST')
    if st != 200:
        sys.exit(f'token request failed: HTTP {st}')
    token = json.loads(body)['access_token']
    auth = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json'}
    issuer = sys.argv[1] if len(sys.argv) > 1 else 'COREWEAVE'
    for name in CANDIDATES:
        st, meta = call(f'{API}/metadata/group/fixedIncomeMarket/name/{name}', headers=auth)
        print(f'\n== {name}: metadata HTTP {st}')
        if st == 200:
            m = json.loads(meta)
            print('fields:', [f.get('name') for f in m.get('fields', [])])
            q = json.dumps({'limit': 5, 'compareFilters': [{'compareType': 'EQUAL', 'fieldName': 'issuerName', 'fieldValue': issuer}]}).encode()
            st, rows = call(f'{API}/data/group/fixedIncomeMarket/name/{name}', data=q, headers=auth, method='POST')
            print(f'rows for {issuer}: HTTP {st}', rows[:600].decode(errors='replace'))


if __name__ == '__main__':
    main()
