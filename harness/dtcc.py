"""DTCC public swap data (SEC Regulation SBSR) -> canonical CDS tables.

Raw:       data/raw/dtcc/YYYY-MM-DD.zip          one end-of-day credit file per weekday, as published; only valid ZIPs
                                                  are kept, a failed download leaves no file and is retried next run
Manifest:  manifests/dtcc_sha256.csv              append-only: sha256 of every raw file kept (committed)
Conflicts: manifests/dtcc_conflicts.csv           a re-download whose hash differs from the manifest; the bytes go to
                                                  data/raw/dtcc/conflicts/, the day is left out until a human decides
Canonical: data/canonical/cds_trades.csv          one row per priced trade (schema/cds_trades.yaml)
           data/canonical/cds_daily.csv           one row per entity-day (schema/cds_daily.yaml)

Cleaning rules (same as aistress.si/api/cds; each has a test in tests/test_dtcc.py):
  - CDS rows only: no loans (LX ids), total return swaps or delayed-draw loans; needs a coupon or a quoted spread
  - eligible trades only: USD, senior (UPI FISN "CDS Corp SN Sr"), $1M notional or more, no package, non-standard or
    post-priced flag, and no novation (Event type NOVA; a blank Event type, as in older files, is a trade)
  - one ledger across files: a cancellation (EROR) or correction (CORR) applies whichever later file it arrives in, and
    a late report is priced on its execution day (weekdays only)
  - on-the-run 5-year maturity only (ISDA semi-annual roll, 20 Mar / 20 Sep)
  - coupons written in percent (1 = 1%) are divided by 100; only 0 < coupon <= 10% is kept (issue #7)
  - quoted spreads as decimals (0.0365) or raw bp (365) are both read; only 10-5,000 bp is kept; 0 or blank = not quoted
  - an upfront is read only when Other payment type is UFRO (UWIN = unwind, not an entry price); blank = missing, not 0
  - upfront trades on a standard coupon (1% or 5%) converted to a par spread (flat hazard, 40% recovery, 4.5% flat rate).
    DTCC upfronts are unsigned, so each has two readings. A reading under the floor for its coupon (10 bp on 1%, 100 bp
    on 5%) is impossible and the trade decides alone; otherwise the reading nearer the day's eligible quoted median
    (or the previous level) wins, and n_sign_from_reference counts those trades
  - quoted and upfront-implied spreads are kept in separate columns. Official level: the median of all the day's priced
    trades; official_kind says what it rests on (quoted, upfront_implied or mixed). Quoted-only was tested and rejected:
    on Oracle the two differ by a median 1.3 bp, and dropping implied prints more than doubles the days resting on
    under 3 trades (research/findings/2026-11-26-p0-tape-hygiene.md). Methods can still choose prints: quoted_first.

Usage: python harness/dtcc.py [--days 400] [--offline]
"""
import argparse, concurrent.futures as cf, csv, datetime as dt, hashlib, io, math, pathlib, statistics, sys, time, urllib.request, zipfile

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
TRADE_FIELDS = ['date', 'entity', 'dissemination_id', 'exec_ts', 'kind', 'spread_bp', 'coupon', 'coupon_normalised',
                'upfront_pct', 'notional_capped', 'sign_by']
DAILY_FIELDS = ['date', 'entity', 'official_bp', 'official_kind', 'official_trades', 'quoted_median_bp', 'quoted_trades',
                'upfront_implied_bp', 'upfront_trades', 'loo_bp', 'last_trade', 'n_capped', 'n_percent_coupon_normalised',
                'n_ambiguous', 'n_sign_from_reference']
MAN_FIELDS = ['date', 'url', 'bytes', 'sha256', 'fetched_at']
CONFLICT_FIELDS = ['date', 'manifest_sha256', 'new_sha256', 'new_bytes', 'path', 'fetched_at']


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


def coupon(v):
    """(running coupon as a decimal or None, normalised?). 1 means 1%: values of 0.5 or more are percent units."""
    c = fnum(v)
    if c is None or c <= 0:
        return None, False
    norm = c >= 0.5
    if norm:
        c = c / 100
    return (c if c <= 0.10 else None), norm


def quoted_spread(v):
    """Quoted par spread as a decimal, or None. Decimals (0.0365) and raw bp (365) both occur; 10-5,000 bp is kept."""
    q = fnum(v)
    if not q or q <= 0:
        return None
    if q >= 5:
        q = q / 10000  # raw basis points
    elif q >= 0.5:
        return None  # 0.5-5 is neither a plausible decimal spread nor a plausible bp figure
    return q if 0.001 <= q <= 0.5 else None


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


# --- which trades may price the index ---------------------------------------------------------------------------------
# Only a standard, new, USD senior single-name CDS trade of at least MIN_NOTIONAL prices a level: novations, packages,
# non-standard and post-priced terms, other currencies, other seniorities and odd lots price nothing.
MIN_NOTIONAL = 1_000_000
STANDARD_COUPONS = (0.01, 0.05)
# An unsigned upfront has two readings. The lower one is only plausible above this floor for its coupon: a name quoted on
# a 5% coupon does not trade at 12 bp (that is the wrong sign of a 23% upfront), and nothing trades under 10 bp.
PLAUSIBLE_FLOOR = {0.01: 0.001, 0.05: 0.01}


def _flag(v):
    return str(v if v is not None else '').strip().upper() == 'TRUE'


def eligible(r):
    # Event type is blank in older files (the field was filled later); only an explicit non-trade event (NOVA) is out.
    if str(r.get('Event type', '')).strip().upper() not in ('', 'TRAD'):
        return False
    if str(r.get('Notional currency-Leg 1', '')).strip().upper() != 'USD':
        return False
    if str(r.get('Other payment currency', '')).strip().upper() not in ('', 'USD'):
        return False
    if any(_flag(r.get(k)) for k in ('Package indicator', 'Non-standardized term indicator', 'Post-priced swap indicator')):
        return False
    if 'CDS CORP SN SR' not in str(r.get('UPI FISN', '')).upper():
        return False
    n = fnum(r.get('Notional amount-Leg 1'))
    return n is not None and n >= MIN_NOTIONAL


def standard_coupon(c):
    return next((s for s in STANDARD_COUPONS if c is not None and abs(c - s) < 1e-9), None)


def resolve_upfront(hi, lo, c, ref, pct):
    """(spread, decided_by) for an unsigned upfront, or (None, 'ambiguous'). DTCC does not publish who paid an upfront,
    so a trade has two readings. A reading under the floor for its coupon is impossible (a 5%-coupon name at 12 bp is
    the wrong sign of a 23% upfront), and then the trade decides alone. With two plausible readings the one nearer the
    reference (the day's eligible quotes, else the last official level) is taken; with no reference, a large upfront (2%
    or more) is read as wider than the coupon, and a small one is dropped."""
    if abs(hi - lo) < 1e-9:
        return hi, 'trade'  # a zero upfront: the spread is the coupon
    floor = PLAUSIBLE_FLOOR.get(c)
    if floor is None:
        return None, 'ambiguous'
    plausible = [x for x in (hi, lo) if floor <= x < 1]
    if len(plausible) == 1:
        return plausible[0], 'trade'
    if not plausible:
        return None, 'ambiguous'
    if ref is not None:
        return min(plausible, key=lambda x: abs(x - ref)), 'reference'
    return (hi, 'large_upfront') if pct >= 0.02 else (None, 'ambiguous')


def entity_of(r):
    text = (str(r.get('Underlying Asset Name', '')) + ' ' + str(r.get('UPI Underlier Name', '')) + ' ' +
            str(r.get('Underlier ID-Leg 1', ''))).upper()
    return next((eid for eid, (match, red) in ENTITIES.items() if match in text or red in text), None)


def slim(rows):
    """What the ledger needs from one file: every cancellation id, and the new and corrected trades of our names."""
    return ({r.get('Original Dissemination Identifier') or r.get('Dissemination Identifier') for r in rows if r.get('Action type') == 'EROR'},
            [r for r in rows if r.get('Action type') in ('NEWT', 'CORR') and entity_of(r)])


def ledger(files):
    """files: [(file_day, (cancelled_ids, rows))] in date order. Applies every cancellation and correction in the window,
    whichever later file it arrives in, and returns the surviving trades grouped by execution date (weekdays)."""
    cancelled = set().union(*(f[1][0] for f in files)) if files else set()
    keep = {}
    for _, (_, rows) in files:
        for r in rows:
            act = r.get('Action type')
            key = (r.get('Original Dissemination Identifier') or r.get('Dissemination Identifier')) if act == 'CORR' else r.get('Dissemination Identifier')
            if key in cancelled or r.get('Dissemination Identifier') in cancelled:
                continue
            if act == 'NEWT' and key in keep:
                continue
            keep[key] = r
    by_day = {}
    for r in keep.values():
        d = str(r.get('Execution Timestamp', ''))[:10]
        try:
            if dt.date.fromisoformat(d).weekday() >= 5:
                continue
        except ValueError:
            continue
        by_day.setdefault(d, []).append(r)
    return by_day


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


def parse_day(rows, day, prev, cleaned=False):
    """Returns (levels, trades): levels[entity] = daily row; trades = list of canonical trade rows.
    rows are one file's rows (cleaned here, same-day executions only) or, with cleaned=True, a ledger day."""
    if not cleaned:
        rows = clean(rows, day)
    levels, trades = {}, []
    for eid, (match, red) in ENTITIES.items():
        quoted, ups = [], []
        for r in rows:
            text = (str(r.get('Underlying Asset Name', '')) + ' ' + str(r.get('UPI Underlier Name', '')) + ' ' + str(r.get('Underlier ID-Leg 1', ''))).upper()
            if not ((match in text or red in text) and is_cds(r) and eligible(r)):
                continue
            ex, exp = ts(r.get('Execution Timestamp')), ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
            if ex is None or exp is None or exp != on_the_run_5y(ex):
                continue
            C, norm = coupon(r.get('Fixed rate-Leg 1'))
            step = ex + DAY
            T = (exp - step) / (365.25 * DAY)
            accr = (step - last_imm(ex)) / (360 * DAY)
            N = fnum(r.get('Notional amount-Leg 1'))
            amt = fnum(r.get('Other payment amount'))  # blank stays None: no upfront, no reading
            ufro = str(r.get('Other payment type', '')).strip().upper() == 'UFRO'
            pct = amt / N if (ufro and N and N > 0 and amt is not None) else None
            meta = {'dissemination_id': r.get('Dissemination Identifier', ''), 'exec_ts': r.get('Execution Timestamp', ''),
                    'coupon': standard_coupon(C) or C, 'coupon_normalised': norm, 'notional_capped': '+' in str(r.get('Notional amount-Leg 1', ''))}
            q = quoted_spread(r.get('Spread-Leg 1'))
            if fnum(r.get('Spread-Leg 1')):
                if q is not None:
                    quoted.append((q, ex, dict(meta, kind='quoted', upfront_pct=None if pct is None else round(abs(pct) * 100, 4))))
            elif pct is not None and standard_coupon(C):
                C = standard_coupon(C)
                ups.append((spread_from_upfront(abs(pct), C, T, accr), spread_from_upfront(-abs(pct), C, T, accr), abs(pct), ex,
                            dict(meta, kind='upfront', upfront_pct=round(abs(pct) * 100, 4))))
        # Reference for two plausible readings: the day's eligible quotes (a $1 or odd-lot quote is not eligible), else
        # the last official level.
        ref = statistics.median([x[0] for x in quoted]) if quoted else (prev[eid] / 10000 if prev.get(eid) else None)
        implied, n_amb, n_ref = [], 0, 0
        for hi, lo, p, ex, meta in ups:
            s, by = resolve_upfront(hi, lo, meta['coupon'], ref, p)
            if s is None:
                n_amb += 1
                continue
            n_ref += by != 'trade'
            implied.append((s, ex, dict(meta, sign_by=by)))
        if not quoted and not implied:
            continue
        official = quoted + implied
        kind = 'mixed' if (quoted and implied) else ('quoted' if quoted else 'upfront_implied')
        vals = [x[0] for x in official]
        med = statistics.median(vals)
        loo = max(abs(statistics.median(vals[:i] + vals[i + 1:]) - med) for i in range(len(vals))) if len(vals) > 1 else None
        last = max(x[1] for x in official)
        bp = lambda xs: round(statistics.median([x[0] for x in xs]) * 10000, 1) if xs else None
        levels[eid] = {'date': day, 'entity': eid, 'official_bp': round(med * 10000, 1), 'official_kind': kind,
                       'official_trades': len(vals), 'quoted_median_bp': bp(quoted), 'quoted_trades': len(quoted),
                       'upfront_implied_bp': bp(implied), 'upfront_trades': len(implied),
                       'loo_bp': None if loo is None else round(loo * 10000, 1),
                       'last_trade': dt.datetime.fromtimestamp(last, dt.timezone.utc).isoformat(),
                       'n_capped': sum(1 for x in official if x[2]['notional_capped']),
                       'n_percent_coupon_normalised': sum(1 for x in quoted + implied if x[2]['coupon_normalised']),
                       'n_ambiguous': n_amb, 'n_sign_from_reference': n_ref}
        for s, ex, meta in official:
            trades.append({'date': day, 'entity': eid, 'spread_bp': round(s * 10000, 2), **meta})
    return levels, trades


def rows_of(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode('utf-8', 'replace'))))


def valid_zip(b):
    """A published day: a ZIP whose first member is a CSV with a header row."""
    if not b or b[:2] != b'PK':
        return False
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        name = z.namelist()[0]
        return name.lower().endswith('.csv') and bool(z.read(name)[:200].strip())
    except Exception:
        return False


def download(day, tries=3):
    req = urllib.request.Request(URL.format(day.replace('-', '_')), headers={'User-Agent': UA, 'Accept': '*/*'})
    for k in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
            if valid_zip(b):
                return b
        except Exception:
            pass
        time.sleep(1.5 * (k + 1))
    return None


def fetch(day, raw, offline):
    """Bytes of the day's file, or None. Only a valid ZIP is ever written, so a failure is retried on the next run;
    a file on disk is never overwritten."""
    p = raw / f'{day}.zip'
    if p.exists():
        b = p.read_bytes()
        if valid_zip(b):
            return b
        p.unlink()  # an empty or broken file left by an older version of this script: fetch again
    if offline:
        return None
    b = download(day)
    if b is None:
        return None
    tmp = p.with_suffix('.part')
    tmp.write_bytes(b)
    tmp.replace(p)
    return b


def load_manifest(path):
    if not path.exists():
        return {}
    return {r['date']: r for r in csv.DictReader(open(path))}


def append_rows(path, fields, rows):
    new = not path.exists()
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if new:
            w.writeheader()
        w.writerows(rows)


def check_provenance(days, blobs, raw, man_path, conf_path):
    """Every raw file must match the append-only manifest. New days are appended. A day whose bytes differ from the
    manifest is a conflict: its bytes are kept under conflicts/, it is logged, and it is left out of the tables."""
    manifest = load_manifest(man_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    added, conflicts = [], []
    for d in days:
        b = blobs[d]
        if not b:
            continue
        h = hashlib.sha256(b).hexdigest()
        if d in manifest and manifest[d]['sha256'] != h:
            cdir = raw / 'conflicts'; cdir.mkdir(exist_ok=True)
            cp = cdir / f'{d}-{h[:12]}.zip'
            if not cp.exists():
                cp.write_bytes(b)
            conflicts.append({'date': d, 'manifest_sha256': manifest[d]['sha256'], 'new_sha256': h, 'new_bytes': len(b),
                              'path': str(cp.relative_to(ROOT)), 'fetched_at': now})
            blobs[d] = None
            (raw / f'{d}.zip').unlink(missing_ok=True)
        elif d not in manifest:
            added.append({'date': d, 'url': URL.format(d.replace('-', '_')), 'bytes': len(b), 'sha256': h, 'fetched_at': now})
    if added:
        append_rows(man_path, MAN_FIELDS, sorted(added, key=lambda r: r['date']))
    if conflicts:
        seen = {(r['date'], r['new_sha256']) for r in csv.DictReader(open(conf_path))} if conf_path.exists() else set()
        append_rows(conf_path, CONFLICT_FIELDS, [c for c in conflicts if (c['date'], c['new_sha256']) not in seen])
        print(f'WARNING: {len(conflicts)} raw file(s) differ from the manifest ({", ".join(c["date"] for c in conflicts)}); '
              'left out of the tables and logged in manifests/dtcc_conflicts.csv. Investigate.', file=sys.stderr)
    return len(added), conflicts


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

    added, conflicts = check_provenance(days, blobs, raw, man_path, ROOT / 'manifests' / 'dtcc_conflicts.csv')

    # One ledger over every file: a cancellation or correction applies whichever later file it arrives in, and a trade
    # reported late is priced on the day it was executed.
    by_day = ledger([(d, slim(rows_of(blobs[d]))) for d in days if blobs[d]])
    first = min((d for d in days if blobs[d]), default=None)
    prev, daily, trades = {}, [], []
    for d in sorted(x for x in by_day if first and x >= first):
        lv, tr = parse_day(by_day[d], d, prev, cleaned=True)
        for eid, v in lv.items():
            prev[eid] = v['official_bp']
            daily.append(v)
        trades.extend(tr)
    for name, fields, rows in (('cds_daily.csv', DAILY_FIELDS, daily), ('cds_trades.csv', TRADE_FIELDS, trades)):
        with open(canon / name, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f'{sum(1 for d in days if blobs[d])} raw files (hashes checked, {added} new, {len(conflicts)} conflicts), '
          f'{len(daily)} entity-days, {len(trades)} priced trades -> data/canonical/', file=sys.stderr)


if __name__ == '__main__':
    main()
