"""Build the DTCC parity corpus and its expected decoder output.

Usage (from the repository root):
    python tests/fixtures/dtcc_parity/build.py           # write corpus.json, expected.json, SHA256SUMS
    python tests/fixtures/dtcc_parity/build.py --check   # fail if the committed files differ from a rebuild

The corpus is synthetic: DTCC SEC credit records written field by field to exercise every decoding rule that the lab
(harness/dtcc.py) and the website (api/cds.js) must apply identically. No market data is copied. expected.json is what
this repository's decoder produces from the corpus. The website keeps byte-identical copies of the three files and its
CI checks its JavaScript decoder against expected.json and these files against this repository's main branch.

Change the decoder in both repositories together: rebuild here, copy the three files to the website, and update the
website's decoder until its parity test passes.
"""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
import dtcc  # noqa: E402
from dtcc_lifecycle import REQUIRED_COLUMNS  # noqa: E402

NAMES = {'ORCL': ('ORACLE CORPORATION', '6EC42L', '0.01'), 'CRWV': ('COREWEAVE, INC.', '239FAV', '0.05'),
         'META': ('META PLATFORMS, INC.', 'UZEAAG', '0.01')}
ON_THE_RUN = '2031-12-20'  # executions from 20 Sep 2026 to 19 Mar 2027
DAYS = ['2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-30']
SCENARIOS = {}


def rec(rid, entity, day, time='15:00:00', scenario=None, **kw):
    name, red, coupon = NAMES[entity]
    stamp = f'{day}T{time}Z'
    row = {c: '' for c in REQUIRED_COLUMNS}
    row.update({'Action type': 'NEWT', 'Dissemination Identifier': rid, 'Event type': 'TRAD', 'Event timestamp': stamp,
                'Execution Timestamp': stamp, 'Expiration Date': ON_THE_RUN, 'Underlying Asset Name': name,
                'Underlier ID-Leg 1': red, 'Fixed rate-Leg 1': coupon, 'Notional amount-Leg 1': '10000000',
                'Notional currency-Leg 1': 'USD', 'Other payment type': 'UFRO', 'Other payment currency': 'USD',
                'UPI FISN': 'NA/CDS Corp SN Sr', 'Package indicator': 'FALSE', 'Non-standardized term indicator': 'FALSE',
                'Post-priced swap indicator': 'FALSE'})
    for k, v in kw.items():
        row[k.replace('_', ' ') if k not in row else k] = v
    if scenario:
        SCENARIOS.setdefault(scenario, []).append(rid)
    return row


def low_root_amount(day):
    """An upfront on CoreWeave's 5% coupon whose low-spread root is positive but under 10 bp."""
    ex, exp = dtcc.ts(f'{day}T15:00:00Z'), dtcc.ts(f'{ON_THE_RUN}T00:00:00Z')
    step = ex + dtcc.DAY
    T, accr = (exp - step) / (365.25 * dtcc.DAY), (step - dtcc.last_imm(ex)) / (360 * dtcc.DAY)
    for a in range(2_200_000, 2_500_000, 500):
        s = dtcc.upfront_solution(-a / 1e7, .05, T, accr)['spread']
        if s is not None and 0 < s < dtcc.QUOTE_FLOOR:
            return str(a)
    raise RuntimeError('no low-root amount found')


def corpus():
    SCENARIOS.clear()
    A, B, C, D, E = DAYS
    q = lambda v: {'Spread-Leg 1': v}
    up = lambda v, **kw: dict({'Other payment amount': v}, **kw)
    files = {
        A: [
            rec('A01', 'ORCL', A, scenario='quoted decimal', **q('0.0334')),
            rec('A02', 'ORCL', A, '15:10:00', scenario='quoted raw bp', **q('336')),
            rec('A03', 'ORCL', A, '15:20:00', scenario='upfront with one feasible root', **up('1100000')),
            rec('A04', 'ORCL', A, '15:30:00', scenario='coupon written in percent', **up('1090000', **{'Fixed rate-Leg 1': '1'})),
            rec('A05', 'ORCL', A, '15:40:00', scenario='capped notional', **up('550000', **{'Notional amount-Leg 1': '5,000,000+'})),
            rec('A06', 'ORCL', A, '15:50:00', scenario='unwind payment is not an upfront', **up('1100000', **{'Other payment type': 'UWIN'})),
            rec('A07', 'ORCL', A, '16:00:00', scenario='blank upfront is missing'),
            rec('A08', 'ORCL', A, '16:10:00', scenario='zero quote is the no-quote sentinel', **dict(q('0'), **up('1080000'))),
            rec('A09', 'ORCL', A, '16:20:00', scenario='quote with unresolved units', **q('2')),
            rec('A10', 'ORCL', A, '16:30:00', scenario='quote under 10 bp', **q('.00045')),
            rec('A11', 'CRWV', A, scenario='two feasible upfront roots', **up('2309720')),
            rec('A12', 'CRWV', A, '15:10:00', scenario='upfront outside the model domain', **up('7000000')),
            rec('A13', 'CRWV', A, '15:20:00', scenario='quote under 10 bp', **q('.000125')),
            rec('A14', 'META', A, scenario='quoted decimal', **q('0.0115')),
            rec('A15', 'META', A, '15:10:00', scenario='two feasible upfront roots', **up('120000')),
            rec('A16', 'ORCL', A, '17:00:00', scenario='ineligible: odd lot', **dict(q('0.0500'), **{'Notional amount-Leg 1': '500,000'})),
            rec('A17', 'ORCL', A, '17:10:00', scenario='ineligible: not USD', **dict(q('0.0500'), **{'Notional currency-Leg 1': 'EUR'})),
            rec('A18', 'ORCL', A, '17:20:00', scenario='ineligible: novation', **dict(q('0.0500'), **{'Event type': 'NOVA'})),
            rec('A19', 'ORCL', A, '17:30:00', scenario='ineligible: package', **dict(q('0.0500'), **{'Package indicator': 'TRUE'})),
            rec('A20', 'ORCL', A, '17:40:00', scenario='ineligible: post-priced', **dict(q('0.0500'), **{'Post-priced swap indicator': 'true'})),
            rec('A21', 'ORCL', A, '17:50:00', scenario='ineligible: not senior CDS', **dict(q('0.0500'), **{'UPI FISN': 'NA/CDS Corp SN'})),
            rec('A22', 'ORCL', A, '18:00:00', scenario='off-the-run maturity', **dict(q('0.0500'), **{'Expiration Date': '2031-06-20'})),
            rec('A23', 'ORCL', A, '18:10:00', scenario='basket legs across names', **up('197460')),
            rec('A24', 'META', A, '18:11:00', scenario='basket legs across names', **up('197460')),
            rec('A25', 'CRWV', A, '18:20:00', scenario='quote with no ceiling', **q('0.45')),
        ],
        B: [
            rec('B01', 'ORCL', B, scenario='correction in a later file', **q('0.0365')),
            rec('B02', 'ORCL', B, '15:10:00', scenario='cancellation in a later file', **q('0.0360')),
            rec('B03', 'CRWV', B, scenario='upfront low root under 10 bp stays ambiguous', **up(low_root_amount(B))),
            rec('B04', 'ORCL', B, '15:30:00', scenario='ambiguous correction order', **q('0.0361')),
            rec('B05', 'ORCL', B, '15:40:00', scenario='conflicting versions of one identifier', **q('0.0362')),
            rec('B06', 'META', B, scenario='quoted decimal', **q('0.0120')),
        ],
        C: [
            rec('C01', 'ORCL', B, '15:00:05', scenario='correction in a later file',
                **dict(q('0.0370'), **{'Action type': 'CORR', 'Original Dissemination Identifier': 'B01', 'Event timestamp': f'{C}T09:00:00Z'})),
            rec('C02', 'ORCL', B, '15:10:00', scenario='cancellation in a later file',
                **{'Action type': 'EROR', 'Original Dissemination Identifier': 'B02', 'Event timestamp': f'{C}T09:05:00Z'}),
            rec('C03', 'ORCL', B, '19:55:00', scenario='late report priced on its execution day', **q('0.0362')),
            rec('C04', 'ORCL', B, '15:30:00', scenario='ambiguous correction order',
                **dict(q('0.0363'), **{'Action type': 'CORR', 'Original Dissemination Identifier': 'B04', 'Event timestamp': f'{C}T10:00:00Z'})),
            rec('C05', 'ORCL', B, '15:30:00', scenario='ambiguous correction order',
                **dict(q('0.0364'), **{'Action type': 'CORR', 'Original Dissemination Identifier': 'B04', 'Event timestamp': f'{C}T10:00:00Z'})),
            rec('C06', 'ORCL', C, scenario='correction of a trade outside the window',
                **dict(q('0.0371'), **{'Action type': 'CORR', 'Original Dissemination Identifier': 'Z99'})),
            rec('C07', 'ORCL', C, '15:10:00', scenario='termination of a trade outside the window',
                **{'Action type': 'TERM', 'Original Dissemination Identifier': 'Z98'}),
            rec('C08', 'ORCL', C, '15:20:00', scenario='cyclic lineage',
                **dict(q('0.0372'), **{'Action type': 'CORR', 'Original Dissemination Identifier': 'C09'})),
            rec('C09', 'ORCL', C, '15:30:00', scenario='cyclic lineage',
                **dict(q('0.0372'), **{'Action type': 'CORR', 'Original Dissemination Identifier': 'C08'})),
            rec('C10', 'ORCL', C, '15:40:00', scenario='unknown action', **dict(q('0.0372'), **{'Action type': 'XXXX'})),
            rec('C11', 'ORCL', C, '16:00:00', scenario='quoted decimal', **q('0.0373')),
            rec('C12', 'CRWV', C, scenario='two feasible upfront roots', **up('2300000')),
            rec('C13', 'META', C, scenario='quoted decimal', **q('0.0124')),
            rec('B05', 'ORCL', B, '15:40:00', scenario='conflicting versions of one identifier', **q('0.0390')),
        ],
        D: [
            rec('D01', 'ORCL', D, scenario='quoted decimal', **q('0.0374')),
        ],
        E: [
            rec('E01', 'ORCL', '2026-11-28', scenario='weekend execution is not a trading day', **q('0.0375')),
            rec('E02', 'ORCL', E, scenario='quoted raw bp', **q('376')),
        ],
    }
    return {'schema': 'acsi-dtcc-parity-corpus-v1',
            'description': 'Synthetic DTCC SEC credit records exercising every decoding rule; no market data.',
            'columns': list(REQUIRED_COLUMNS),
            'scenarios': {k: sorted(set(v)) for k, v in sorted(SCENARIOS.items())},
            'files': [{'date': d, 'rows': rows} for d, rows in sorted(files.items())]}


def decode(doc):
    files = [(x['date'], dtcc.slim(x['rows'])) for x in doc['files']]
    by_day = dtcc.ledger(files)
    first = doc['files'][0]['date']
    days, prev = {}, {}
    for d in sorted(x for x in by_day if x >= first):
        quality = {}
        levels, _ = dtcc.parse_day(by_day[d], d, prev, cleaned=True, quality=quality)
        for e, v in levels.items():
            prev[e] = v['official_bp']
        days[d] = {e: {'level': ({k: levels[e][k] for k in ('official_bp', 'official_kind', 'official_trades', 'quoted_trades')}
                                 if e in levels else None),
                       'quality': quality.get(e)} for e in ('ORCL', 'CRWV', 'META')}
    q = by_day.quality
    return {'schema': 'acsi-dtcc-parity-expected-v1', 'decoder_version': dtcc.DECODER_VERSION,
            'ledger_quality': {k: q[k] for k in ('status', 'issue_counts', 'quarantined_lineages', 'orphan_lineages',
                                                 'cancelled_lineages', 'effective_trades')},
            'days': days}


def dump(obj):
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + '\n'


def build():
    doc = corpus()
    files = {'corpus.json': dump(doc), 'expected.json': dump(decode(doc))}
    sums = ''.join(f'{hashlib.sha256(text.encode()).hexdigest()}  {name}\n' for name, text in sorted(files.items()))
    files['SHA256SUMS'] = sums
    return files


def main():
    files = build()
    if '--check' in sys.argv:
        stale = [n for n, text in files.items() if not (HERE / n).exists() or (HERE / n).read_text() != text]
        if stale:
            sys.exit('parity fixture out of date (rebuild with tests/fixtures/dtcc_parity/build.py): ' + ', '.join(stale))
        print('parity fixture matches a rebuild')
        return
    for name, text in files.items():
        (HERE / name).write_text(text)
    print('wrote', ', '.join(sorted(files)))


if __name__ == '__main__':
    main()
