"""Coordinate isolated collection and encrypted audit preservation at zero data cost."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from archive_sources import seal
from bond_spreads import expected_session, atomic_json

ROOT = Path(__file__).resolve().parent.parent

def main():
    os.chdir(ROOT)
    private = ROOT / 'private_data'
    private.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    attempted_at = datetime.now(timezone.utc).isoformat()
    rc = 1
    try:
        with (private / 'collection.log').open('w') as log:
            rc = subprocess.run([sys.executable, 'harness/finra_trace.py'], stdout=log, stderr=subprocess.STDOUT).returncode
        with (private / 'calculation.log').open('w') as log:
            calc = subprocess.run([sys.executable, 'harness/bond_spreads.py'], stdout=log, stderr=subprocess.STDOUT).returncode
        manifest = ROOT / 'data/acsi_bond_manifest.json'
        if rc or calc:
            m = json.loads(manifest.read_text()) if manifest.exists() else {}
            stamp_time=datetime.now(timezone.utc).isoformat()
            failure='source_error' if rc else 'calculation_error'
            try:
                expected=expected_session()
            except (ValueError, IndexError):
                expected=None
                failure='calendar_unverified'
            m.update({'status': failure, 'quality_status': failure,
                      'generated_at': stamp_time, 'updated_at': stamp_time,
                      'last_collection_attempt_at': attempted_at,
                      'collection_status': 'failed' if rc else m.get('collection_status', 'unverified'),
                      'expected_session': expected, 'expected_market_date': expected,
                      'publication_eligible': False, 'perp_eligible': False, 'settlement_eligible': False,
                      'update_error': 'Market calendar requires verification' if failure=='calendar_unverified' else 'Source collection incomplete' if rc else 'Research calculation incomplete'})
            if calc and calc != 2:
                m['calculation_status'] = 'calculation_error'
            if rc:
                m['collection_verification_status'] = 'failed'
                # Never allow a failed attempt timestamp to stand for successful data.
                m['source_fetched_at'] = m.get('source_fetched_at') if m.get('last_successful_collection_at') else None
            m['warnings']=list(dict.fromkeys(m.get('warnings',[])+[m['update_error']]))
            atomic_json(manifest,m)
        return 1 if rc or calc else 0
    finally:
        seal(private, ROOT/'config/archive_public.pem', ROOT/'source_archives'/f'{stamp}.enc.json')

if __name__ == '__main__':
    raise SystemExit(main())
