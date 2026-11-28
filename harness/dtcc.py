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
  - quoted spreads as decimals (0.0365) or raw bp (365) are both read; unresolved units rejected; 0 or blank = not quoted
  - an upfront is read only when Other payment type is UFRO (UWIN = unwind, not an entry price); blank = missing, not 0
  - upfront trades on a standard coupon (1% or 5%) converted to a par spread (flat hazard, 40% recovery, 4.5% flat rate).
    DTCC upfronts are unsigned, so each has two readings. Two distinct feasible roots are ambiguous and excluded.
    Only a root below zero hazard proves the alternative infeasible in this model; a numerical-domain failure blocks
    the trade. Quotes and previous levels never select a sign. The fixed model is not an ISDA validation.
  - suspected basket legs quarantined: eligible on-the-run CDS upfronts matching to a millionth of notional across
    our names within 120 seconds. This is a heuristic, not proof of a basket (n_basket_legs).
  - quoted and upfront-implied spreads are kept in separate columns. Official level: the median of all the day's priced
    trades; official_kind says what it rests on (quoted, upfront_implied or mixed). Quoted-only was tested and rejected:
    on Oracle the two differ by a median 1.3 bp, and dropping implied prints more than doubles the days resting on
    under 3 trades (research/findings/2026-11-26-p0-tape-hygiene.md). Methods can still choose prints: quoted_first.

Usage: python harness/dtcc.py [--days 400] [--offline]
"""
import argparse, concurrent.futures as cf, csv, datetime as dt, hashlib, io, json, math, pathlib, statistics, sys, time, urllib.request, zipfile

try:
    from .dtcc_lifecycle import resolve_lifecycle, validate_columns, validate_rows, SourceSchemaError
except ImportError:
    from dtcc_lifecycle import resolve_lifecycle, validate_columns, validate_rows, SourceSchemaError

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
                'n_ambiguous', 'n_sign_from_reference', 'n_basket_legs', 'n_model_domain_errors', 'n_invalid_quotes']
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
    """Decode the observed decimal/raw-bp conventions, without an empirical spread floor.
    Values in [0.5, 5) have unresolved units and are rejected, rather than guessed."""
    q = fnum(v)
    if not q or q <= 0:
        return None
    if q >= 5:
        q = q / 10000  # raw basis points
    elif q >= 0.5:
        return None
    return q if math.isfinite(q) and q > 0 else None


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


def upfront_solution(upfront, C, T, accr):
    """Invert only a bracketed, validated root of this simplified model.

    The finite hazard domain is a numerical support limit, not an economic
    impossibility test. Only a target below the zero-hazard PV proves that no
    nonnegative-hazard root exists. Callers must keep these failures distinct.
    """
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (upfront, C, T, accr)) or C <= 0 or T <= 0 or accr < 0:
        return {'spread': None, 'error': 'invalid_model_input'}
    target = upfront + C * accr
    lo, hi, tol = 0.0, 3.0, 1e-10
    p0, v0 = legs(lo, T)
    p1, v1 = legs(hi, T)
    lower, upper = p0 - C * v0, p1 - C * v1
    if not all(math.isfinite(x) for x in (target, lower, upper)) or upper <= lower:
        return {'spread': None, 'error': 'invalid_model_domain'}
    if target < lower - tol:
        return {'spread': None, 'error': 'no_nonnegative_hazard_root'}
    if target > upper + tol:
        return {'spread': None, 'error': 'outside_model_domain'}
    for _ in range(90):
        m = (lo + hi) / 2
        p, v = legs(m, T)
        if p - C * v < target:
            lo = m
        else:
            hi = m
    p, v = legs((lo + hi) / 2, T)
    residual = p - C * v - target
    if not (math.isfinite(p) and math.isfinite(v) and v > 0 and abs(residual) <= tol):
        return {'spread': None, 'error': 'root_residual_failure'}
    return {'spread': p / v, 'error': None}


def spread_from_upfront(upfront, C, T, accr):
    return upfront_solution(upfront, C, T, accr)['spread']


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


def resolve_upfront(hi, lo, c=None, ref=None, pct=None):
    """Resolve validated candidate roots, never infer payment direction from a reference.

    Candidate dictionaries preserve why a root is absent. Bare numeric candidates
    remain supported for diagnostics; a missing numeric root is not proof that its
    sign is impossible. c/ref/pct are retained only for caller compatibility.
    """
    candidates = [x if isinstance(x, dict) else {'spread': x, 'error': None if isinstance(x, (int, float)) and math.isfinite(x) and x >= 0 else 'unknown_root_failure'} for x in (hi, lo)]
    if any(x['error'] not in (None, 'no_nonnegative_hazard_root') or
           (x['error'] is None and not (isinstance(x['spread'], (int, float)) and math.isfinite(x['spread']) and x['spread'] >= 0)) for x in candidates):
        return None, 'model_domain_error'
    valid = [x['spread'] for x in candidates if x['error'] is None and x['spread'] is not None]
    if not valid:
        return None, 'model_domain_error'
    if len(valid) == 1 or abs(valid[0] - valid[1]) < 1e-9:
        return valid[0], 'trade'
    return None, 'ambiguous'


BASKET_SECONDS = 120


def basket_legs(rows):
    """Quarantine suspected basket legs among otherwise eligible on-the-run CDS rows.
    Matching payment fractions and timing do not prove that trades form a basket."""
    ups = []
    for r in rows:
        e = entity_of(r)
        if not e or not eligible(r) or not is_cds(r) or not standard_coupon(coupon(r.get('Fixed rate-Leg 1'))[0]) or str(r.get('Other payment type', '')).strip().upper() != 'UFRO':
            continue
        n, amt, ex = fnum(r.get('Notional amount-Leg 1')), fnum(r.get('Other payment amount')), ts(r.get('Execution Timestamp'))
        exp = ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
        if n and n > 0 and amt and ex is not None and exp == on_the_run_5y(ex):
            ups.append((abs(amt / n), ex, e, r.get('Dissemination Identifier')))
    out = set()
    for i, a in enumerate(ups):
        for b in ups[i + 1:]:
            if a[2] != b[2] and abs(a[0] - b[0]) <= 1e-6 and abs(a[1] - b[1]) <= BASKET_SECONDS:
                out.update((a[3], b[3]))
    return out


def entity_of(r):
    text = (str(r.get('Underlying Asset Name', '')) + ' ' + str(r.get('UPI Underlier Name', '')) + ' ' +
            str(r.get('Underlier ID-Leg 1', ''))).upper()
    return next((eid for eid, (match, red) in ENTITIES.items() if match in text or red in text), None)


def slim(rows):
    """Keep lifecycle events before entity/eligibility filtering.

    A correction can remove a tracked name, change currency or invalidate terms;
    filtering it first would resurrect the original price.
    """
    return ({r.get('Original Dissemination Identifier') or r.get('Dissemination Identifier')
             for r in rows if r.get('Action type') == 'EROR'}, list(rows))


class LedgerDays(dict):
    """Execution-day rows, with aggregate bounded-ledger quality diagnostics."""


def ledger(files, track_all=False):
    rows, quality = resolve_lifecycle([r for _, (_, rs) in files for r in rs], (lambda r: True) if track_all else entity_of)
    by_day = LedgerDays()
    for r in rows:
        d = str(r.get('Execution Timestamp', ''))[:10]
        try:
            if dt.date.fromisoformat(d).weekday() >= 5:
                continue
        except ValueError:
            quality['issue_counts']['invalid_execution_date'] = quality['issue_counts'].get('invalid_execution_date', 0) + 1
            quality['status'] = 'partial'
            continue
        by_day.setdefault(d, []).append(r)
    by_day.quality = quality
    return by_day


def clean(rows, day):
    return ledger([(day, slim(rows))], track_all=True).get(day, [])


def parse_day(rows, day, prev, cleaned=False, quality=None):
    """Returns (levels, trades): levels[entity] = daily row; trades = list of canonical trade rows.
    rows are one file's rows (cleaned here, same-day executions only) or, with cleaned=True, a ledger day.
    If supplied, quality receives diagnostics for every entity, including days with no accepted price."""
    if not cleaned:
        rows = clean(rows, day)
    baskets = basket_legs(rows)
    levels, trades = {}, []
    for eid, (match, red) in ENTITIES.items():
        quoted, ups, n_basket = [], [], 0
        reasons, candidates = set(), []
        n_invalid, n_domain = 0, 0
        for r in rows:
            text = (str(r.get('Underlying Asset Name', '')) + ' ' + str(r.get('UPI Underlier Name', '')) + ' ' + str(r.get('Underlier ID-Leg 1', ''))).upper()
            if not ((match in text or red in text) and is_cds(r) and eligible(r)):
                continue
            ex, exp = ts(r.get('Execution Timestamp')), ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
            if ex is None or exp is None or exp != on_the_run_5y(ex):
                continue
            if r.get('Dissemination Identifier') in baskets:
                n_basket += 1
                reasons.add('suspected_basket_leg')
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
            raw_quote = str(r.get('Spread-Leg 1') or '').strip()
            # Zero is the source's no-quote sentinel, not a priced observation.
            if raw_quote and fnum(raw_quote) != 0:
                if q is not None:
                    quoted.append((q, ex, dict(meta, kind='quoted', upfront_pct=None if pct is None else round(abs(pct) * 100, 4))))
                else:
                    n_invalid += 1
                    reasons.add('invalid_or_unresolved_quote_units')
            elif pct is not None and standard_coupon(C):
                C = standard_coupon(C)
                ups.append((upfront_solution(abs(pct), C, T, accr), upfront_solution(-abs(pct), C, T, accr), abs(pct), ex,
                            dict(meta, kind='upfront', upfront_pct=round(abs(pct) * 100, 4))))
            elif pct is not None:
                n_domain += 1
                reasons.add('unsupported_coupon')
            else:
                reasons.add('missing_price')
        implied, n_amb = [], 0
        for hi, lo, p, ex, meta in ups:
            candidates.extend(x['spread'] for x in (hi, lo) if x['error'] is None)
            s, by = resolve_upfront(hi, lo)
            if s is None:
                if by == 'ambiguous':
                    n_amb += 1
                    reasons.add('ambiguous_upfront_sign')
                else:
                    n_domain += 1
                    reasons.update(x['error'] for x in (hi, lo) if x['error'] not in (None, 'no_nonnegative_hazard_root'))
                    reasons.add('model_domain_error')
                continue
            implied.append((s, ex, dict(meta, sign_by=by)))
        diagnostic = {'accepted_trades': len(quoted) + len(implied), 'ambiguous': n_amb, 'model_domain_errors': n_domain,
                      'invalid_quotes': n_invalid, 'basket_legs': n_basket,
                      'candidate_interval_bp': {'low': round(min(candidates) * 10000, 1), 'high': round(max(candidates) * 10000, 1)} if candidates else None,
                      'reasons': sorted(reasons)}
        if not diagnostic['accepted_trades']:
            diagnostic['reasons'] = sorted(set(diagnostic['reasons']) | {'no_accepted_price'})
        if quality is not None:
            quality[eid] = diagnostic
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
                       'n_ambiguous': n_amb, 'n_sign_from_reference': 0, 'n_basket_legs': n_basket,
                       'n_model_domain_errors': n_domain, 'n_invalid_quotes': n_invalid}
        for s, ex, meta in official:
            trades.append({'date': day, 'entity': eid, 'spread_bp': round(s * 10000, 2), **meta})
    return levels, trades


def rows_of(b):
    """Strict source schema, including valid empty files; renamed fields never mean no trades."""
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        reader = csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode('utf-8-sig')), strict=True)
        validate_columns(reader.fieldnames)
        rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise SourceSchemaError('DTCC row width does not match header')
    validate_rows(rows)
    return rows


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

    parsed, invalid = {}, []
    for d in days:
        if not blobs[d]:
            continue
        try:
            parsed[d] = rows_of(blobs[d])
        except (SourceSchemaError, UnicodeError, csv.Error, zipfile.BadZipFile, IndexError):
            invalid.append(d)
    by_day = ledger([(d, slim(rows)) for d, rows in sorted(parsed.items())])
    missing = [d for d in days if not blobs[d] and not any(c['date'] == d for c in conflicts)]
    reasons = []
    if missing:
        reasons.append('requested_file_unavailable')
    if invalid:
        reasons.append('invalid_source_file')
    if conflicts:
        reasons.append('source_hash_conflict')
    if by_day.quality['status'] != 'complete':
        reasons.append('lifecycle_quarantine')
    status = 'invalid' if invalid or conflicts else 'unavailable' if not parsed else 'partial' if reasons else 'complete'
    source_quality = {'status': status, 'calculation_eligible': status == 'complete', 'reasons': reasons,
                      'scope': 'requested_files_in_bounded_window', 'requested_dates': days,
                      'available_dates': sorted(parsed), 'missing_dates': missing, 'invalid_dates': invalid,
                      'conflict_dates': [c['date'] for c in conflicts], 'lifecycle': by_day.quality,
                      'bounded_history': True, 'point_in_time_verified': False, 'settlement_eligible': False}
    quality_path = canon / 'cds_source_quality.json'
    quality_doc = {'decoder_version': 'dtcc-strict-v1', 'research_only': True,
                   'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'source_quality': source_quality}
    quality_path.write_text(json.dumps(quality_doc, indent=2, sort_keys=True) + '\n')
    if status in ('invalid', 'unavailable'):
        raise SystemExit('DTCC source ' + status + ': canonical price files preserved; see cds_source_quality.json')

    first = min(parsed, default=None)
    prev, daily, trades, diagnostics = {}, [], [], {}
    for d in sorted(set(parsed) | {x for x in by_day if first and x >= first}):
        day_quality = {}
        lv, tr = parse_day(by_day.get(d, []), d, prev, cleaned=True, quality=day_quality)
        diagnostics[d] = day_quality
        for eid, v in lv.items():
            prev[eid] = v['official_bp']
            daily.append(v)
        trades.extend(tr)
    quality_doc['pricing_quality'] = diagnostics
    for name, fields, rows in (('cds_daily.csv', DAILY_FIELDS, daily), ('cds_trades.csv', TRADE_FIELDS, trades)):
        tmp = canon / (name + '.tmp')
        with open(tmp, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
        tmp.replace(canon / name)
    quality_doc['canonical_sha256'] = {name: hashlib.sha256((canon / name).read_bytes()).hexdigest()
                                       for name in ('cds_daily.csv', 'cds_trades.csv')}
    quality_tmp = quality_path.with_suffix('.json.tmp')
    quality_tmp.write_text(json.dumps(quality_doc, indent=2, sort_keys=True) + '\n')
    quality_tmp.replace(quality_path)
    print('DTCC source quality: ' + status + '; research candidate only; settlement eligible: false', file=sys.stderr)
    print(f'{sum(1 for d in days if blobs[d])} raw files (hashes checked, {added} new, {len(conflicts)} conflicts), '
          f'{len(daily)} entity-days, {len(trades)} priced trades -> data/canonical/', file=sys.stderr)


if __name__ == '__main__':
    main()
