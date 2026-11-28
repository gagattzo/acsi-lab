"""How often does dtcc-strict-v1's lifecycle quarantine block the headline, and how often does it matter?

Usage:  python research/tools/lifecycle_gate_replay.py [/path/to/acsi-lab] [step]   (default: this checkout, 5)
Needs:  data/raw/dtcc/*.zip and harness/dtcc.py, harness/dtcc_lifecycle.py in that checkout (lab main 09990a6).

Replays the site's 12-calendar-day window starting on every `step`-th file day (default 5, so 56 windows over a year)
through the lab's own resolve_lifecycle. For each window it records:
  blocked   at least one quarantined lineage on our three names (the release then marks the whole source partial,
            which withholds the headline)
  matters   a quarantined lineage that holds an eligible, on-the-run 5-year CoreWeave or Oracle NEWT/CORR record,
            i.e. a trade that could have priced a headline input
The quarantine logic is the lab's, unchanged; a copy of the function only records which lineages it quarantined.
It does not model missing or unpublished files, which block the headline separately (weekday_block_check.js).
"""
import collections
import datetime as dt
import inspect
import pathlib
import sys

LAB = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2]
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 5
sys.path.insert(0, str(LAB / 'harness'))
import dtcc as d  # noqa: E402
import dtcc_lifecycle as lc  # noqa: E402

MARK = "            issues.update(reasons)\n"
src = inspect.getsource(lc.resolve_lifecycle)
assert src.count(MARK) == 1, 'resolve_lifecycle changed; update the recording hook'
ns = dict(vars(lc))
exec(src.replace(MARK, MARK + "            QUARANTINED.append((sorted(reasons), list(nodes.values())))\n"), ns)


def quarantined(rows):
    ns['QUARANTINED'] = []
    ns['resolve_lifecycle'](rows, d.entity_of)
    return ns['QUARANTINED']


def prices_an_input(r):
    if d.entity_of(r) not in ('CRWV', 'ORCL') or r.get('Action type') not in ('NEWT', 'CORR') or not (d.is_cds(r) and d.eligible(r)):
        return False
    ex, exp = d.ts(r.get('Execution Timestamp')), d.ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
    return ex is not None and exp is not None and exp == d.on_the_run_5y(ex)


def main():
    raw = LAB / 'data' / 'raw' / 'dtcc'
    days = sorted(p.stem for p in raw.glob('*.zip'))
    rows = {x: d.rows_of((raw / f'{x}.zip').read_bytes()) for x in days}
    print(f'# Lifecycle quarantine replay\n\n{len(days)} raw DTCC files, {days[0]} to {days[-1]}; lab code at {LAB.name}\n')
    q = quarantined([r for x in days for r in rows[x]])
    hits = [(why, [r for r in recs if prices_an_input(r)]) for why, recs in q]
    key = lambda r: (str(r.get('Execution Timestamp', '')), str(r.get('Action type', '')), str(r.get('Dissemination Identifier', '')))
    hits = sorted(((why, sorted(recs, key=key)) for why, recs in hits if recs), key=lambda h: key(h[1][0]))
    reasons = collections.Counter(w for why, _ in q for w in why)
    print(f'Whole year in one ledger: {len(q)} quarantined lineages on our three names ({dict(reasons)}); '
          f'{len(hits)} hold a CoreWeave or Oracle trade that could price an input:')
    for why, recs in hits:
        print(f'- {", ".join(why)}: ' + ', '.join(f'{r.get("Action type")} {d.entity_of(r)} {str(r.get("Execution Timestamp"))[:10]}' for r in recs))
    windows = blocked = matters = 0
    for i in range(0, len(days), STEP):
        end = (dt.date.fromisoformat(days[i]) + dt.timedelta(days=11)).isoformat()
        if end > days[-1]:
            break
        win = [x for x in days[i:] if x <= end]
        qw = quarantined([r for x in win for r in rows[x]])
        windows += 1
        blocked += bool(qw)
        matters += any(prices_an_input(r) for _, recs in qw for r in recs)
    print(f'\n12-day windows (every {STEP}th start day): {windows}; blocked by a quarantine: {blocked}; '
          f'a quarantined trade that could price CoreWeave or Oracle: {matters}')


if __name__ == '__main__':
    main()
