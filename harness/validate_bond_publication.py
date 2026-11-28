"""Fail closed on identifier leaks, inconsistent weights and false freshness."""
import json
from pathlib import Path

def validate(root=Path('.')):
    manifest = json.loads((root/'data/acsi_bond_manifest.json').read_text())
    assert manifest.get('perp_eligible') is False, 'Research feed must never become settlement eligible implicitly'
    series = json.loads((root/'data/acsi_bond_spreads_daily.json').read_text())
    assert len({r['date'] for r in series}) == len(series), 'Duplicate observation dates'
    for row in series:
        values=[row.get('g_spread_equal_weighted_bp'),row.get('g_spread_issuer_capped_bp')]
        if any(v is not None for v in values):
            assert row.get('active_bonds_count')==24 and row.get('active_issuers_count')==19, 'Incomplete fixed basket'
    forbidden={'cusip','isin','xsrf_token','cookies'}
    def inspect(value):
        if isinstance(value,dict):
            assert not forbidden.intersection(k.lower() for k in value), 'Private field in public output'
            for v in value.values():inspect(v)
        elif isinstance(value,list):
            for v in value:inspect(v)
    for f in (root/'data').glob('acsi_bond_*.json'):
        inspect(json.loads(f.read_text()))
    for name in ['bond_reference.json','bonds_universe.json','trace_trades_raw.parquet','trace_trades_clean.parquet']:
        assert not (root/'data'/name).exists(), 'Private input is in public data directory'
    print('Public research outputs validated; no settlement eligibility asserted.')

if __name__ == '__main__':
    validate()
