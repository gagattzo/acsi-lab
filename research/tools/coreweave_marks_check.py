"""CoreWeave's two DTCC readings against dated public CDS marks, and the rating-band rule that was rejected.

1. For every mark in harness/sign_attestations.json (plus an approximate early-June 2026 low, reported without an
   exact date and therefore not in the marks file), the median high and low reading of the CoreWeave trades the
   decoder can price: eligible on-the-run 5-year upfront-only trades after the cross-file lifecycle ledger (cancelled
   and quarantined reports removed) and the basket-leg screen, on the mark's day or the nearest earlier trading day
   within 2 days, and which reading the mark sides with.
2. Leave-one-out: the attested-side rule (harness/dtcc.py attested_side) on that day using ONLY marks dated strictly
   before it.
3. Coverage: CoreWeave trading days priced with all marks, by month.
4. The rejected alternative: a reading implying a rating 4+ notches better than the S&P rating is ruled out. Scored
   on every model-matched answer-key trade (reports both a quote and an upfront; exactly one coupon fits the quote)
   for a short list of names with well-known S&P ratings. A WRONG pick throws out the true reading. As in
   research/tools/sign_ground_truth.py, these are new reports read file by file, without the lifecycle ledger.

Usage: python research/tools/coreweave_marks_check.py   (needs data/raw/dtcc from harness/dtcc.py)
"""
import bisect, collections, datetime as dt, math, pathlib, statistics, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
import dtcc  # noqa: E402

RAW = ROOT / 'data' / 'raw' / 'dtcc'
GRID = [('AAA', 20), ('AA+', 26), ('AA', 34), ('AA-', 41), ('A+', 49), ('A', 58), ('A-', 68), ('BBB+', 81), ('BBB', 95),
        ('BBB-', 120), ('BB+', 151), ('BB', 190), ('BB-', 237), ('B+', 296), ('B', 370), ('B-', 488), ('CCC+', 644),
        ('CCC', 850), ('CCC-', 980)]  # Groundbreaker grid, as in api/cds.js
IDX = {r: i for i, (r, _) in enumerate(GRID)}
BOUNDS = [math.sqrt(GRID[i][1] * GRID[i + 1][1]) for i in range(len(GRID) - 1)]
notch = lambda bp: bisect.bisect_right(BOUNDS, bp)
# Names chosen for clear, well-known S&P issuer ratings, checked by hand; edit if a rating has changed.
SP = {'AUTOZONE': 'BBB', 'RTX CORP': 'BBB+', 'CATERPILLAR': 'A', 'HONEYWELL': 'A', 'ALLSTATE': 'A-',
      'MOTOROLA SOLUTIONS': 'BBB', 'BOEING': 'BBB-', 'ORACLE': 'BBB-', 'META PLATFORMS': 'AA-'}
APPROX = [('2026-06-05', 452.0, 'early-June low, date approximate (The Next Web; Dave Friedman)')]


def readings(pct, C, ex, exp):
    step = ex + dtcc.DAY
    T, accr = (exp - step) / (365.25 * dtcc.DAY), (step - dtcc.last_imm(ex)) / (360 * dtcc.DAY)
    return dtcc.upfront_solution(pct, C, T, accr), dtcc.upfront_solution(-pct, C, T, accr)


def coreweave_days(files):
    """CoreWeave trades the decoder can price, by execution day: ledger-resolved, eligible, on-the-run, upfront-only."""
    out = collections.defaultdict(list)
    by_day = dtcc.ledger([(d, dtcc.slim(rows)) for d, rows in files])
    for day, rows in by_day.items():
        baskets = dtcc.basket_legs(rows)
        for r in rows:
            if dtcc.entity_of(r) != 'CRWV' or not (dtcc.is_cds(r) and dtcc.eligible(r)) or r.get('Dissemination Identifier') in baskets:
                continue
            ex, exp = dtcc.ts(r.get('Execution Timestamp')), dtcc.ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
            N, amt = dtcc.fnum(r.get('Notional amount-Leg 1')), dtcc.fnum(r.get('Other payment amount'))
            Cr = dtcc.standard_coupon(dtcc.coupon(r.get('Fixed rate-Leg 1'))[0])
            raw_q = str(r.get('Spread-Leg 1') or '').strip()
            if ex is None or exp != dtcc.on_the_run_5y(ex) or not Cr or (raw_q and dtcc.fnum(raw_q) != 0):
                continue
            if str(r.get('Other payment type', '')).strip().upper() != 'UFRO' or not N or N <= 0 or amt is None:
                continue
            hi, lo = readings(abs(amt / N), Cr, ex, exp)
            if hi['spread'] is not None:
                out[day].append((hi['spread'] * 1e4, None if lo['spread'] is None else lo['spread'] * 1e4, Cr))
    return out


def scan():
    crwv, key, files = collections.defaultdict(list), [], []
    for p in sorted(RAW.glob('*.zip')):
        rows = dtcc.rows_of(p.read_bytes())
        files.append((p.stem, rows))
        for r in rows:
            if r.get('Action type') != 'NEWT' or not (dtcc.is_cds(r) and dtcc.eligible(r)):
                continue
            ex, exp = dtcc.ts(r.get('Execution Timestamp')), dtcc.ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
            if ex is None or exp is None or exp != dtcc.on_the_run_5y(ex):
                continue
            N, amt = dtcc.fnum(r.get('Notional amount-Leg 1')), dtcc.fnum(r.get('Other payment amount'))
            if not (str(r.get('Other payment type', '')).strip().upper() == 'UFRO' and N and N > 0 and amt and amt > 0):
                continue
            q = dtcc.quote_status(r.get('Spread-Leg 1'))[0]
            Cr = dtcc.standard_coupon(dtcc.coupon(r.get('Fixed rate-Leg 1'))[0])
            day = str(r.get('Execution Timestamp'))[:10]
            if q:
                name = str(r.get('Underlying Asset Name') or '').upper()
                fits = []
                for C in ([Cr] if Cr else [0.01, 0.05]):
                    hi, lo = readings(amt / N, C, ex, exp)
                    for x, o in ((hi, lo), (lo, hi)):
                        if x['spread'] is not None and abs(x['spread'] - q) <= max(0.0002, 0.02 * q):
                            fits.append((C, x['spread'] * 1e4, None if o['spread'] is None else o['spread'] * 1e4))
                if len({f[0] for f in fits}) == 1 and fits[0][2] is not None:
                    key.append((name, day) + fits[0][1:])
    return coreweave_days(files), key


def main():
    att = dtcc.load_attestations()
    crwv, key = scan()
    D = dt.date.fromisoformat
    print('1-2. CoreWeave: DTCC readings vs public marks; rule decided with earlier marks only')
    print(f"{'mark':10s} {'bp':>6s}  {'DTCC day':10s} {'high':>5s} {'low':>5s}  mark sides with  rule (earlier marks only)")
    tests = [(m['date'], m['reported_bp'], m['reported_by']) for m in att['marks'] if m['entity'] == 'CRWV'] + APPROX
    for d, bp, who in sorted(tests):
        days = [x for x in sorted(crwv) if 0 <= (D(d) - D(x)).days <= 2]
        if not days:
            print(f'{d} {bp:6.0f}  no priced-eligible CoreWeave upfront within 2 days')
            continue
        x = days[-1]
        two = [t for t in crwv[x] if t[1] is not None]
        hi = statistics.median(t[0] for t in crwv[x])
        lo = statistics.median(t[1] for t in two) if two else None
        side = 'high' if lo is None or abs(hi - bp) < abs(lo - bp) else 'LOW'
        earlier = dict(att, marks=[m for m in att['marks'] if m['date'] < d])
        res = [dtcc.attested_side('CRWV', x, C, h / 1e4, l / 1e4, earlier) for h, l, C in two]
        priced = [s * 1e4 for s, _, _ in res if s is not None]
        why = collections.Counter(w for _, w, _ in res)
        rule = (f"{statistics.median(priced):.0f} bp, {100 * (statistics.median(priced) - bp) / bp:+.0f}% vs mark"
                if priced else 'abstains') + f" {dict(why)}"
        print(f"{d} {bp:6.0f}  {x} {hi:5.0f} {('%5.0f' % lo) if lo else '    -'}  {side:15s}  {rule}")
    print('\n3. CoreWeave trading days priced with all marks, by month:')
    cov = collections.defaultdict(lambda: [0, 0])
    for d, xs in crwv.items():
        ok = any(l is None or dtcc.attested_side('CRWV', d, C, h / 1e4, l / 1e4, att)[0] is not None for h, l, C in xs)
        cov[d[:7]][0] += ok
        cov[d[:7]][1] += 1
    print('  ' + ' '.join(f'{m}:{a}/{b}' for m, (a, b) in sorted(cov.items())))
    print('\n4. Rejected: rule out readings 4+ notches better than the S&P rating (answer-key trades, true reading known)')
    for nm, rating in SP.items():
        ks = [k for k in key if nm in k[0]]
        k_ = IDX[rating]
        wrong = [k for k in ks if notch(k[2]) < k_ - 3 <= notch(k[3])]
        right = [k for k in ks if notch(k[3]) < k_ - 3 <= notch(k[2])]
        ex = min(wrong, key=lambda k: k[2]) if wrong else None
        print(f"  {nm:20s} S&P {rating:4s} trades {len(ks):4d}  right {len(right):4d}  WRONG {len(wrong):4d}" +
              (f"  e.g. {ex[1]} true {ex[2]:.0f} bp, rule picks {ex[3]:.0f} bp" if ex else '') +
              ('  (cannot fire: nothing is 4+ notches better)' if k_ - 3 <= 0 else ''))


if __name__ == '__main__':
    main()
