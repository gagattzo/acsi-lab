"""DTCC public swap data (SEC Regulation SBSR) -> canonical CDS tables.

Raw:       data/raw/dtcc/YYYY-MM-DD.zip          one end-of-day credit file per weekday, as published
Manifest:  manifests/dtcc_sha256.csv              sha256 of every raw file (committed: a changed file fails the run)
Canonical: data/canonical/cds_trades.csv          one row per priced trade (schema/cds_trades.yaml)
           data/canonical/cds_daily.csv           one row per entity-day (schema/cds_daily.yaml)

Cleaning rules (same as aistress.si/api/cds; each has a test in tests/test_dtcc.py):
  - CDS rows only: no loans (LX ids), total return swaps or delayed-draw loans; needs a coupon or a quoted spread
  - cancelled trades (EROR) removed, corrections (CORR) replace the record they correct, same-day executions only
  - on-the-run 5-year maturity only
  - quoted spreads used as reported when 10-5,000 bp; 0 or blank means "not quoted"
  - a blank upfront is missing, not zero
  - upfront-only trades converted to a par spread (flat hazard, 40% recovery, 4.5% flat rate); DTCC upfronts are
    unsigned, so the feasible reading (10 bp or more) closer to the day's quoted median (or the previous level) wins
  - daily level = median of the day's priced trades

Usage: python harness/dtcc.py [--days 400] [--offline]
"""
import argparse, concurrent.futures as cf, csv, datetime as dt, hashlib, io, math, pathlib, statistics, sys, urllib.request, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
URL = 'https://pddata.dtcc.com/ppd/api/report/cumulative/sec/SEC_CUMULATIVE_CREDITS_{}.zip'
UA = 'Mozilla/5.0 (compatible; acsi-lab/0.2; +https://aistress.si/lab)'
ENTITIES = {  # id: (name match, RED code)
    'ORCL': ('ORACLE', '6EC42L'),
    'CRWV': ('COREWEAVE', '239FAV'),
    'META': ('META PLATFORMS', 'UZEAAG'),
}
DAY = 86400.0
RATE, RECOVERY = 0.045, 0.40
TRADE_FIELDS = ['date', 'entity', 'dissemination_id', 'exec_ts', 'kind', 'spread_bp', 'coupon', 'upfront_pct', 'notional_capped']
DAILY_FIELDS = ['date', 'entity', 'bp', 'trades', 'quoted', 'loo_bp', 'last_trade']


def fnum(v):
    """Number or None. Blank strings are None, never 0."""
    s = str(v if v is not None else '').strip().replace(',', '').replace('+', '')
    if not s:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def ts(s):
    s = str(s or '').strip()
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
    if uid.upper().startswith('LX') or ' TRS ' in ' ' + nm + ' ' or 'DDTL' in nm:
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


def parse_day(rows, day, prev):
    """Returns (levels, trades): levels[entity] = daily row; trades = list of canonical trade rows."""
    rows = clean(rows, day)
    levels, trades = {}, []
    for eid, (match, red) in ENTITIES.items():
        quoted, ups = [], []
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
            amt = fnum(r.get('Other payment amount'))
            pct = amt / N if (N and N > 0 and amt is not None) else None
            meta = {'dissemination_id': r.get('Dissemination Identifier', ''), 'exec_ts': r.get('Execution Timestamp', ''),
                    'coupon': C or None, 'notional_capped': '+' in str(r.get('Notional amount-Leg 1', ''))}
            q = fnum(r.get('Spread-Leg 1'))
            if q:
                if 0.001 <= q < 0.5:
                    quoted.append((q, ex, dict(meta, kind='quoted', upfront_pct=None if pct is None else round(pct * 100, 4))))
            elif pct is not None and C:
                ups.append((spread_from_upfront(abs(pct), C, T, accr), spread_from_upfront(-abs(pct), C, T, accr), abs(pct), ex,
                            dict(meta, kind='upfront', upfront_pct=round(abs(pct) * 100, 4))))
        ref = statistics.median([x[0] for x in quoted]) if quoted else (prev[eid] / 10000 if prev.get(eid) else None)
        priced = list(quoted)
        ok = lambda x: 0.001 <= x < 1  # a reading at the solver's floor (under 10 bp) is the impossible sign, not a price
        for hi, lo, p, ex, meta in ups:
            s = None
            feasible = [x for x in (hi, lo) if ok(x)]
            if ref is not None and feasible:
                s = min(feasible, key=lambda x: abs(x - ref))
            elif ref is None and p >= 0.02 and ok(hi):
                s = hi
            if s is not None:
                priced.append((s, ex, meta))
        if not priced:
            continue
        vals = [x[0] for x in priced]
        med = statistics.median(vals)
        loo = max(abs(statistics.median(vals[:i] + vals[i + 1:]) - med) for i in range(len(vals))) if len(vals) > 1 else None
        last = max(x[1] for x in priced)
        levels[eid] = {'date': day, 'entity': eid, 'bp': round(med * 10000, 1), 'trades': len(vals), 'quoted': len(quoted),
                       'loo_bp': None if loo is None else round(loo * 10000, 1),
                       'last_trade': dt.datetime.fromtimestamp(last, dt.timezone.utc).isoformat()}
        for s, ex, meta in priced:
            trades.append({'date': day, 'entity': eid, 'spread_bp': round(s * 10000, 2), **meta})
    return levels, trades


def rows_of(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode('utf-8', 'replace'))))


def fetch(day, raw, offline):
    p = raw / f'{day}.zip'
    if p.exists():
        return p.read_bytes() or None
    if offline:
        return None
    req = urllib.request.Request(URL.format(day.replace('-', '_')), headers={'User-Agent': UA, 'Accept': '*/*'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
    except Exception:
        b = b''
    if b[:2] != b'PK':
        b = b''
    if b or day < (dt.date.today() - dt.timedelta(days=3)).isoformat():
        p.write_bytes(b)  # remember old misses (holidays) so they are not re-fetched every run
    return b or None


def load_manifest(path):
    if not path.exists():
        return {}
    return {r['date']: r for r in csv.DictReader(open(path))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=400)
    ap.add_argument('--offline', action='store_true', help='use cached raw files only')
    a = ap.parse_args()
    raw = ROOT / 'data' / 'raw' / 'dtcc'; raw.mkdir(parents=True, exist_ok=True)
    canon = ROOT / 'data' / 'canonical'; canon.mkdir(parents=True, exist_ok=True)
    man_path = ROOT / 'manifests' / 'dtcc_sha256.csv'; man_path.parent.mkdir(exist_ok=True)
    today = dt.date.today()
    days = sorted(d for d in ((today - dt.timedelta(days=k)).isoformat() for k in range(1, a.days + 1)) if dt.date.fromisoformat(d).weekday() < 5)
    with cf.ThreadPoolExecutor(12) as ex:
        blobs = dict(zip(days, ex.map(lambda d: fetch(d, raw, a.offline), days)))

    # Provenance: every raw file must match the committed manifest; new days are appended.
    manifest = load_manifest(man_path)
    bad = []
    for d in days:
        b = blobs[d]
        if not b:
            continue
        h = hashlib.sha256(b).hexdigest()
        if d in manifest and manifest[d]['sha256'] != h:
            bad.append(d)
        elif d not in manifest:
            manifest[d] = {'date': d, 'url': URL.format(d.replace('-', '_')), 'bytes': len(b), 'sha256': h}
    if bad:
        sys.exit(f'raw files differ from manifests/dtcc_sha256.csv on {", ".join(bad)}: DTCC republished them or someone '
                 'tampered with the data. Investigate before scoring.')
    with open(man_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['date', 'url', 'bytes', 'sha256']); w.writeheader()
        for d in sorted(manifest):
            w.writerow(manifest[d])

    prev, daily, trades = {}, [], []
    for d in days:
        if not blobs[d]:
            continue
        lv, tr = parse_day(rows_of(blobs[d]), d, prev)
        for eid, v in lv.items():
            prev[eid] = v['bp']
            daily.append(v)
        trades.extend(tr)
    for name, fields, rows in (('cds_daily.csv', DAILY_FIELDS, daily), ('cds_trades.csv', TRADE_FIELDS, trades)):
        with open(canon / name, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f'{sum(1 for d in days if blobs[d])} raw files (hashes checked), {len(daily)} entity-days, {len(trades)} priced trades '
          f'-> data/canonical/', file=sys.stderr)


if __name__ == '__main__':
    main()
