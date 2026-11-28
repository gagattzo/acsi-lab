"""Evidence checks for bounded portal snapshots; never verifies TRACE lifecycle.

Local hashes prove which bytes were calculated, not source authenticity or rights.
Private replay remains useful without this evidence, but cannot publish a fixing.
"""
import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from .finra_trace import TRADE_FIELDS, WINDOW_LIMIT, clean_trades
except ImportError:
    from finra_trace import TRADE_FIELDS, WINDOW_LIMIT, clean_trades

SUCCESS_STATES = {'research_snapshot'}


def publication_authorization(record, observation_date):
    """Validate an operator's rights-review record, not interpret a license.

    Only a separately supplied, reviewed authorization can open public research
    publication. Source-response claims and a boolean cannot grant that authority.
    This scope never authorizes sale, benchmark licensing or derivative settlement.
    """
    required = {'record_id', 'reviewed_by', 'reviewed_at', 'evidence_reference',
                'valid_from', 'valid_through', 'source_id', 'method_version', 'scope'}
    try:
        if not isinstance(record, dict) or not required.issubset(record):
            raise ValueError('missing rights record')
        if any(not isinstance(record[k], str) or not record[k].strip() for k in required):
            raise ValueError('invalid rights record')
        if (record['source_id'] != 'finra_portal_snapshot'
                or record['method_version'] != 'bond-research-v1'
                or record['scope'] != 'public_derived_research'):
            raise ValueError('scope mismatch')
        day = date.fromisoformat(observation_date)
        if not date.fromisoformat(record['valid_from']) <= day <= date.fromisoformat(record['valid_through']):
            raise ValueError('authorization outside period')
        reviewed = datetime.fromisoformat(record['reviewed_at'].replace('Z', '+00:00'))
        if reviewed.tzinfo is None or reviewed > datetime.now(timezone.utc):
            raise ValueError('review timestamp timezone missing or in future')
    except (TypeError, ValueError, KeyError):
        return {'status': 'unverified', 'blockers': ['publication_rights_unverified']}
    return {'status': 'recorded_authorization', 'blockers': [],
            'record_sha256': hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def prepare_private_snapshot(raw):
    """One bounded snapshot per issue, retaining multiplicity and boundary flags."""
    if raw.empty or not {'cusip', 'tradeExecutionDate'}.issubset(raw.columns):
        raise ValueError('A nonempty snapshot with security and date fields is required')
    counts = raw.groupby('cusip').size()
    if counts.gt(WINDOW_LIMIT).any():
        raise ValueError('Multiple/oversized snapshots require an explicit validated adapter')
    clean = clean_trades(raw)
    earliest = clean.groupby('cusip').tradeExecutionDate.transform('min')
    boundary = clean.cusip.map(counts).ge(WINDOW_LIMIT) & clean.tradeExecutionDate.eq(earliest)
    if 'history_boundary_excluded' in clean:
        boundary |= clean.history_boundary_excluded.fillna(False).eq(True)
    clean['history_boundary_excluded'] = boundary
    return clean


def verify_collection(df, universe, manifest, source_dir):
    """Bind every materialized row to an archived snapshot, failing closed.

    A 'success' string alone is insufficient. Archive hashes, constituent identity,
    complete materialization and all fields used by the calculator must agree.
    """
    if not manifest:
        return {'status': 'unverified', 'blockers': ['collection_provenance_unverified']}
    if manifest.get('status') not in SUCCESS_STATES:
        return {'status': 'failed', 'blockers': ['collection_error']}
    errors = []
    expected = {b.get('id', f'ACSI-BOND-{i:02d}'): b for i, b in enumerate(universe, 1)}
    entries = manifest.get('bonds', [])
    ids = [e.get('id') for e in entries]
    if (len(ids) != len(set(ids)) or set(ids) != set(expected)
            or manifest.get('expected_bonds') != len(expected) or manifest.get('failed_bonds')):
        errors.append('snapshot_universe_mismatch')
    try:
        fetched = datetime.fromisoformat(manifest['fetched_at'].replace('Z', '+00:00'))
        completed = datetime.fromisoformat(manifest['completed_at'].replace('Z', '+00:00'))
        if fetched.tzinfo is None or completed.tzinfo is None or fetched > completed or completed > datetime.now(timezone.utc):
            raise ValueError('invalid collection chronology')
    except (KeyError, TypeError, ValueError, AttributeError):
        errors.append('collection_timestamps_unverified')
    required = set(TRADE_FIELDS) | {'source_snapshot_id', 'source_fetched_at',
                                  'source_row_ordinal', 'history_boundary_excluded'}
    if not required.issubset(df.columns):
        errors.append('materialized_provenance_missing')
    rebuilt = []
    root = Path(source_dir).resolve()
    for entry in entries:
        try:
            if entry.get('status') != 'ok' or entry.get('id') not in expected:
                raise ValueError('invalid entry')
            if entry['fetched_at'] != manifest.get('fetched_at'):
                raise ValueError('snapshot and collection timestamps disagree')
            path = (root / entry['snapshot_file']).resolve()
            if not path.is_relative_to(root / 'snapshots'):
                raise ValueError('unsafe snapshot path')
            raw = gzip.decompress(path.read_bytes())
            digest = hashlib.sha256(raw).hexdigest()
            if digest != entry['snapshot_sha256']:
                raise ValueError('snapshot hash mismatch')
            rows = json.loads(raw)
            if not rows or len(rows) != entry['records'] or len(rows) > WINDOW_LIMIT:
                raise ValueError('snapshot size mismatch')
            cusip = expected[entry['id']]['cusip']
            if any(r.get('cusip') != cusip for r in rows):
                raise ValueError('snapshot security mismatch')
            dates = [r['tradeExecutionDate'] for r in rows]
            frame = pd.DataFrame(rows)
            frame['source_snapshot_id'] = digest
            frame['source_fetched_at'] = entry['fetched_at']
            frame['source_row_ordinal'] = range(len(frame))
            frame['history_boundary_excluded'] = (len(rows) >= WINDOW_LIMIT) & frame.tradeExecutionDate.eq(min(dates))
            if (entry.get('earliest_execution_date') != min(dates)
                    or entry.get('latest_execution_date') != max(dates)
                    or entry.get('truncated_window') is not (len(rows) >= WINDOW_LIMIT)):
                raise ValueError('snapshot coverage mismatch')
            rebuilt.append(clean_trades(frame))
        except (OSError, ValueError, KeyError, TypeError):
            errors.append('snapshot_integrity_error')
    if rebuilt and not errors:
        expected_df = pd.concat(rebuilt, ignore_index=True)
        columns = sorted(required)
        keys = ['source_snapshot_id', 'source_row_ordinal']
        try:
            pd.testing.assert_frame_equal(
                df[columns].sort_values(keys).reset_index(drop=True),
                expected_df[columns].sort_values(keys).reset_index(drop=True),
                check_dtype=False, check_exact=True)
        except (AssertionError, TypeError, ValueError):
            errors.append('materialized_snapshot_mismatch')
    if not rebuilt:
        errors.append('snapshots_missing')
    return {'status': 'verified_snapshot' if not errors else 'unverified',
            'blockers': sorted(set(errors)),
            'snapshot_hashes': sorted(e.get('snapshot_sha256', '') for e in entries) if not errors else []}
