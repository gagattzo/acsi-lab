"""Fail closed on identifier leaks, inconsistent weights and false freshness."""
import json
import math
from pathlib import Path
try:
    from .bond_publication import MARK_FIELDS, public_market_data_authorized
except ImportError:
    from bond_publication import MARK_FIELDS, public_market_data_authorized

def validate(root=Path('.')):
    manifest = json.loads((root/'data/acsi_bond_manifest.json').read_text())
    assert manifest.get('perp_eligible') is False, 'Research feed must never become settlement eligible implicitly'
    assert manifest.get('settlement_eligible') is False, 'Research feed must remain settlement ineligible'
    assert not manifest.get('private_candidate'), 'Private replay cannot be served as public data'
    if manifest.get('publication_eligible'):
        assert manifest.get('collection_verification_status') == 'verified_snapshot', 'Snapshot evidence not verified'
        assert manifest.get('calculation_status') == 'complete_research', 'Incomplete calculation'
        assert manifest.get('source_fetched_at'), 'Missing successful source timestamp'
        assert manifest.get('publication_authorization_status') == 'recorded_authorization', 'Public research rights record missing'
        assert not manifest.get('publication_blockers'), 'Blocked observation marked publishable'
    series = json.loads((root/'data/acsi_bond_spreads_daily.json').read_text())
    assert len({r['date'] for r in series}) == len(series), 'Duplicate observation dates'
    for row in series:
        values=[row.get('g_spread_equal_weighted_bp'),row.get('g_spread_issuer_capped_bp')]
        assert row.get('settlement_eligible') is False, 'Research row marked settlement eligible'
        assert not row.get('private_candidate'), 'Private candidate leaked to public output'
        if any(v is not None for v in values):
            assert all(isinstance(v,(float,int)) and math.isfinite(v) for v in values), 'Nonfinite or partial pair of levels'
            assert row.get('publication_eligible') is True, 'Unverified candidate level in public output'
            assert row.get('status') == 'complete_research', 'Invalid public calculation status'
            assert row.get('active_bonds_count')==manifest['basket_size'] and row.get('active_issuers_count')==manifest['issuer_count'], 'Incomplete fixed basket'
    forbidden={'cusip','isin','xsrf_token','cookies'}
    def inspect(value):
        if isinstance(value,dict):
            assert not forbidden.intersection(k.lower() for k in value), 'Private field in public output'
            for v in value.values():inspect(v)
        elif isinstance(value,list):
            for v in value:inspect(v)
    for f in (root/'data').rglob('*.json'):
        data=json.loads(f.read_text())
        inspect(data)
        def inspect_marks(value):
            if isinstance(value,list):
                for item in value: inspect_marks(item)
            elif isinstance(value,dict):
                marks=[value[k] for k in MARK_FIELDS.intersection(value) if value[k] is not None]
                if marks:
                    assert f.parent==root/'data' and f.name in {'acsi_bond_spreads_daily.json','acsi_bond_inputs_daily.json','acsi_bond_universe_latest.json'}, 'Unreviewed public bond-value archive'
                    assert value.get('publication_eligible') is True, 'Individual or historical bond values lack publication approval'
                    days=[value.get('date') or manifest.get('expected_session') or manifest.get('as_of')]
                    if f.name=='acsi_bond_inputs_daily.json': days.append(value.get('source_date'))
                    if f.name=='acsi_bond_universe_latest.json': days.append(value.get('last_trade_date'))
                    assert public_market_data_authorized(manifest,*days), 'Bond values outside verified public-rights scope'
                    assert all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in marks), 'Nonfinite bond market value'
                for item in value.values(): inspect_marks(item)
        inspect_marks(data)
    for name in ['bond_reference.json','bonds_universe.json','trace_trades_raw.parquet','trace_trades_clean.parquet']:
        assert not (root/'data'/name).exists(), 'Private input is in public data directory'
    print('Public research outputs validated; no settlement eligibility asserted.')

if __name__ == '__main__':
    validate()
