"""Append-only, hash-chained record of Stack editions (one JSON line per edition).

  python -m stack.track [--as-of D] [--history PATH] [--layers L2] [--offline] [--config PATH]
  python -m stack.track --verify [--history PATH]

Each line: seq, as_of, built_at (UTC), method {name, version}, config_sha256, code_commit (git HEAD or null), inputs
(raw files with sha256), output (the edition from stack.build), prev_hash, hash = sha256 of the canonical JSON of the
line without `hash`. A correction adds correction_of: <seq of the first line for that as_of>.

  - same as_of, same output and inputs as its latest line: nothing is appended
  - same as_of, anything different: a new line with correction_of; no line is ever edited or removed
  - --verify walks the chain (seq, prev_hash, hash, correction_of) and exits 1 on the first break
"""
import argparse, datetime as dt, hashlib, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HISTORY = ROOT / 'data' / 'stack' / 'editions.jsonl'

try:
    import fcntl
except ImportError:  # pragma: no cover - not on Linux/macOS
    fcntl = None


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def line_hash(rec):
    return hashlib.sha256(canonical({k: v for k, v in rec.items() if k != 'hash'}).encode('utf-8')).hexdigest()


def code_commit(root=ROOT):
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, timeout=10)
        h = r.stdout.strip()
        return h if r.returncode == 0 and h else None
    except (OSError, subprocess.SubprocessError):
        return None


def read(path):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]


def verify(path):
    """List of problems (empty = the chain is intact)."""
    p = pathlib.Path(path)
    if not p.exists():
        return []
    errs, prev, first = [], None, {}
    for n, text in enumerate(p.read_text(encoding='utf-8').splitlines(), start=1):
        if not text.strip():
            errs.append(f'line {n}: blank line')
            continue
        try:
            rec = json.loads(text)
        except json.JSONDecodeError as e:
            errs.append(f'line {n}: not JSON ({e})')
            break
        if rec.get('seq') != n:
            errs.append(f'line {n}: seq {rec.get("seq")} != {n}')
        if rec.get('prev_hash') != prev:
            errs.append(f'line {n}: prev_hash does not match the hash of line {n - 1}')
        if rec.get('hash') != line_hash(rec):
            errs.append(f'line {n}: hash does not match its content (edited?)')
        c = rec.get('correction_of')
        if c is not None and first.get(rec.get('as_of')) != c:
            errs.append(f'line {n}: correction_of {c} is not the first line for as_of {rec.get("as_of")}')
        if c is None and rec.get('as_of') in first:
            errs.append(f'line {n}: second line for as_of {rec.get("as_of")} without correction_of')
        first.setdefault(rec.get('as_of'), rec.get('seq'))
        prev = rec.get('hash')
        if errs:
            break
    return errs


def record(output, inputs, path=HISTORY, config_sha256=None, commit=None, now=None):
    """Append the edition unless it is already recorded. Returns (line appended or None, reason)."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'a+', encoding='utf-8') as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            errs = verify(p)
            if errs:
                raise SystemExit('history chain is broken, refusing to append:\n  ' + '\n  '.join(errs))
            lines = read(p)
            as_of = output['as_of']
            same = [r for r in lines if r['as_of'] == as_of]
            if same:
                last = same[-1]
                if canonical(last['output']) == canonical(output) and canonical(last['inputs']) == canonical(inputs):
                    return None, f'as_of {as_of} already recorded with the same output and inputs (seq {last["seq"]})'
            rec = {'seq': len(lines) + 1, 'as_of': as_of,
                   'built_at': (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec='seconds'),
                   'method': {'name': output['method']['name'], 'version': output['method']['version']},
                   'config_sha256': config_sha256 or output.get('config_sha256'), 'code_commit': commit,
                   'inputs': inputs, 'output': output, 'prev_hash': lines[-1]['hash'] if lines else None}
            if same:
                rec['correction_of'] = same[0]['seq']
                changed = [k for k in ('output', 'inputs') if canonical(same[-1][k]) != canonical(rec[k])]
                rec['corrected'] = changed
            rec['hash'] = line_hash(rec)
            f.seek(0, os.SEEK_END)
            f.write(canonical(rec) + '\n')
            f.flush()
            os.fsync(f.fileno())
            why = f'correction of seq {rec["correction_of"]} ({", ".join(rec["corrected"])} changed)' if same else 'new edition'
            return rec, why
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m stack.track', description=__doc__.split('\n')[0])
    ap.add_argument('--as-of', help='YYYY-MM-DD (default: today, UTC)')
    ap.add_argument('--history', default=str(HISTORY), help='JSONL file (default data/stack/editions.jsonl)')
    ap.add_argument('--verify', action='store_true', help='check the chain and exit')
    ap.add_argument('--layers', help='comma-separated, e.g. L2 (default: all)')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--config')
    a = ap.parse_args(argv)
    if a.verify:
        errs = verify(a.history)
        for e in errs:
            print(e, file=sys.stderr)
        print(f'{len(read(a.history)) if not errs else "?"} lines, chain ok' if not errs else 'chain BROKEN')
        return 1 if errs else 0
    from . import build
    as_of = a.as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()
    out, inputs = build.run(as_of, a.layers.split(',') if a.layers else None, a.offline, a.config)
    rec, why = record(out, inputs, a.history, out['config_sha256'], code_commit())
    print(f'{why}' + (f': seq {rec["seq"]}, hash {rec["hash"][:16]}' if rec else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
