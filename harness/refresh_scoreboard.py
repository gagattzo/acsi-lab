"""Publish an all-or-nothing research scoreboard from verified canonical inputs.

A blocked collection or score run preserves the last scoreboard byte for byte.
Only a small status report changes; raw data is never written to the scoreboard.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DECODER = 'dtcc-strict-v1'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def source_gate(root, started_at=None):
    path = root / 'data/canonical/cds_source_quality.json'
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, ['source_quality_missing_or_invalid']
    q = doc.get('source_quality', {})
    reasons = []
    if doc.get('decoder_version') != DECODER:
        reasons.append('decoder_version_mismatch')
    if q.get('status') != 'complete' or q.get('calculation_eligible') is not True:
        reasons.append('source_not_complete')
    try:
        generated = dt.datetime.fromisoformat(doc['generated_at'].replace('Z', '+00:00'))
        if generated.tzinfo is None or (started_at and generated < started_at):
            reasons.append('source_quality_not_from_this_attempt')
    except (KeyError, ValueError, TypeError):
        reasons.append('invalid_source_generation_time')
    for name in ('cds_daily.csv', 'cds_trades.csv'):
        table = root / 'data/canonical' / name
        expected = doc.get('canonical_sha256', {}).get(name)
        if not table.exists() or not expected or sha256(table) != expected:
            reasons.append('canonical_hash_mismatch:' + name)
    return doc, reasons


def score_method(root, method, output):
    reg = subprocess.run(['git', 'log', '--diff-filter=A', '--follow', '--format=%as', '--',
                          str(method.relative_to(root))], cwd=root, capture_output=True, text=True, check=False)
    dates = reg.stdout.split()
    args = [sys.executable, 'harness/score.py', str(method), '--json', str(output)]
    if dates:
        args += ['--registered', dates[-1]]
    result = subprocess.run(args, cwd=root, check=False)
    if result.returncode:
        raise ValueError('scoring_failed')
    payload = json.loads(output.read_text())
    if not isinstance(payload.get('method'), dict):
        raise ValueError('invalid_score_output')
    return payload['method']


def refresh(root=ROOT, *, collection_ok=True, started_at=None, scorer=score_method):
    root = Path(root)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    doc, reasons = source_gate(root, started_at)
    if not collection_ok:
        reasons.insert(0, 'source_collection_failed')
    methods = sorted((root / 'methods').glob('*.yaml'))
    method_hashes = {p.name: sha256(p) for p in methods}
    if not methods:
        reasons.append('no_registered_methods')
    rows = []
    if not reasons:
        with tempfile.TemporaryDirectory(prefix='acsi-scores-') as tmp:
            for method in methods:
                try:
                    rows.append(scorer(root, method, Path(tmp) / (method.stem + '.json')))
                except (OSError, ValueError, KeyError):
                    reasons.append('score_failed:' + method.stem)
                    break
    # Recheck after all scorers: a changing file cannot be certified with its earlier hash.
    if not reasons:
        final_doc, changed = source_gate(root, started_at)
        reasons.extend(changed)
        if not changed and final_doc != doc:
            reasons.append('source_changed_during_scoring')
        if method_hashes != {p.name: sha256(p) for p in sorted((root / 'methods').glob('*.yaml'))}:
            reasons.append('methods_changed_during_scoring')
    report = {
        'attempted_at': now, 'status': 'blocked' if reasons else 'published_research',
        'reasons': reasons, 'decoder_version': DECODER,
        'source_status': (doc or {}).get('source_quality', {}).get('status', 'unavailable'),
        'previous_scoreboard_retained': bool(reasons),
        'settlement_eligible': False, 'institutional_use_eligible': False,
    }
    if not reasons:
        write_json(root / 'scoreboard/scoreboard.json', {
            'updated': now[:10], 'generated_at': now,
            'decoder_version': DECODER, 'decoder_status': 'current',
            'validation_scope': 'retrospective_research',
            'source_quality': doc['source_quality'],
            'canonical_sha256': doc['canonical_sha256'],
            'method_sha256': method_hashes,
            'institutional_use_eligible': False, 'settlement_eligible': False,
            'methods': rows,
        })
    write_json(root / 'scoreboard/last_attempt.json', report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--collect', action='store_true')
    ap.add_argument('--days', type=int, default=400)
    args = ap.parse_args()
    started = dt.datetime.now(dt.timezone.utc)
    ok = True
    if args.collect:
        ok = subprocess.run([sys.executable, 'harness/dtcc.py', '--days', str(args.days)], cwd=ROOT).returncode == 0
    report = refresh(collection_ok=ok, started_at=started if args.collect else None)
    print(json.dumps(report, indent=2))
    if report['reasons'] and os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::warning title=Scoreboard refresh blocked::No new scoreboard published; previous results retained. See last_attempt.json and the workflow summary.')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as stream:
            stream.write('\n### Scoreboard refresh: ' + report['status'] + '\n\n')
            stream.write('Previous scoreboard retained. No new research score published.\n' if report['reasons']
                         else 'Published a source-checked retrospective research score; not a benchmark approval.\n')
            stream.write('\nReasons: ' + (', '.join(report['reasons']) or 'none') + '\n')
    # A handled source outage is a blocked refresh, distinct from a broken code test.
    # The machine-readable last_attempt report and workflow summary expose that state.


if __name__ == '__main__':
    main()
