"""Offline, hash-checked Python/JavaScript decoder parity on a bounded raw window.

This does not download, publish raw records, or establish point-in-time history.
All inputs must already exist locally and match the lab's committed manifest.
Example:
  python research/tools/validate_strict_decoder.py --raw-dir /private/dtcc \
    --site-dir /checkout/site --output-dir /private/validation --baseline-ref HEAD
"""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
ENTITIES = ('ORCL', 'CRWV', 'META')

NODE_REPLAY = r'''
const fs=require('node:fs'), path=require('node:path');
const cfg=JSON.parse(fs.readFileSync(0,'utf8'));
const t=require(path.join(cfg.site_dir,'api/cds.js'))._test;
const files=cfg.files.map(f=>({date:f.date,slim:t.slim(t.parseCSV(t.unzipFirst(fs.readFileSync(f.path)).toString('utf8')))}));
const byDay=t.ledger(files), daily=[], quality=[];
let prev={};
const dates=[...new Set(files.map(f=>f.date).concat([...byDay.keys()].filter(d=>d>=files[0].date && d<=files.at(-1).date)))].sort();
for(const date of dates){
  const out=t.levelsForDay(byDay.get(date)||[],date,prev,true);
  for(const entity of ['ORCL','CRWV','META']){
    const x=out[entity];
    if(x){daily.push({date,entity,bp:x.bp1,trades:x.trades,kind:x.kind,quoted:x.quoted,
      quoted_bp:x.quoted_bp,upfront_implied_bp:x.upfront_implied_bp,
      ambiguous:x.ambiguous,sign_from_reference:x.sign_from_reference,basket_legs:x.basket_legs});prev[entity]=x;}
    quality.push({date,entity,...((out._quality||{})[entity]||{})});
  }
}
process.stdout.write(JSON.stringify({daily,quality,ledger_quality:byDay.quality||null}));
'''


def load_current():
    sys.path.insert(0, str(ROOT / 'harness'))
    spec = importlib.util.spec_from_file_location('strict_validation_dtcc', ROOT / 'harness/dtcc.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_baseline(ref):
    source = subprocess.run(['git', '-C', str(ROOT), 'show', f'{ref}:harness/dtcc.py'],
                            check=True, capture_output=True, text=True).stdout
    module = types.ModuleType('strict_validation_baseline')
    module.__file__ = str(ROOT / 'harness/dtcc.py')
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module, hashlib.sha256(source.encode()).hexdigest()


def py_replay(module, files, rows, include_quality=True, ledger_override=None):
    by_day = ledger_override or module.ledger([(f['date'], module.slim(rows[f['date']])) for f in files])
    dates = sorted(set(f['date'] for f in files) | {d for d in by_day if files[0]['date'] <= d <= files[-1]['date']})
    daily, quality, prev = [], [], {}
    for date in dates:
        diagnostics = {}
        args = {'cleaned': True}
        if include_quality:
            args['quality'] = diagnostics
        levels, _ = module.parse_day(by_day.get(date, []), date, prev, **args)
        for entity in ENTITIES:
            x = levels.get(entity)
            if x:
                daily.append({'date': date, 'entity': entity, 'bp': x['official_bp'],
                              'trades': x['official_trades'], 'kind': x['official_kind'],
                              'quoted': x['quoted_trades'], 'quoted_bp': x['quoted_median_bp'],
                              'upfront_implied_bp': x['upfront_implied_bp'],
                              'ambiguous': x['n_ambiguous'], 'sign_from_reference': x['n_sign_from_reference'],
                              'basket_legs': x['n_basket_legs']})
                prev[entity] = x['official_bp']
            quality.append({'date': date, 'entity': entity, **diagnostics.get(entity, {})})
    return {'daily': daily, 'quality': quality, 'ledger_quality': getattr(by_day, 'quality', None)}, by_day


def differences(a, b, path='', tolerance=0.1000001):
    """Compare all fields; tolerate only the published tenth-bp rounding boundary."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append({'field': path+'.'+key, 'python': a.get(key), 'javascript': b.get(key)})
            else:
                out.extend(differences(a[key], b[key], path+'.'+key, tolerance))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [{'field': path+'.length', 'python': len(a), 'javascript': len(b)}]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f'{path}[{i}]', tolerance)]
    # Counts and booleans must be exact; only bps and candidate interval coordinates permit rounding.
    rounding = path.endswith(('.bp', '.quoted_bp', '.upfront_implied_bp', '.low', '.high'))
    if rounding and isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        equal = abs(a-b) <= tolerance
    else:
        equal = a == b
    return [] if equal else [{'field': path, 'python': a, 'javascript': b}]


def summary(replay):
    result = {}
    for entity in ENTITIES:
        series = [r for r in replay['daily'] if r['entity'] == entity]
        quality = [r for r in replay['quality'] if r['entity'] == entity]
        counters = collections.Counter()
        for row in quality:
            for key, value in row.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    counters[key] += value
        result[entity] = {'accepted_days': len(series), 'accepted_trades': sum(r['trades'] for r in series),
                          'single_trade_days': sum(r['trades'] == 1 for r in series),
                          'at_least_three_trade_days': sum(r['trades'] >= 3 for r in series),
                          'latest': series[-1] if series else None, 'quality_counts': dict(counters)}
    return result


def compare_old(new, old):
    n = {(r['date'], r['entity']): r for r in new['daily']}
    o = {(r['date'], r['entity']): r for r in old['daily']}
    changed = [k for k in n.keys() & o.keys() if abs(n[k]['bp'] - o[k]['bp']) > 0.1000001 or n[k]['trades'] != o[k]['trades']]
    return {'old_accepted_entity_days': len(o), 'new_accepted_entity_days': len(n),
            'withheld_entity_days': len(o.keys()-n.keys()), 'newly_accepted_entity_days': len(n.keys()-o.keys()),
            'changed_entity_days': len(changed),
            'largest_common_day_change_bp': round(max((abs(n[k]['bp']-o[k]['bp']) for k in changed), default=0), 8),
            'by_entity': {e: {'old_days': sum(k[1] == e for k in o), 'new_days': sum(k[1] == e for k in n),
                             'withheld_days': sum(k[1] == e for k in o.keys()-n.keys())} for e in ENTITIES}}


def synthetic_checks(module, site_dir):
    """Assertions target independent properties, not a fitted market outcome."""
    day = '2026-11-24'
    base = {
        'Action type': 'NEWT', 'Dissemination Identifier': 'S1', 'Original Dissemination Identifier': '',
        'Event type': 'TRAD', 'Event timestamp': day+'T16:00:00Z',
        'Execution Timestamp': day+'T15:00:00Z', 'Expiration Date': '2031-12-20',
        'Underlying Asset Name': 'COREWEAVE INC', 'UPI Underlier Name': '', 'Underlier ID-Leg 1': '239FAV',
        'Fixed rate-Leg 1': '0.05', 'Spread-Leg 1': '', 'Notional amount-Leg 1': '10000000',
        'Notional currency-Leg 1': 'USD', 'Other payment amount': '2000000', 'Other payment type': 'UFRO',
        'Other payment currency': 'USD', 'UPI FISN': 'NA/CDS Corp SN Sr', 'Package indicator': 'FALSE',
        'Non-standardized term indicator': 'FALSE', 'Post-priced swap indicator': 'FALSE'}
    quote = dict(base, **{'Dissemination Identifier': 'S2', 'Spread-Leg 1': '0.005', 'Other payment amount': ''})
    large = dict(quote, **{'Spread-Leg 1': '0.4'})
    seed = dict(quote, **{'Dissemination Identifier': 'C1', 'Spread-Leg 1': '0.03'})
    corr1 = dict(seed, **{'Action type': 'CORR', 'Dissemination Identifier': 'C2',
                        'Original Dissemination Identifier': 'C1', 'Spread-Leg 1': '0.035'})
    corr2 = dict(corr1, **{'Dissemination Identifier': 'C3', 'Original Dissemination Identifier': 'C2',
                         'Spread-Leg 1': '0.04'})
    cancel = dict(corr2, **{'Action type': 'EROR', 'Dissemination Identifier': 'C4',
                          'Original Dissemination Identifier': 'C3'})
    scenarios = {'ambiguous': [base], 'injected_quote': [base, quote],
                 'genuine_large_quote': [large], 'correction_chain': [corr2, seed, corr1],
                 'cancel_descendant': [corr2, cancel, seed, corr1]}
    out = {}
    for name, rows in scenarios.items():
        q = {}
        levels, _ = module.parse_day(rows, day, {'CRWV': 1500}, quality=q)
        v = levels.get('CRWV')
        out[name] = {'bp': v['official_bp'] if v else None, 'trades': v['official_trades'] if v else 0,
                     'upfront_trades': v['upfront_trades'] if v else 0, 'quality': q['CRWV']}
    assert out['ambiguous']['bp'] is None and out['ambiguous']['quality']['ambiguous'] == 1
    assert out['injected_quote']['bp'] == 50 and out['injected_quote']['upfront_trades'] == 0
    assert out['injected_quote']['quality']['ambiguous'] == 1
    assert out['genuine_large_quote']['bp'] == 4000  # no arbitrary ceiling hides repricing
    assert out['correction_chain']['bp'] == 400 and out['correction_chain']['trades'] == 1
    assert out['cancel_descendant']['bp'] is None
    try:
        module.validate_columns([k for k in base if k != 'Notional currency-Leg 1'])
    except module.SourceSchemaError:
        schema_pass = True
    else:
        schema_pass = False
    assert schema_pass
    code = r"""
const fs=require('node:fs'),path=require('node:path'),cfg=JSON.parse(fs.readFileSync(0,'utf8'));
const t=require(path.join(cfg.site_dir,'api/cds.js'))._test,out={};
for(const [name,rows] of Object.entries(cfg.scenarios)){
 const all=t.levelsForDay(rows,cfg.day,{CRWV:{bp1:1500}},false),v=all.CRWV;
 out[name]={bp:v?v.bp1:null,trades:v?v.trades:0,upfront_trades:v?v.trades-v.quoted:0,quality:all._quality.CRWV};
}
const gap=t.sourceQuality([cfg.day,'2026-11-25'],[{date:cfg.day,status:'available'},{date:'2026-11-25',status:'missing'}],[{date:cfg.day}],{status:'complete'});
process.stdout.write(JSON.stringify({out,gap}));
"""
    node = subprocess.run(['node', '-e', code], input=json.dumps({'site_dir': str(site_dir.resolve()), 'scenarios': scenarios, 'day': day}),
                          capture_output=True, text=True, check=True)
    result = json.loads(node.stdout)
    diffs = differences(out, result['out'])
    assert not diffs, diffs
    assert result['gap']['calculation_eligible'] is False and result['gap']['status'] == 'partial'
    return {'passed': True, 'cross_language_scenarios': len(scenarios),
            'properties': ['An injected quote cannot choose an unsigned upfront direction.',
                           'A valid large quote is retained without an empirical movement cap.',
                           'Out-of-order correction chains count once; descendant cancellation removes the lineage.',
                           'Missing required schema and a requested source-file gap fail closed.'],
            'residual_risk': 'One eligible quoted trade can still set a research level. This test does not establish manipulation resistance or settlement eligibility.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw-dir', type=Path, required=True)
    ap.add_argument('--site-dir', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--manifest', type=Path, default=ROOT/'manifests/dtcc_sha256.csv')
    ap.add_argument('--baseline-ref')
    a = ap.parse_args()
    manifest = {r['date']: r['sha256'] for r in csv.DictReader(a.manifest.open())}
    module = load_current()
    files, rows, hashes = [], {}, []
    for path in sorted(a.raw_dir.glob('????-??-??.zip')):
        date, data = path.stem, path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if manifest.get(date) != digest:
            raise ValueError(f'{date}: missing or mismatched source manifest hash')
        rows[date] = module.rows_of(data)
        files.append({'date': date, 'path': str(path.resolve())})
        hashes.append({'date': date, 'sha256': digest})
    if not files:
        raise ValueError('No date-named source ZIPs found')
    python, by_day = py_replay(module, files, rows)
    reversed_ledger = module.ledger([(f['date'], module.slim(list(reversed(rows[f['date']])))) for f in reversed(files)])
    order_invariant = by_day == reversed_ledger and by_day.quality == reversed_ledger.quality
    if not order_invariant:
        raise AssertionError('Lifecycle result depends on source file or row order')
    run = subprocess.run(['node', '-e', NODE_REPLAY], input=json.dumps({'site_dir': str(a.site_dir.resolve()), 'files': files}),
                         text=True, capture_output=True, check=True)
    javascript = json.loads(run.stdout)
    parity = differences(python, javascript)
    report = {'window': {'start': files[0]['date'], 'end': files[-1]['date'], 'verified_files': len(files)},
              'point_in_time_verified': False, 'complete_historical_ledger': False, 'settlement_eligible': False,
              'decoder_sha256': hashlib.sha256((ROOT/'harness/dtcc.py').read_bytes()).hexdigest(),
              'site_decoder_sha256': hashlib.sha256((a.site_dir/'api/cds.js').read_bytes()).hexdigest(),
              'source_hashes': hashes, 'source_order_invariant': order_invariant, 'ledger_quality': python['ledger_quality'], 'entities': summary(python),
              'synthetic_checks': synthetic_checks(module, a.site_dir),
              'implementation_hashes': {str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p.relative_to(a.site_dir)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'harness/dtcc.py', ROOT/'harness/dtcc_lifecycle.py', a.site_dir/'api/cds.js', a.site_dir/'api/_dtcc_lifecycle.js']},
              'parity': {'pass': not parity, 'accepted_entity_days': len(python['daily']),
                         'diagnostic_entity_days': len(python['quality']), 'differences': parity},
              'limitations': ['Bounded retrospective replay; later lifecycle events and earlier ancestors may be absent.',
                              'Python/JavaScript agreement is implementation parity, not independent economic model validation.',
                              'The legacy raw capture does not establish when every event was first available.']}
    if a.baseline_ref:
        baseline, baseline_hash = load_baseline(a.baseline_ref)
        old, _ = py_replay(baseline, files, rows, include_quality=False)
        old_same_ledger, _ = py_replay(baseline, files, rows, include_quality=False, ledger_override=by_day)
        report['baseline'] = {'ref': a.baseline_ref, 'commit': subprocess.run(['git','-C',str(ROOT),'rev-parse',a.baseline_ref],capture_output=True,text=True,check=True).stdout.strip(), 'decoder_sha256': baseline_hash,
                              'all_changes': compare_old(python, old),
                              'pricing_changes_on_same_strict_ledger': compare_old(python, old_same_ledger)}
    a.output_dir.mkdir(parents=True, exist_ok=True)
    (a.output_dir/'strict_decoder_validation.json').write_text(json.dumps(report, indent=2)+'\n')
    (a.output_dir/'strict_decoder_entity_days.json').write_text(json.dumps(python, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('window', 'ledger_quality', 'entities', 'parity', 'synthetic_checks', 'baseline') if k in report}, indent=2))
    return 0 if not parity else 1


if __name__ == '__main__':
    raise SystemExit(main())
