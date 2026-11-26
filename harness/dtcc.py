"""Daily 5-year CDS levels from DTCC's free public swap data (SEC Regulation SBSR dissemination).

Same cleaning rules as aistress.si/api/cds:
  - CDS rows only (no loans, total return swaps or delayed-draw loans); a standard coupon or a quoted spread
  - cancelled trades (EROR) removed, corrections (CORR) applied, only trades executed on the file date
  - on-the-run 5-year maturity only
  - quoted spreads used as reported when 10-5,000 bp
  - upfront-only trades converted to a par spread (flat hazard, 40% recovery, 4.5% flat rate);
    DTCC upfronts are unsigned, so the reading closer to the day's quoted median (or the previous level) wins
  - daily level = median of the day's spreads

It also records, for each day, how far the level moves if the single most influential trade is removed
(leave-one-out). That is the manipulation test: what one trade can do to the index.

Usage: python harness/dtcc.py --days 400 --out data/cds_daily.csv
"""
import argparse, concurrent.futures as cf, csv, datetime as dt, io, json, math, os, pathlib, statistics, sys, urllib.request, zipfile

URL = 'https://pddata.dtcc.com/ppd/api/report/cumulative/sec/SEC_CUMULATIVE_CREDITS_{}.zip'
UA = 'acsi-lab/0.1 (+https://github.com/gagattzo/acsi-lab)'
ENTITIES = {  # id: (name match, RED code)
    'ORCL': ('ORACLE', '6EC42L'),
    'CRWV': ('COREWEAVE', '239FAV'),
    'META': ('META PLATFORMS', 'UZEAAG'),
}
DAY = 86400.0
RATE, RECOVERY = 0.045, 0.40


def fnum(v):
    try:
        x = float(str(v).replace(',', '').replace('+', ''))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def ts(s):
    s = str(s).strip()
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def utc(y, m, d):
    return dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp()


def last_imm(t):
    y = dt.datetime.fromtimestamp(t, dt.timezone.utc).year
    for yy in (y, y - 1):
        for m in (12, 9, 6, 3):
            c = utc(yy, m, 20)
            if c <= t:
                return c
    return t


def on_the_run_5y(t):
    y = dt.datetime.fromtimestamp(t, dt.timezone.utc).year
    if t >= utc(y, 9, 20):
        return utc(y + 5, 12, 20)
    if t >= utc(y, 3, 20):
        return utc(y + 5, 6, 20)
    return utc(y + 4, 12, 20)


def legs(lam, T, R=RECOVERY, r=RATE):
    prot = rpv = 0.0
    q0 = 1.0
    n = max(1, math.floor(T * 4 + 0.5))
    for i in range(1, n + 1):
        t0 = (i - 1) / 4
        t1 = i / 4 if i < n else T
        q1 = math.exp(-lam * t1)
        dm = math.exp(-r * (t0 + t1) / 2)
        rpv += (t1 - t0) * (365 / 360) * (math.exp(-r * t1) * q1 + 0.5 * dm * (q0 - q1))
        prot += (1 - R) * dm * (q0 - q1)
        q0 = q1
    return prot, rpv


def spread_from_upfront(upfront, C, T, accr):
    target = upfront + C * accr
    lo, hi = 1e-6, 3.0
    for _ in range(90):
        m = (lo + hi) / 2
        p, v = legs(m, T)
        if p - C * v < target:
            lo = m
        else:
            hi = m
    p, v = legs((lo + hi) / 2, T)
    return p / v


def is_cds(r):
    uid = str(r.get('Underlier ID-Leg 1', ''))
    nm = (str(r.get('Underlying Asset Name', '')) + ' ' + str(r.get('UPI Underlier Name', ''))).upper()
    if uid.upper().startswith('LX') or ' TRS' in ' ' + nm + ' ' or 'DDTL' in nm:
        return False
    return (fnum(r.get('Fixed rate-Leg 1')) or 0) > 0 or (fnum(r.get('Spread-Leg 1')) or 0) > 0


def clean(rows, day):
    cancelled = {r.get('Original Dissemination Identifier') or r.get('Dissemination Identifier') for r in rows if r.get('Action type') == 'EROR'}
    keep = {}
    for r in rows:
        act = r.get('Action type')
        if act not in ('NEWT', 'CORR'):
            continue
        key = (r.get('Original Dissemination Identifier') or r.get('Dissemination Identifier')) if act == 'CORR' else r.get('Dissemination Identifier')
        if key in cancelled or r.get('Dissemination Identifier') in cancelled:
            continue
        if act == 'NEWT' and key in keep:
            continue
        keep[key] = r
    return [r for r in keep.values() if str(r.get('Execution Timestamp', ''))[:10] == day]


def levels_for_day(rows, day, prev):
    rows = clean(rows, day)
    out = {}
    for eid, (match, red) in ENTITIES.items():
        quoted, ups, last = [], [], None
        for r in rows:
            text = (str(r.get('Underlying Asset Name', '')) + ' ' + str(r.get('UPI Underlier Name', '')) + ' ' + str(r.get('Underlier ID-Leg 1', ''))).upper()
            if not ((match in text or red in text) and is_cds(r)):
                continue
            ex, exp = ts(r.get('Execution Timestamp')), ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
            if ex is None or exp is None or exp != on_the_run_5y(ex):
                continue
            C = fnum(r.get('Fixed rate-Leg 1')) or 0.0
            step = ex + DAY
            T = (exp - step) / (365.25 * DAY)
            accr = (step - last_imm(ex)) / (360 * DAY)
            N = fnum(r.get('Notional amount-Leg 1'))
            amt = fnum(r.get('Other payment amount'))  # blank stays None: no upfront, no reading
            pct = amt / N if (N and N > 0 and amt is not None) else None
            q = fnum(r.get('Spread-Leg 1'))
            if q:
                if 0.001 <= q < 0.5:
                    quoted.append(q)
                    last = max(last or ex, ex)
            elif pct is not None and C:
                ups.append((spread_from_upfront(abs(pct), C, T, accr), spread_from_upfront(-abs(pct), C, T, accr), abs(pct)))
                last = max(last or ex, ex)
        ref = statistics.median(quoted) if quoted else (prev[eid] / 10000 if prev.get(eid) else None)
        vals = list(quoted)
        for hi, lo, p in ups:
            s = None
            if ref is not None:
                s = hi if abs(hi - ref) <= abs(lo - ref) else lo
            elif p >= 0.02:
                s = hi
            if s is not None and 0 < s < 1:
                vals.append(s)
        if not vals:
            continue
        med = statistics.median(vals)
        loo = max((abs(statistics.median(vals[:i] + vals[i + 1:]) - med) for i in range(len(vals))), default=None) if len(vals) > 1 else None
        out[eid] = {'bp': round(med * 10000, 1), 'trades': len(vals), 'quoted': len(quoted),
                    'loo_bp': None if loo is None else round(loo * 10000, 1),
                    'last_trade': dt.datetime.fromtimestamp(last, dt.timezone.utc).isoformat() if last else None}
    return out


def fetch(day, cache):
    p = cache / f'{day}.zip'
    if p.exists():
        return p.read_bytes() or None
    req = urllib.request.Request(URL.format(day.replace('-', '_')), headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
    except Exception:
        b = b''
    if b[:2] != b'PK':
        b = b''
    if day < dt.date.today().isoformat():
        p.write_bytes(b)  # cache misses too, for past days
    return b or None


def rows_of(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode('utf-8', 'replace'))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=400)
    ap.add_argument('--out', default='data/cds_daily.csv')
    ap.add_argument('--cache', default='data/dtcc_cache')
    a = ap.parse_args()
    cache = pathlib.Path(a.cache); cache.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    days = [(today - dt.timedelta(days=k)).isoformat() for k in range(1, a.days + 1)]
    days = sorted(d for d in days if dt.date.fromisoformat(d).weekday() < 5)
    with cf.ThreadPoolExecutor(12) as ex:
        blobs = dict(zip(days, ex.map(lambda d: fetch(d, cache), days)))
    prev, out_rows = {}, []
    for d in days:
        b = blobs[d]
        if not b:
            continue
        lv = levels_for_day(rows_of(b), d, prev)
        for eid, v in lv.items():
            prev[eid] = v['bp']
            out_rows.append({'date': d, 'entity': eid, **{k: v[k] for k in ('bp', 'trades', 'quoted', 'loo_bp', 'last_trade')}})
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['date', 'entity', 'bp', 'trades', 'quoted', 'loo_bp', 'last_trade'])
        w.writeheader(); w.writerows(out_rows)
    n_files = sum(1 for d in days if blobs[d])
    print(f'{n_files} daily files, {len(out_rows)} entity-days -> {a.out}', file=sys.stderr)


if __name__ == '__main__':
    main()
