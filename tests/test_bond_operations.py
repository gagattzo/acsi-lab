"""Private research, source integrity and retained-publication transition tests."""
import copy
import pandas as pd
import pytest
from harness.bond_spreads import METHOD, apply_publication_gate, private_output_path
from harness.bond_provenance import prepare_private_snapshot, verify_collection
from harness.finra_trace import archive_snapshot, clean_trades


def trade(**kw):
    return {'cusip':'TEST00001','tradeExecutionDate':'2026-11-27','tradeExecutionTime':'12:00:00',
            'lastSalePrice':'90','lastSaleYield':'10','tradeStatus':'M','reportedTradeVolume':'1MM+',
            'contraPartyTypeCode':'C','reportingPartySideCode':'S','reportingPartyTypeCode':'D',**kw}


def collection(tmp_path):
    frame, entry=archive_snapshot(tmp_path,'ACSI-BOND-01',[trade(),trade(),trade()], '2026-11-28T08:00:00+00:00')
    universe=[{'id':'ACSI-BOND-01','cusip':'TEST00001'}]
    manifest={'status':'research_snapshot','expected_bonds':1,'bonds':[entry], 'failed_bonds':[],
              'fetched_at':'2026-11-28T08:00:00+00:00','completed_at':'2026-11-28T08:01:00+00:00'}
    return clean_trades(frame),universe,manifest


def computed(day='2026-11-27',status='complete_research'):
    return {'date':day,'method_version':METHOD,'status':status,
            'g_spread_equal_weighted_bp':501.0 if status=='complete_research' else None,
            'g_spread_issuer_capped_bp':502.0 if status=='complete_research' else None}


def test_snapshot_verification_checks_materialized_values_and_multiplicity(tmp_path):
    frame,u,m=collection(tmp_path)
    assert verify_collection(frame,u,m,tmp_path)['status']=='verified_snapshot'
    changed=frame.copy();changed.loc[0,'lastSaleYield']=11
    assert 'materialized_snapshot_mismatch' in verify_collection(changed,u,m,tmp_path)['blockers']
    assert 'materialized_snapshot_mismatch' in verify_collection(frame.iloc[:-1],u,m,tmp_path)['blockers']


def test_success_label_does_not_replace_archive_evidence(tmp_path):
    frame,u,m=collection(tmp_path)
    (tmp_path/m['bonds'][0]['snapshot_file']).unlink()
    assert verify_collection(frame,u,m,tmp_path)['status']=='unverified'
    assert verify_collection(frame,u,{'status':'ok'},tmp_path)['status']=='failed'


def test_universe_and_hash_tampering_are_blocked(tmp_path):
    frame,u,m=collection(tmp_path)
    bad=copy.deepcopy(m);bad['expected_bonds']=2
    assert 'snapshot_universe_mismatch' in verify_collection(frame,u,bad,tmp_path)['blockers']
    bad=copy.deepcopy(m);bad['bonds'][0]['snapshot_sha256']='f'*64
    assert 'snapshot_integrity_error' in verify_collection(frame,u,bad,tmp_path)['blockers']


def test_path_escape_and_missing_provenance_block_publication(tmp_path):
    frame,u,m=collection(tmp_path)
    bad=copy.deepcopy(m);bad['bonds'][0]['snapshot_file']='../outside.gz'
    assert 'snapshot_integrity_error' in verify_collection(frame,u,bad,tmp_path)['blockers']
    assert 'materialized_provenance_missing' in verify_collection(frame.drop(columns=['source_row_ordinal']),u,m,tmp_path)['blockers']


def test_collection_timestamp_cannot_impersonate_a_new_source(tmp_path):
    frame,u,m=collection(tmp_path)
    m['fetched_at']='2026-11-28T09:00:00+00:00'
    result=verify_collection(frame,u,m,tmp_path)
    assert 'collection_timestamps_unverified' in result['blockers']
    assert 'snapshot_integrity_error' in result['blockers']


def test_failed_collection_does_not_conflate_calculation_with_source():
    row=apply_publication_gate([computed()],{},['collection_error'])[0]
    assert row['calculation_status']=='complete_research'
    assert row['status']=='collection_error'
    assert row['g_spread_issuer_capped_bp'] is None
    assert not row['publication_eligible']


def test_failed_refresh_retains_original_accepted_observation_and_time():
    prior={**computed(),'publication_eligible':True,'source_fetched_at':'old-source'}
    revised={**computed(),'g_spread_issuer_capped_bp':777.0}
    row=apply_publication_gate([revised],{prior['date']:prior},['collection_error'])[0]
    assert row['g_spread_issuer_capped_bp']==502
    assert row['source_fetched_at']=='old-source'
    assert row['retained_after_source_failure']
    assert row['refresh_blockers']==['collection_error']
    assert not row['settlement_eligible']


def test_private_replay_keeps_calculated_value_without_authorizing_publication():
    rows=apply_publication_gate([computed(),computed('2026-11-25','incomplete_coverage')],{},['collection_provenance_unverified'],True)
    assert rows[0]['g_spread_issuer_capped_bp']==502
    assert rows[1]['g_spread_issuer_capped_bp'] is None
    assert all(r['private_candidate'] and not r['publication_eligible'] and not r['settlement_eligible'] for r in rows)


def test_private_replay_excludes_entire_truncated_boundary_and_preserves_duplicates():
    df=pd.DataFrame([trade(tradeExecutionDate='2026-11-25')]*40+[trade()]*460)
    clean=prepare_private_snapshot(df)
    assert len(clean)==500
    assert int(clean.history_boundary_excluded.sum())==40
    with pytest.raises(ValueError):prepare_private_snapshot(pd.concat([df,df],ignore_index=True))


def test_private_replay_cannot_overwrite_public_static_directory():
    from harness.bond_spreads import ROOT
    with pytest.raises(ValueError):private_output_path(ROOT/'data')
    with pytest.raises(ValueError):private_output_path(ROOT/'data'/'replay')
    assert private_output_path(ROOT/'private_data'/'replay').name=='replay'


def test_private_cli_produces_values_with_failed_public_rights_gate(tmp_path,monkeypatch,capsys):
    import json,sys
    from harness import bond_spreads as b
    raw=tmp_path/'raw.parquet';pd.DataFrame([trade(),trade(),trade()]).to_parquet(raw)
    universe=tmp_path/'universe.json';universe.write_text(json.dumps([{'id':'ACSI-BOND-01','cusip':'TEST00001','issuer':'Test','coupon':6,'maturity':'2031-01-01'}]))
    curve=tmp_path/'treasury.csv';curve.write_text('Date,1 Yr,5 Yr,10 Yr\n11/27/2026,4,4,4\n')
    out=tmp_path/'private'
    monkeypatch.setattr(sys,'argv',['bond_spreads.py','--private-replay','--trades',str(raw),'--universe',str(universe),'--as-of','2026-11-27','--treasury-file',str(curve),'--default-events',str(b.ROOT/'config/default_events.json'),'--out',str(out)])
    b.main()
    manifest=json.loads((out/'acsi_bond_manifest.json').read_text())
    rows=json.loads((out/'acsi_bond_spreads_daily.json').read_text())
    assert manifest['status']=='private_replay'
    assert manifest['source_fetched_at'] is None
    assert manifest['last_successful_collection_at'] is None
    assert rows[-1]['g_spread_issuer_capped_bp']==600
    assert not rows[-1]['publication_eligible'] and not manifest['publication_eligible']
    assert 'publication_rights_unverified' in manifest['publication_blockers']


def test_public_rights_gate_requires_separate_scoped_review_record():
    from harness.bond_provenance import publication_authorization
    assert publication_authorization(None,'2026-11-27')['status']=='unverified'
    assert publication_authorization({'licensed':True},'2026-11-27')['status']=='unverified'
    record={'record_id':'synthetic-test','reviewed_by':'test-reviewer','reviewed_at':'2026-11-28T12:00:00+00:00',
            'evidence_reference':'test-fixture-only','valid_from':'2026-01-01','valid_through':'2026-12-31',
            'source_id':'finra_portal_snapshot','method_version':METHOD,'scope':'public_derived_research'}
    assert publication_authorization(record,'2026-11-27')['status']=='recorded_authorization'
    assert publication_authorization({**record,'scope':'perp_settlement'},'2026-11-27')['status']=='unverified'
    assert publication_authorization(record,'2027-01-01')['status']=='unverified'


def test_retained_public_inputs_stay_paired_with_retained_values(tmp_path):
    import json
    from harness.bond_spreads import export_public_safe_datasets
    original={'date':'2026-11-27','method_version':METHOD,'id':'ACSI-BOND-01','spread_bp':500}
    (tmp_path/'acsi_bond_inputs_daily.json').write_text(json.dumps([original]))
    series=[{**computed(),'retained_after_source_failure':True}]
    info={'TEST00001':{'issuer_group':'Test','daily_inputs':[{**original,'spread_bp':777}]}}
    universe=[{'id':'ACSI-BOND-01','cusip':'TEST00001','issuer':'Test','coupon':6,'maturity':'2031-01-01'}]
    export_public_safe_datasets(series,{},info,universe,tmp_path,'2026-11-27')
    assert json.loads((tmp_path/'acsi_bond_inputs_daily.json').read_text())==[original]
