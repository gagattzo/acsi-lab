"""Adversarial snapshot integrity tests. No live endpoint required."""
import asyncio
import gzip
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

MODULE = pathlib.Path(__file__).resolve().parents[1] / 'harness' / 'finra_trace.py'
spec = importlib.util.spec_from_file_location('finra_snapshots', MODULE)
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


def row(**kwargs):
    return {'cusip':'TEST00001','tradeExecutionDate':'2026-11-27','tradeExecutionTime':'12:00:00',
            'lastSalePrice':'90','lastSaleYield':'10','tradeStatus':'M','reportedTradeVolume':'1MM+',
            'contraPartyTypeCode':'C','reportingPartySideCode':'S', **kwargs}


class SnapshotTests(unittest.TestCase):
    def test_distinct_identical_trades_and_correction_versions_not_lossily_deduplicated(self):
        raw = pd.DataFrame([row(), row(), row(lastSalePrice='89', tradeStatus='N')])
        clean = f.clean_trades(raw)
        self.assertEqual(len(clean), 3)
        self.assertEqual(clean.lastSalePrice.tolist(), [90,90,89])
        self.assertEqual(set(clean.correction_status), {'unverified_portal_snapshot'})
        self.assertFalse(clean.settlement_eligible.any())

    def test_volume_caps_are_not_claimed_exact(self):
        self.assertEqual(f.parse_volume('5MM+'), (5_000_000, True))
        self.assertEqual(f.parse_volume('1,000,000'), (1_000_000, False))
        clean = f.clean_trades(pd.DataFrame([row()]))
        self.assertTrue(clean.isVolumeCapped.iloc[0])

    def test_unknown_status_never_silently_guessed_or_dropped(self):
        clean = f.clean_trades(pd.DataFrame([row(tradeStatus='X'),row(tradeStatus='M')]))
        self.assertEqual(len(clean),2)
        self.assertEqual(clean.portal_status_recognized.tolist(),[False,True])

    def test_archive_retains_multiplicity_and_old_revisions(self):
        with tempfile.TemporaryDirectory() as d:
            a,m = f.archive_snapshot(d,'ACSI-BOND-01',[row(),row()], '2026-11-28T08:00:00+00:00')
            b,n = f.archive_snapshot(d,'ACSI-BOND-01',[row(lastSalePrice='88')], '2026-11-28T09:00:00+00:00')
            self.assertNotEqual(m['snapshot_sha256'],n['snapshot_sha256'])
            original=json.loads(gzip.decompress((pathlib.Path(d)/m['snapshot_file']).read_bytes()))
            self.assertEqual(original,[row(),row()])
            self.assertEqual(a.source_row_ordinal.tolist(),[0,1])

    def test_bounded_window_excludes_entire_boundary_date(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[row(tradeExecutionDate='2026-11-25')]*40+[row()]*460
            a,m=f.archive_snapshot(d,'ACSI-BOND-01',rows,'2026-11-28T08:00:00+00:00')
            self.assertTrue(m['truncated_window'])
            self.assertEqual(int(a.history_boundary_excluded.sum()),40)

    def test_empty_or_mixed_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(f.SourceError):f.archive_snapshot(d,'ACSI-BOND-01',[],'2026-11-28')
            with self.assertRaises(f.SourceError):f.archive_snapshot(d,'ACSI-BOND-01',[row(),row(cusip='OTHER')],'2026-11-28')

    def test_no_overlap_flagged_without_identity_claim(self):
        with tempfile.TemporaryDirectory() as d:
            _,m=f.archive_snapshot(d,'ACSI-BOND-01',[row()]*500,'2026-11-28',[row(lastSalePrice='80')])
            self.assertEqual(m['history_quality'],'no_overlap_possible_gap')

    def test_failed_run_keeps_raw_reference_and_independent_success(self):
        class Fake:
            async def start(self): pass
            async def close(self): pass
            async def fetch_reference(self, identifier):
                if identifier=='BAD':raise f.SourceError('HTTP403')
                return {'cusip':'TEST00001','issuerName':'Test'}
            async def fetch_trades(self, identifier):return [row(),row()]
        async def no_wait(*args): pass
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)
            seed=p/'seed.json';seed.write_text(json.dumps([
                {'id':'ACSI-BOND-01','trace_symbol':'GOOD'},
                {'id':'ACSI-BOND-02','trace_symbol':'BAD'}]))
            (p/'bond_reference.json').write_text(json.dumps([{'cusip':'OLD000002'}]))
            pd.DataFrame([row(cusip='OLD000002')]).to_parquet(p/'trace_trades_raw.parquet')
            with patch.object(f.asyncio,'sleep',no_wait):
                with self.assertRaises(f.SourceError):asyncio.run(f.run_pipeline(seed,output_dir=p,client=Fake()))
            raw=pd.read_parquet(p/'trace_trades_raw.parquet')
            self.assertEqual(len(raw),3)
            refs=json.loads((p/'bond_reference.json').read_text())
            self.assertEqual({r['cusip'] for r in refs},{'OLD000002','TEST00001'})
            manifest=json.loads((p/'trace_collection_manifest.json').read_text())
            self.assertEqual(manifest['status'],'incomplete')
            self.assertEqual(manifest['failed_bonds'],['ACSI-BOND-02'])
            self.assertEqual(len(list((p/'snapshots').rglob('*.gz'))),1)

    def test_http403_is_not_retried_and_503_is_retried_once(self):
        class Response:
            def __init__(self,status):self.status=status
        class API:
            def __init__(self,statuses):self.statuses=list(statuses);self.calls=0
            async def get(self,*a,**k):self.calls+=1;return Response(self.statuses.pop(0))
        async def no_wait(*args):pass
        with patch.object(f.asyncio,'sleep',no_wait):
            c=f.FinraTraceClient();c.api_context=API([403])
            with self.assertRaises(f.SourceError):asyncio.run(c._request('get','https://example.invalid'))
            self.assertEqual(c.api_context.calls,1)
            c.api_context=API([503,200])
            self.assertEqual(asyncio.run(c._request('get','https://example.invalid')).status,200)
            self.assertEqual(c.api_context.calls,2)


if __name__ == '__main__':unittest.main()
