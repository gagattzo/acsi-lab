"""Red team, 28 Nov 2026: what breaks the live headline? Eight scenarios on the real DTCC tape.

Each scenario runs the production rules (harness/dtcc.py, identical to aistress.si/api/cds.js) and reports how far the
v0.2 headline moves. Headline arithmetic (v0.2, no ceiling): G = (F1 + F2 + F3) / 3 - A with
F1 = 100 (CoreWeave bp - 500) / 500, F2 = 100 (Oracle bp - 100) / 900, F3 and A held at the 24 Nov edition, so one bp of
CoreWeave moves G by 0.067 points and one bp of Oracle by 0.037.

  S1  One wash trade: an eligible $5M quote at 1.5x the last level, injected on each day since 1 Oct 2026
  S2  Sign attack: one eligible quote at the other reading, on days where the reference sets the upfront sign
  S3  Cold start: the site prices a 12-day window with no history; how often does it disagree with the full chain?
  S4  Revisions: how often is a day's level changed by files published after that day?
  S5  Roll: the same day priced on the new and the old on-the-run maturity around 20 Mar and 20 Sep 2026
  S6  Credit event: what large upfronts read as, and what the uncapped score does
  S7  Concentration: the headline's share that is CoreWeave, and the headline at other CoreWeave levels
  S8  Field drift: a renamed column or a new product label

Usage: python redteam/stress_scenarios.py   (needs data/raw/dtcc from python harness/dtcc.py)
"""
import copy
import datetime as dt
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'harness'))
import dtcc  # noqa: E402

PER_BP = {'CRWV': 100 / 500 / 3, 'ORCL': 100 / 900 / 3}
CALM = {'CRWV': 500, 'ORCL': 100}
F3_SCORE, A = 100 * (98 - 90) / (98 - 80), 15.3


def headline(crwv, orcl):
    return (100 * (crwv - 500) / 500 + 100 * (orcl - 100) / 900 + F3_SCORE) / 3 - A


def chain(files):
    """Level per (day, entity) from the files given, as the pipeline computes it, plus the ledger."""
    by_day = dtcc.ledger(files)
    first = files[0][0] if files else None
    prev, out = {}, {}
    for d in sorted(x for x in by_day if first and x >= first):
        lv, _ = dtcc.parse_day(by_day[d], d, prev, cleaned=True)
        for e, v in lv.items():
            prev[e] = v['official_bp']
            out[(d, e)] = v
    return out, by_day


def last_level(levels, e, d):
    ks = [k for k in levels if k[1] == e and k[0] < d]
    return levels[max(ks)]['official_bp'] if ks else None


def fake_quote(e, d, bp, did='999999999'):
    template = {'Action type': 'NEWT', 'Dissemination Identifier': did, 'Original Dissemination Identifier': '',
                'Execution Timestamp': f'{d}T16:00:00Z', 'Expiration Date': None,
                'Underlying Asset Name': {'CRWV': 'COREWEAVE, INC.', 'ORCL': 'ORACLE CORPORATION', 'META': 'META PLATFORMS, INC.'}[e], 'UPI Underlier Name': '',
                'Underlier ID-Leg 1': dtcc.ENTITIES[e][1], 'Fixed rate-Leg 1': '0.05' if e == 'CRWV' else '0.01',
                'Spread-Leg 1': f'{bp / 10000:.6f}', 'Notional amount-Leg 1': '5000000', 'Other payment amount': '',
                'Other payment type': '', 'Event type': 'TRAD', 'Notional currency-Leg 1': 'USD', 'Other payment currency': '',
                'UPI FISN': 'NA/CDS Corp SN Sr', 'Package indicator': 'FALSE', 'Non-standardized term indicator': 'FALSE',
                'Post-priced swap indicator': 'false'}
    ex = dtcc.ts(template['Execution Timestamp'])
    template['Expiration Date'] = dt.datetime.fromtimestamp(dtcc.on_the_run_5y(ex), dt.timezone.utc).date().isoformat()
    return template


def other_readings(rows, e, d):
    """For each eligible upfront trade of e on d: (hi, lo) readings."""
    out = []
    for r in rows:
        if dtcc.entity_of(r) != e or not (dtcc.is_cds(r) and dtcc.eligible(r)) or dtcc.fnum(r.get('Spread-Leg 1')):
            continue
        ex, exp = dtcc.ts(r.get('Execution Timestamp')), dtcc.ts(str(r.get('Expiration Date', '')) + 'T00:00:00Z')
        if ex is None or exp is None or exp != dtcc.on_the_run_5y(ex):
            continue
        C = dtcc.standard_coupon(dtcc.coupon(r.get('Fixed rate-Leg 1'))[0])
        N, amt = dtcc.fnum(r.get('Notional amount-Leg 1')), dtcc.fnum(r.get('Other payment amount'))
        if not (C and N and amt is not None and str(r.get('Other payment type', '')).strip().upper() == 'UFRO'):
            continue
        step = ex + dtcc.DAY
        T, accr = (exp - step) / (365.25 * dtcc.DAY), (step - dtcc.last_imm(ex)) / (360 * dtcc.DAY)
        p = abs(amt / N)
        out.append((dtcc.spread_from_upfront(p, C, T, accr), dtcc.spread_from_upfront(-p, C, T, accr)))
    return out


def main():
    raw = ROOT / 'data' / 'raw' / 'dtcc'
    days = sorted(p.stem for p in raw.glob('*.zip'))
    files = [(d, dtcc.slim(dtcc.rows_of((raw / f'{d}.zip').read_bytes()))) for d in days]
    levels, by_day = chain(files)
    scored = sorted({d for d, e in levels if d >= '2026-10-01'})
    print(f'{len(files)} raw files {days[0]} to {days[-1]}; scenarios S1-S2 on {len(scored)} weekdays since 1 Oct 2026\n')

    # S1: one eligible quote at 1.5x the last level
    print('S1  One wash trade (eligible $5M quote at 1.5x the last level)')
    for e in ('CRWV', 'ORCL'):
        moves, alone = [], 0
        for d in scored:
            base = levels.get((d, e), {}).get('official_bp') or last_level(levels, e, d)
            prev = {x: last_level(levels, x, d) for x in ('CRWV', 'ORCL', 'META') if last_level(levels, x, d)}
            ref = last_level(levels, e, d)
            if not ref:
                continue
            lv, _ = dtcc.parse_day(by_day.get(d, []) + [fake_quote(e, d, 1.5 * ref)], d, prev, cleaned=True)
            moves.append((lv[e]['official_bp'] - base) * PER_BP[e])
            alone += (d, e) not in levels
        big = sum(1 for m in moves if m > 5)
        print(f'    {e}: headline +{statistics.median(moves):.1f} pts median, +{max(moves):.1f} max; over +5 pts on {big} of {len(moves)} days; '
              f'the fake alone sets the level on {alone} days (no real trade)')

    # S2: one eligible quote at the other reading, where the reference sets the sign
    print('\nS2  Sign attack (one eligible quote at the other reading, on days the reference sets the sign)')
    for e in ('CRWV', 'ORCL', 'META'):
        hits = []
        for d in sorted({d for d, x in levels if x == e}):
            v = levels[(d, e)]
            if not v['n_sign_from_reference'] or v['quoted_trades']:
                continue
            rd = other_readings(by_day[d], e, d)
            if not rd:
                continue
            chosen = v['official_bp'] / 10000
            alt = statistics.median([lo if abs(hi - chosen) <= abs(lo - chosen) else hi for hi, lo in rd])
            prev = {x: last_level(levels, x, d) for x in ('CRWV', 'ORCL', 'META') if last_level(levels, x, d)}
            lv, _ = dtcc.parse_day(by_day[d] + [fake_quote(e, d, alt * 10000)], d, prev, cleaned=True)
            hits.append((d, v['official_bp'], lv[e]['official_bp']))
        if not hits:
            print(f'    {e}: no day where the reference sets the sign without a real quote')
            continue
        worst = max(hits, key=lambda h: abs(h[2] - h[1]))
        recent = [h for h in hits if h[0] >= '2026-10-01']
        pts = f', headline {(worst[2] - worst[1]) * PER_BP[e]:+.1f} pts' if e in PER_BP else ' (not in the headline)'
        print(f'    {e}: exposed on {len(hits)} days ({len(recent)} since 1 Oct); worst {worst[0]}: {worst[1]:.0f} -> {worst[2]:.0f} bp{pts}')

    # S3: cold start, 12 calendar days of files, no history
    print('\nS3  Cold start (the site prices 12 calendar days of files with no earlier history)')
    worst, bad, windows = {}, {}, 0
    for i, (s, _) in enumerate(files):
        end = (dt.date.fromisoformat(s) + dt.timedelta(days=11)).isoformat()
        win = [f for f in files[i:] if f[0] <= end]
        if win[-1][0] < end and win[-1][0] == days[-1]:
            break
        windows += 1
        lv, _ = chain(win)
        for (d, e), v in lv.items():
            full = levels.get((d, e))
            err = abs(v['official_bp'] - full['official_bp']) if full else float('inf')
            if err > 1:
                bad.setdefault(e, set()).add(s)
                if err > worst.get(e, (0,))[0]:
                    worst[e] = (err, s, d, v['official_bp'], full['official_bp'] if full else None)
    for e in ('CRWV', 'ORCL', 'META'):
        if e in bad:
            w = worst[e]
            print(f'    {e}: {len(bad[e])} of {windows} windows disagree by more than 1 bp; worst: window from {w[1]}, {w[2]}: '
                  f'{w[3]:.0f} bp cold vs {w[4]} with history')
        else:
            print(f'    {e}: all {windows} windows agree')

    # S4: revisions after the day's own file
    print("\nS4  Revisions (a day's level as of its own end-of-day file vs. after every later file)")
    snaps = {}
    for i, (d, _) in enumerate(files):
        if d >= '2026-06-01':
            at_day, _ = chain(files[: i + 1])
            snaps[d] = {e: at_day.get((d, e), {}).get('official_bp') for e in ('CRWV', 'ORCL')}
    for e in ('CRWV', 'ORCL'):
        revised, sizes, n = 0, [], 0
        for d, snap in snaps.items():
            a, b = snap[e], levels.get((d, e), {}).get('official_bp')
            if a is None and b is None:
                continue
            n += 1
            if a is None or b is None or abs(a - b) > 1:
                revised += 1
                sizes.append(abs(a - b) if a is not None and b is not None else float('nan'))
        finite = [x for x in sizes if x == x]
        print(f'    {e}: {revised} of {n} days since 1 Jun 2026 changed after their own file'
              + (f' (median {statistics.median(finite):.0f} bp, max {max(finite):.0f} bp; {len(sizes) - len(finite)} appeared or vanished)' if sizes else ''))

    # S5: roll basis
    print('\nS5  Roll (same trades, new vs old on-the-run maturity)')
    for roll in ('2026-03-20', '2026-09-20'):
        for e in ('CRWV', 'ORCL'):
            r0 = dt.date.fromisoformat(roll)
            near = [d for d in sorted(by_day) if 0 <= (dt.date.fromisoformat(d) - r0).days <= 10]
            new_exp = dtcc.on_the_run_5y(dtcc.utc(r0.year, r0.month, 21))
            old_exp = dtcc.on_the_run_5y(dtcc.utc(r0.year, r0.month, 19))
            pairs = []
            for d in near:
                lv_new, _ = dtcc.parse_day(by_day[d], d, {e: last_level(levels, e, d)} if last_level(levels, e, d) else {}, cleaned=True)
                orig = dtcc.on_the_run_5y
                dtcc.on_the_run_5y = lambda t, _o=old_exp: _o
                try:
                    lv_old, _ = dtcc.parse_day(by_day[d], d, {e: last_level(levels, e, d)} if last_level(levels, e, d) else {}, cleaned=True)
                finally:
                    dtcc.on_the_run_5y = orig
                if e in lv_new and e in lv_old:
                    pairs.append(lv_new[e]['official_bp'] - lv_old[e]['official_bp'])
            if pairs:
                b = statistics.median(pairs)
                print(f'    {roll} {e}: new minus old maturity {b:+.0f} bp (median of {len(pairs)} days), headline {b * PER_BP[e]:+.1f} pts on the roll day')
            else:
                print(f'    {roll} {e}: no day with both maturities traded')

    # S6: credit event
    print('\nS6  Credit event (5% coupon, 5 years, 40% recovery): upfront -> spread -> CoreWeave score -> headline at Oracle 373')
    for up in (0.23, 0.30, 0.40, 0.50, 0.55, 0.60, 0.70):
        s = dtcc.spread_from_upfront(up, 0.05, 5.06, 0.02) * 10000
        sat = ' (solver at its cap: the model cannot price it)' if s > 15000 else ''
        print(f'    {up:.0%} upfront -> {s:,.0f} bp -> F1 score {100 * (s - 500) / 500:,.0f} -> headline {headline(s, 373.2):+,.0f}{sat}')
    print('    After an ISDA credit event the contract stops trading: F1 carries for 10 days, then the live headline goes dark.')

    # S7: concentration
    print('\nS7  Concentration')
    f1, f2 = 100 * (1372.8 - 500) / 500, 100 * (373.2 - 100) / 900
    print(f'    CoreWeave is {f1 / (f1 + f2 + F3_SCORE):.0%} of the financing score at 1,373 bp; Oracle {f2 / (f1 + f2 + F3_SCORE):.0%}, loans {F3_SCORE / (f1 + f2 + F3_SCORE):.0%}')
    for c in (500, 700, 961, 1100, 1372.8, 2000, 3000):
        print(f'    CoreWeave {c:,.0f} bp, Oracle 373 bp -> headline {headline(c, 373.2):+.1f}')

    # S8: field drift
    print('\nS8  Field drift (rename one column or relabel the product in the 24 Nov ledger day)')
    d = '2026-11-24'
    base, _ = dtcc.parse_day(by_day[d], d, {e: last_level(levels, e, d) for e in ('CRWV', 'ORCL')}, cleaned=True)
    for what, change in (('Other payment amount -> Other payment amount-Leg 1', ('rename', 'Other payment amount', 'Other payment amount-Leg 1')),
                         ('UPI FISN "CDS Corp SN Sr" -> "CDS Corp SNAC Sr"', ('sub', 'UPI FISN', 'SN Sr', 'SNAC Sr')),
                         ('Notional amount in thousands', ('scale', 'Notional amount-Leg 1'))):
        rows = copy.deepcopy(by_day[d])
        for r in rows:
            if change[0] == 'rename':
                r[change[2]] = r.pop(change[1], '')
            elif change[0] == 'sub':
                r[change[1]] = str(r.get(change[1], '')).replace(change[2], change[3])
            else:
                n = dtcc.fnum(r.get(change[1]))
                r[change[1]] = '' if n is None else f'{n / 1000:.0f}'
        lv, _ = dtcc.parse_day(rows, d, {e: last_level(levels, e, d) for e in ('CRWV', 'ORCL')}, cleaned=True)
        got = ', '.join(f"{e} {lv[e]['official_bp']:.0f}" if e in lv else f'{e} none' for e in ('CRWV', 'ORCL'))
        print(f"    {what}: {got} (was CRWV {base['CRWV']['official_bp']:.0f}, ORCL {base['ORCL']['official_bp']:.0f}); no error raised")


if __name__ == '__main__':
    main()
