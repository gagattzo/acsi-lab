"""Score the unsigned-upfront sign rules against the tape's own answer key.

Usage:  python research/tools/sign_ground_truth.py [/path/to/acsi-lab]   (default: this checkout)
Needs:  data/raw/dtcc/*.zip in that checkout (python harness/dtcc.py downloads them) and its harness/dtcc.py, whose
        eligibility filter and model solver are used unchanged.

Answer key: eligible, on-the-run 5-year NEWT records (all reference entities) that report BOTH a quoted spread and an
upfront payment (UFRO). The quote shows which of the two readings of the unsigned upfront is right. A blank coupon is
resolved to whichever standard coupon (1% or 5%) reproduces the quote within max(2 bp, 2%); records that match neither
or both are left out and counted. Records are read file by file (no cross-file lifecycle): the test is about the
relation between a record's own quote and upfront, not about which trades survive.

Rules scored:
  floor   the 28 Nov 2026 contract-check rule: a reading under 10 bp (1% coupon) or 100 bp (5% coupon) is impossible,
          so the other reading is taken; when both readings clear the floor the record is excluded here (that rule then
          used quotes or the previous level, which the answer key cannot test)
  strict  dtcc-strict-v1 (acsi-lab main 09990a6): a reading is taken only when the other has no valid model root;
          two valid roots exclude the record

Also prints: names quoted under 100 bp on a 5% coupon and how the floor reads their upfront-only trades within 5 days,
and every quoted spread outside the 10-5,000 bp sanity range that dtcc-strict-v1 removed.
"""
import collections
import datetime as dt
import pathlib
import sys

LAB = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LAB / 'harness'))
import dtcc as d  # noqa: E402

FLOOR = {0.01: 0.001, 0.05: 0.01}


def readings(pct, C, ex, exp):
    step = ex + d.DAY
    T, accr = (exp - step) / (365.25 * d.DAY), (step - d.last_imm(ex)) / (360 * d.DAY)
    return d.upfront_solution(pct, C, T, accr), d.upfront_solution(-pct, C, T, accr)


def main():
    files = sorted((LAB / 'data' / 'raw' / 'dtcc').glob('*.zip'))
    res, wrong = collections.Counter(), []
    tight5, up5, out_of_range = collections.defaultdict(list), collections.defaultdict(list), []
    for p in files:
        for r in d.rows_of(p.read_bytes()):
            if r.get('Action type') != 'NEWT' or not (d.is_cds(r) and d.eligible(r)):
                continue
            ex, exp = d.ts(r.get('Execution Timestamp')), d.ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
            if ex is None or exp is None or exp != d.on_the_run_5y(ex):
                continue
            name = str(r.get('Underlying Asset Name') or '?').strip().upper()[:24]
            qraw, q = d.fnum(r.get('Spread-Leg 1')), None
            if qraw and qraw > 0:
                q = qraw if qraw < 0.5 else (qraw / 10000 if qraw >= 5 else None)
            if q is not None and not (0.001 <= q <= 0.5):
                out_of_range.append((p.stem, name, round(q * 1e4, 2)))
            Cr = d.standard_coupon(d.coupon(r.get('Fixed rate-Leg 1'))[0])
            N, amt = d.fnum(r.get('Notional amount-Leg 1')), d.fnum(r.get('Other payment amount'))
            up = str(r.get('Other payment type', '')).strip().upper() == 'UFRO' and N and N > 0 and amt is not None and amt > 0
            if q and Cr == 0.05 and q < 0.01:
                tight5[name].append((p.stem, q))
            if up and not q and Cr == 0.05:
                up5[name].append((p.stem, ex, exp, amt / N))
            if not (q and up):
                continue
            matches = []
            for C in ([Cr] if Cr else [0.01, 0.05]):
                hi, lo = readings(amt / N, C, ex, exp)
                for k, x in (('hi', hi), ('lo', lo)):
                    if x['spread'] is not None and abs(x['spread'] - q) <= max(0.0002, 0.02 * q):
                        matches.append((C, k, hi, lo))
            if len(matches) != 1:
                res['left out (no unique match)'] += 1
                continue
            C, k, hi, lo = matches[0]
            truth = (hi if k == 'hi' else lo)['spread']
            res[(C, 'records')] += 1
            res[(C, 'true reading ' + k)] += 1
            plaus = [x['spread'] for x in (hi, lo) if x['spread'] is not None and FLOOR[C] <= x['spread'] < 1]
            if len(plaus) == 1:
                ok = abs(plaus[0] - truth) < 1e-12
                res[(C, 'floor decides, right' if ok else 'floor decides, WRONG')] += 1
                if not ok and len(wrong) < 10:
                    wrong.append(f'{p.stem} {name} quoted {q * 1e4:.1f} bp, floor reads {plaus[0] * 1e4:,.0f} bp')
            else:
                res[(C, 'floor excludes (both readings clear it)' if len(plaus) == 2 else 'floor excludes (neither clears it)')] += 1
            s, _ = d.resolve_upfront(hi, lo)
            res[(C, 'strict excludes' if s is None else ('strict accepts, right' if abs(s - truth) < 1e-12 else 'strict accepts, WRONG'))] += 1

    print(f'# Sign rules against the answer key\n\n{len(files)} raw DTCC files, {files[0].stem} to {files[-1].stem}; lab code at {LAB.name}\n')
    usable = sum(v for k, v in res.items() if isinstance(k, tuple) and k[1] == 'records')
    print(f'Answer-key records: {usable:,} (left out: {res["left out (no unique match)"]:,})\n')
    keys = ['records', 'true reading hi', 'true reading lo', 'floor decides, right', 'floor decides, WRONG',
            'floor excludes (both readings clear it)', 'floor excludes (neither clears it)',
            'strict accepts, right', 'strict accepts, WRONG', 'strict excludes']
    print('| | 1% coupon | 5% coupon |\n| --- | --- | --- |')
    for k in keys:
        print(f'| {k} | {res[(0.01, k)]:,} | {res[(0.05, k)]:,} |')
    print('\nFloor errors (first 10):')
    for w in wrong:
        print(f'- {w}')
    print('\nNames quoted under 100 bp on a 5% coupon, and how the floor reads their upfront-only trades within 5 days:')
    for name, qs in sorted(tight5.items(), key=lambda t: -len(t[1])):
        qmed = sorted(x[1] for x in qs)[len(qs) // 2]
        picks = []
        for day, ex, exp, pct in up5.get(name, []):
            if any(abs((dt.date.fromisoformat(day) - dt.date.fromisoformat(qd)).days) <= 5 for qd, _ in qs):
                hi, lo = readings(pct, 0.05, ex, exp)
                plaus = [x['spread'] for x in (hi, lo) if x['spread'] is not None and 0.01 <= x['spread'] < 1]
                if len(plaus) == 1:
                    picks.append(plaus[0] * 1e4)
        read = f'floor reads {len(picks)} nearby upfront trades as {min(picks):,.0f}-{max(picks):,.0f} bp' if picks else 'no nearby upfront trade the floor decides'
        print(f'- {name}: {len(qs)} quotes, median {qmed * 1e4:.0f} bp; {read}')
    print(f'\nQuoted spreads outside 10-5,000 bp: {len(out_of_range)}'
          + (f', from {min(x[2] for x in out_of_range)} to {max(x[2] for x in out_of_range)} bp' if out_of_range else ''))
    for name in sorted({x[1] for x in out_of_range}):
        vals = [x[2] for x in out_of_range if x[1] == name]
        print(f'- {name}: {len(vals)} ({", ".join(str(v) for v in sorted(vals)[:6])} bp)')


if __name__ == '__main__':
    main()
