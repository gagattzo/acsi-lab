"""Check a canonical table against its schema: columns, types, enums, ranges, blanks and key uniqueness.

  python harness/validate.py schema/cds_trades.yaml schema/cds_daily.yaml
Exit code 1 on any error.
"""
import csv, datetime as dt, pathlib, sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def check(schema_path):
    s = yaml.safe_load(open(schema_path))
    path = ROOT / s['file']
    if not path.exists():
        return [f"{s['table']}: {s['file']} missing (run {s.get('built_by', 'the loader')})"]
    rows = list(csv.DictReader(open(path)))
    cols = [c['name'] for c in s['columns']]
    errs = []
    head = list(rows[0].keys()) if rows else []
    if rows and head != cols:
        errs.append(f"{s['table']}: columns {head} != schema {cols}")
    seen = set()
    for i, r in enumerate(rows, start=2):
        for c in s['columns']:
            v = (r.get(c['name']) or '').strip()
            where = f"{s['table']} line {i} {c['name']}={v!r}"
            if v == '':
                if not c.get('nullable'):
                    errs.append(where + ': blank but not nullable')
                continue
            t = c['type']
            try:
                if t == 'date':
                    dt.date.fromisoformat(v)
                elif t == 'datetime':
                    dt.datetime.fromisoformat(v.replace('Z', '+00:00'))
                elif t in ('float', 'int'):
                    x = float(v) if t == 'float' else int(v)
                    if 'min' in c and x < c['min']:
                        errs.append(where + f": below {c['min']}")
                    if 'max' in c and x > c['max']:
                        errs.append(where + f": above {c['max']}")
                elif t == 'enum' and v not in c['values']:
                    errs.append(where + f": not one of {c['values']}")
                elif t == 'bool' and v not in ('True', 'False'):
                    errs.append(where + ': not True/False')
            except ValueError:
                errs.append(where + f': not a {t}')
        k = tuple(r.get(x) for x in s['key'])
        if k in seen:
            errs.append(f"{s['table']} line {i}: duplicate key {k}")
        seen.add(k)
    return errs


def main():
    errs = [e for p in sys.argv[1:] for e in check(p)]
    for e in errs[:50]:
        print(e)
    print(f'{len(errs)} errors' if errs else 'ok')
    sys.exit(1 if errs else 0)


if __name__ == '__main__':
    main()
