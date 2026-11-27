"""L3 private loan marks, from the SEC's BDC Data Sets (monthly zips, soi.tsv = schedule of investments).

Scope: the observed BDC book only. Most AI-infrastructure debt sits with private funds, banks, insurers and project
bonds that file no such schedule.

Rows and matching reuse research/tools/bdc_exposure.py (its scan(), patterns and net_assets(), imported unchanged):
current-period rows only (ddate = period), roll-up "[Member]" rows dropped, named obligors vs keyword leads.

Point in time: a row is available from its filing's `filed` date. A build for D reads only filings with filed <= D;
for each holder and period the latest such filing is used (an amendment replaces the original once it is filed).

Review status (research/bdc/review_log.csv, human rows only; an agent's "propose accept" never counts):
  rejected     a human rejected the row's facility_key
  signed off   a named-obligor row whose facility_key, or whose borrower group, has a human accept (the 26 Nov 2026
               decision was made at group level, so a group accept covers that group's rows in every vintage)
  candidate    everything else, keyword leads included: listed in the register, never scored

Facility matching across consecutive quarterly vintages of the same holder (holder CIK is always part of the key):
  exact   borrower group, instrument class, maturity, spread (or fixed rate), and the letters of the filer's text
  terms   borrower group, instrument class, maturity and spread, when unique on both sides (the filer reworded the text)
  text    borrower group, instrument class and the letters of the text, when unique on both sides (a term is missing in
          one vintage)
Rows with the same exact key in one filing are summed first. Only funded debt is matched: unfunded commitments
(blank, zero or negative cost and fair value, or "unfunded" in the text) and equity stakes are listed, never scored.

Metrics per borrower group, over matched facilities (both vintages funded, signed off), weighted by current principal
when it is within 0.5-1.5x of cost (bdc_exposure's rule), else by cost, times the staleness weight of the vintage:
  m0 level     FV / cost now. Context only (weight 0 in the shipped method): L3 scores changes and breadth, not levels
  m1 markdown  weighted mean change in FV / cost from the prior vintage
  m2 breadth   share of matched principal with FV / cost below breadth_below (0.95)
  m3 PIK       share with a PIK rate > 0, split into PIK at origination (PIK already in the prior vintage) and PIK
               added later (0 or absent before, > 0 now); only added PIK scores
  m4           non-accrual: not implemented (almost never tagged in soi.tsv; needs filing footnotes)
Each metric gets an anchor z (proposed examples in stack/stack-v0.yaml), a group's z is the weighted mean of its
metrics' z, the layer Z is the capped sum over groups (20% cap; equal weights and cap_infeasible below 5 groups), the
2-group rule caps the band at Watch, and the band is replayed with senior hysteresis over monthly release points
(each month end, plus the as-of date). L3 publishes at month ends, the cadence of the SEC's monthly file: a month-end
point is fresh only if a scored filing arrived since the previous one; the as-of point between two month ends carries
the last published value (its weight factor follows staleness), so a filing waits for the next month end and an
edition built mid-month equals any later replay of that day.

Column labels: files before 2026_08 carry cost and fair value under "Adjusted cost basis" and "Initial fair value of
Investment" (the standard labels are present but empty). Checked against the 31 Dec 2025 comparatives in 2026_08,
which use the standard labels: cost agrees on 31,743 rows (208 differ, tranches with identical text), fair value on
33,988 (269 differ). They are coalesced into the standard labels before scan() reads the file.

  python -m stack.l3_bdc --register [--as-of D] [--offline]   write research/bdc/stack_register.csv, print match rate
"""
import argparse, csv, datetime as dt, hashlib, importlib.util, io, json, os, pathlib, re, sys, zipfile

from . import engine
from .config import cuts as config_cuts
from .fetch import file_record, get as fetch_get

csv.field_size_limit(10 ** 9)
ROOT = pathlib.Path(__file__).resolve().parent.parent
SCOPE = 'observed BDC book'
METRICS = ('m0_level', 'm1_markdown', 'm2_breadth', 'm3_added_pik', 'm4_non_accrual')
ALIASES = {'Investment Owned, Cost': ('Adjusted cost basis',),
           'Investment Owned, Fair Value': ('Initial fair value of Investment',)}
MANIFEST_FIELDS = ['file', 'url', 'bytes', 'sha256', 'fetched_at']
UA_HELP = ("SEC_USER_AGENT is not set: the SEC asks every automated client for a descriptive User-Agent with a contact, "
           "e.g. export SEC_USER_AGENT='<organisation> <contact address>'")
M4_STUB = {'status': 'not_implemented',
           'reason': 'non-accrual is almost never tagged in soi.tsv (1 row in 2026_08); it needs parsing of the filing '
                     'footnotes (inlineurl links each filing)'}
REG_FIELDS = ['holder', 'holder_cik', 'period', 'filed', 'form', 'accession', 'borrower_group', 'category', 'match_rule',
              'review_status', 'review_basis', 'instrument_class', 'funded', 'principal', 'principal_ok', 'cost',
              'fair_value', 'fv_to_cost', 'pik_rate', 'pik_source', 'spread_bp', 'maturity', 'match_key', 'prior_period',
              'prior_match', 'prior_fv_to_cost', 'bdc_facility_key', 'obligor_text', 'filing_url', 'source_file']


def _load_bdc_exposure():
    spec = importlib.util.spec_from_file_location('bdc_exposure', ROOT / 'research' / 'tools' / 'bdc_exposure.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bx = _load_bdc_exposure()


# --- reading one monthly zip -----------------------------------------------------------------------------------------

def _iso(s):
    s = (s or '').strip()
    return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if re.fullmatch(r'\d{8}', s) else s[:10]


class _LineStream(io.RawIOBase):
    def __init__(self, lines):
        self._it, self._buf = lines, b''

    def readable(self):
        return True

    def readinto(self, b):
        while not self._buf:
            try:
                self._buf = next(self._it)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n], self._buf = self._buf[:n], self._buf[n:]
        return n


def _coalesced_soi(raw):
    """soi.tsv with alias columns copied into the standard label where the standard one is blank."""
    rd = csv.reader(io.TextIOWrapper(raw, encoding='utf-8', errors='replace'), delimiter='\t')
    try:
        hdr = next(rd)
    except StopIteration:
        return
    ix = {h: i for i, h in enumerate(hdr)}
    pairs = [(ix[c], [ix[a] for a in al if a in ix]) for c, al in ALIASES.items() if c in ix]
    pairs = [(c, al) for c, al in pairs if al]
    out = io.StringIO()
    w = csv.writer(out, delimiter='\t', lineterminator='\n')

    def emit(row):
        w.writerow(row)
        s = out.getvalue()
        out.seek(0)
        out.truncate()
        return s.encode('utf-8')

    yield emit(hdr)
    for row in rd:
        for c, al in pairs:
            if c < len(row) and not row[c].strip():
                for a in al:
                    if a < len(row) and row[a].strip():
                        row[c] = row[a]
                        break
        yield emit(row)


class AliasZip:
    """A zipfile whose soi.tsv is served with the alias columns coalesced (everything else passes through)."""

    def __init__(self, z):
        self.z = z

    def open(self, name, *a, **k):
        if name == 'soi.tsv':
            return io.BufferedReader(_LineStream(_coalesced_soi(self.z.open(name))), buffer_size=1 << 20)
        return self.z.open(name, *a, **k)

    def namelist(self):
        return self.z.namelist()


def read_filings(z, fname):
    """Every 10-K / 10-Q in sub.tsv: accession, cik, holder, form, period, filed (ISO dates)."""
    out = []
    try:
        f = z.open('datasets/sub.tsv')
    except KeyError:
        return out
    for r in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8', errors='replace'), delimiter='\t'):
        if not (r.get('form') or '').startswith(('10-K', '10-Q')):
            continue
        out.append({'accession': r['adsh'], 'cik': r['cik'], 'holder': r.get('name', ''), 'form': r['form'],
                    'period': _iso(r['period']), 'filed': _iso(r['filed']), 'source_file': fname})
    return out


def load_zip(path):
    """(matched rows, filings) of one monthly zip. An empty soi.tsv (2026_06) gives no rows."""
    p = pathlib.Path(path)
    with zipfile.ZipFile(p) as z0:
        z = AliasZip(z0)
        try:
            rows = bx.scan(z, p.name)
        except StopIteration:
            rows = []
        filings = read_filings(z0, p.name)
    for r in rows:
        r['period'], r['filed'] = _iso(r['mark_date']), _iso(r['filed'])
        enrich(r)
    return rows, filings


def net_assets(path):
    with zipfile.ZipFile(path) as z:
        return {k: dict(v, period=_iso(v['period'])) for k, v in bx.net_assets(z).items()}


# --- one row: instrument class, funded, weight, matching identity -----------------------------------------------------

_EQUITY = re.compile(r'equity|preferred|common\s*(stock|shares|units)|warrant|\bshares\b|\bunits\b|membership\s*interest|'
                     r'partnership\s*interest|\bLP\s*interest', re.I)
_DEBT = re.compile(r'loan|lien|\bnotes?\b|debt|bond|revolv|delayed\s*draw|\bddtl\b|credit\s*facility|secured|'
                   r'subordinated|mortgage|asset-backed|warehouse', re.I)
_SPREAD = re.compile(r'\b(?:S|SOFR|L|LIBOR|E|EURIBOR|SONIA|SN|P|PRIME|BSBY|CORRA|BBSY)\b(?:\s*\([A-Z0-9]+\))?\s*\+\s*'
                     r'(\d+(?:\.\d+)?)\s*(%?)', re.I)
_SPREAD2 = re.compile(r'spread\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*%', re.I)
_DATE = re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b')
_PIK = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*PIK\b', re.I)


def _text_maturity(text):
    hits = []
    for m in _DATE.finditer(text):
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            hits.append((m.start(), dt.date(y, mo, d).isoformat()))
        except ValueError:
            continue
    if not hits:
        return ''
    k = text.lower().find('matur')
    after = [h for h in hits if k >= 0 and h[0] > k]
    return (after[0] if after else hits[-1])[1]


def _text_spread_bp(text):
    m = _SPREAD.search(text) or _SPREAD2.search(text)
    if not m:
        return None
    v = float(m.group(1))
    pct = m.re is _SPREAD2 or m.group(2) == '%' or v < 30
    return int(round(v * 100)) if pct else int(round(v))


def enrich(r):
    """Adds instrument_class, funded (unfunded text excluded), weight, pik, spread_bp, maturity and match keys."""
    text = r.get('obligor_text') or ''
    r['funded'] = bool(r.get('funded')) and not re.search(r'\bunfunded\b', text, re.I)
    has_terms = any(r.get(k) not in (None, '') for k in ('spread', 'rate', 'pik_rate')) or bool(r.get('maturity'))
    if (r.get('principal') or 0) > 0 or has_terms:
        cls = 'debt' if not (_EQUITY.search(text) and not _DEBT.search(text)) else 'equity'
    else:
        cls = 'equity' if _EQUITY.search(text) else ('debt' if _DEBT.search(text) else 'other')
    r['instrument_class'] = cls
    r['maturity'] = (r.get('maturity') or '').strip()[:10] or _text_maturity(text)
    if r.get('spread') is not None:
        r['spread_bp'] = int(round(r['spread'] * 10000))
    else:
        r['spread_bp'] = _text_spread_bp(text)
    term = f"s{r['spread_bp']}" if r['spread_bp'] is not None else (
        f"fx{int(round(r['rate'] * 10000))}" if r.get('rate') is not None and re.search(r'fixed', text, re.I) else '')
    pik, src = r.get('pik_rate'), 'tag' if r.get('pik_rate') is not None else None
    if pik is None:
        m = _PIK.search(text)
        if m:
            pik, src = float(m.group(1)) / 100.0, 'text'
    r['pik'], r['pik_source'] = pik, src
    words = re.sub(r'[^a-z]', '', text.lower())
    cik, grp = r.get('holder_cik', ''), r.get('borrower_group', '')
    r['key_exact'] = hashlib.sha1(f'{cik}|{grp}|{cls}|{r["maturity"]}|{term}|{words}'.encode()).hexdigest()[:12]
    r['key_terms'] = f'{cik}|{grp}|{cls}|{r["maturity"]}|{term}' if (r['maturity'] and term) else None
    r['key_text'] = f'{cik}|{grp}|{cls}|{words}'
    c, p = r.get('cost'), r.get('principal')
    if r['funded'] and r.get('principal_ok') and p:
        r['weight'], r['weight_basis'] = float(p), 'principal'
    elif r['funded'] and c and c > 0:
        r['weight'], r['weight_basis'] = float(c), 'cost'
    else:
        r['weight'], r['weight_basis'] = 0.0, None
    return r


# --- review status ---------------------------------------------------------------------------------------------------

def load_review_log(path):
    """{'facility': {facility_key: decision}, 'group_accept': {group: date}} from human rows only."""
    out = {'facility': {}, 'group_accept': {}}
    p = pathlib.Path(path)
    if not p.exists():
        return out
    for d in csv.DictReader(open(p, newline='')):
        if (d.get('reviewer_kind') or '').strip() != 'human':
            continue
        out['facility'][d['facility_key']] = d['decision']
        if d['decision'] == 'accept':
            g = d.get('borrower_group') or ''
            out['group_accept'][g] = min(out['group_accept'].get(g) or d.get('date') or '', d.get('date') or '')
    return out


def review(r, log):
    """(review_status, basis). Never auto-verified: only a human row in the review log signs anything off."""
    dec = log['facility'].get(r.get('facility_key'))
    if dec == 'reject':
        return 'rejected', 'human rejected this facility'
    if r.get('match_rule') != 'named obligor':
        return 'candidate', ('keyword lead, deferred by a human' if dec == 'defer' else 'keyword lead: never scored')
    if dec == 'accept':
        return 'signed off', 'human accepted this facility'
    if r.get('borrower_group') in log['group_accept']:
        return 'signed off', f"human group-level accept of {r['borrower_group']} ({log['group_accept'][r['borrower_group']]})"
    if dec == 'defer':
        return 'candidate', 'deferred by a human'
    return 'candidate', 'named obligor, no human sign-off for this borrower group'


def apply_review(rows, log):
    for r in rows:
        r['review_status'], r['review_basis'] = review(r, log)
    return rows


# --- vintages, matching, metrics -------------------------------------------------------------------------------------

def prior_quarter_end(period):
    d = dt.date.fromisoformat(period)
    y, m = (d.year, d.month - 3) if d.month > 3 else (d.year - 1, d.month + 9)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - dt.timedelta(days=1)).isoformat()


def select_filings(filings, T):
    """{cik: {period: filing}}: per holder and period, the latest filing filed on or before T."""
    out = {}
    for f in filings:
        if f['filed'] > T:
            continue
        cur = out.setdefault(f['cik'], {}).get(f['period'])
        if cur is None or (f['filed'], f['accession']) > (cur['filed'], cur['accession']):
            out[f['cik']][f['period']] = f
    return out


def _aggregate(rows):
    """Funded debt rows of one filing, summed by exact key."""
    agg = {}
    for r in rows:
        if not r['funded'] or r['instrument_class'] != 'debt':
            continue
        a = agg.get(r['key_exact'])
        if a is None:
            agg[r['key_exact']] = dict(r, rows=1)
            continue
        a['rows'] += 1
        for k in ('principal', 'cost', 'fair_value', 'weight'):
            if r.get(k) is not None:
                a[k] = (a.get(k) or 0.0) + r[k]
        if r.get('pik') is not None and (a.get('pik') is None or r['pik'] > a['pik']):
            a['pik'] = r['pik']
        if r['review_status'] != 'signed off':
            a['review_status'], a['review_basis'] = r['review_status'], r['review_basis']
    for a in agg.values():
        a['fvc'] = (a['fair_value'] / a['cost']) if a.get('cost') and a['cost'] > 0 and a.get('fair_value') is not None else None
        # principal far from cost: the commitment, a scale error, or drawn and undrawn parts mixed in cost and FV
        pr, c = a.get('principal'), a.get('cost')
        a['principal_mismatch'] = bool(pr and c and c > 0 and not 0.5 <= pr / c <= 1.5)
    return agg


def match_facilities(cur, prev):
    """cur, prev: {exact key: facility}. Returns [(cur facility, prev facility or None, tier or None)]."""
    out, used = [], set()
    left = {}
    for k, c in cur.items():
        if k in prev:
            out.append((c, prev[k], 'exact'))
            used.add(k)
        else:
            left[k] = c
    for tier, field in (('terms', 'key_terms'), ('text', 'key_text')):
        pv = {}
        for k, p in prev.items():
            if k not in used and p.get(field):
                pv.setdefault(p[field], []).append(k)
        cv = {}
        for k, c in left.items():
            if c.get(field):
                cv.setdefault(c[field], []).append(k)
        for f, ks in cv.items():
            if len(ks) == 1 and len(pv.get(f, [])) == 1:
                out.append((left.pop(ks[0]), prev[pv[f][0]], tier))
                used.add(pv[f][0])
    out += [(c, None, None) for c in left.values()]
    return out


def _scorable(f, mode):
    if f is None or f['fvc'] is None or f['weight'] <= 0 or f.get('principal_mismatch'):
        return False
    if mode == 'named_candidates':
        return f['match_rule'] == 'named obligor' and f['review_status'] != 'rejected'
    return f['review_status'] == 'signed off'


def pairs_at(T, rows_by_acc, filings, p, eng):
    """Matched facility pairs of every holder's latest vintage visible at T, and the match-rate tally."""
    sel = select_filings(filings, T)
    mode = p['score_rows']
    st = eng['staleness']
    pairs, tally = [], {'holders_current': 0, 'holders_with_prior_vintage': 0, 'facilities': 0, 'matched': 0,
                        'weight': 0.0, 'matched_weight': 0.0, 'by_tier': {'exact': 0, 'terms': 0, 'text': 0},
                        'unmatched_group_new_to_holder': 0, 'no_prior_vintage': 0, 'excluded_unfunded': 0, 'excluded_equity_or_other': 0,
                        'excluded_principal_not_near_cost': 0, 'candidates_listed': 0, 'candidate_weight': 0.0}
    for cik, per in sel.items():
        P = max(per)
        fc = per[P]
        crow = rows_by_acc.get(fc['accession'], [])
        if not crow:
            continue
        tally['holders_current'] += 1
        tally['excluded_unfunded'] += sum(1 for r in crow if not r['funded'])
        tally['excluded_equity_or_other'] += sum(1 for r in crow if r['funded'] and r['instrument_class'] != 'debt')
        cur = _aggregate(crow)
        for c in cur.values():
            if c['principal_mismatch']:
                tally['excluded_principal_not_near_cost'] += 1
            elif not _scorable(c, mode) and c['fvc'] is not None:
                tally['candidates_listed'] += 1
                tally['candidate_weight'] += c['weight']
        cur = {k: c for k, c in cur.items() if _scorable(c, mode)}
        if not cur:
            continue
        pp = prior_quarter_end(P)
        fp = per.get(pp)
        age = (dt.date.fromisoformat(T) - dt.date.fromisoformat(P)).days
        sw = engine.staleness_weight(age, st['full_weight_days'], st['zero_weight_days'])
        if fp is None:
            tally['no_prior_vintage'] += len(cur)
            continue
        tally['holders_with_prior_vintage'] += 1
        prev = {k: v for k, v in _aggregate(rows_by_acc.get(fp['accession'], [])).items() if _scorable(v, mode)}
        prev_groups = {v['borrower_group'] for v in prev.values()}
        for c, pv, tier in match_facilities(cur, prev):
            tally['facilities'] += 1
            tally['weight'] += c['weight']
            if pv is None:
                if c['borrower_group'] not in prev_groups:
                    tally['unmatched_group_new_to_holder'] += 1
                continue
            tally['matched'] += 1
            tally['matched_weight'] += c['weight']
            tally['by_tier'][tier] += 1
            pik_c, pik_p = c.get('pik') or 0.0, pv.get('pik') or 0.0
            pairs.append({'holder_cik': cik, 'holder': c['holder'], 'group': c['borrower_group'], 'tier': tier,
                          'key': c['key_exact'], 'prior_key': pv['key_exact'], 'period': P, 'prior_period': pp,
                          'filed': fc['filed'], 'accession': fc['accession'], 'prior_filed': fp['filed'],
                          'fvc': c['fvc'], 'prior_fvc': pv['fvc'], 'change': c['fvc'] - pv['fvc'],
                          'weight_usd': c['weight'], 'weight_basis': c['weight_basis'], 'staleness_weight': sw,
                          'w': c['weight'] * sw, 'pik': pik_c, 'prior_pik': pik_p,
                          'pik_class': 'none' if pik_c <= 0 else ('origination' if pik_p > 0 else 'added')})
    tally['match_rate_facilities'] = tally['matched'] / tally['facilities'] if tally['facilities'] else None
    tally['match_rate_weight'] = tally['matched_weight'] / tally['weight'] if tally['weight'] else None
    return pairs, tally


def group_metrics(pairs, p):
    """{group: metrics} over pairs with a positive (staleness-weighted) weight."""
    thr = p['breadth_below']
    out = {}
    for x in pairs:
        if x['w'] <= 0:
            continue
        g = out.setdefault(x['group'], {'pairs': [], 'holders': set()})
        g['pairs'].append(x)
        g['holders'].add(x['holder_cik'])
    res = {}
    for gname, g in out.items():
        ps = g['pairs']
        W = sum(x['w'] for x in ps)
        res[gname] = {
            'facilities': len(ps), 'holders': len(g['holders']), 'matched_weight_usd': W,
            'matched_principal_usd': sum(x['weight_usd'] for x in ps),
            'm0_level': sum(x['w'] * x['fvc'] for x in ps) / W,
            'm1_markdown': sum(x['w'] * x['change'] for x in ps) / W,
            'm2_breadth': sum(x['w'] for x in ps if x['fvc'] < thr) / W,
            'm3_any_pik': sum(x['w'] for x in ps if x['pik'] > 0) / W,
            'm3_origination_pik': sum(x['w'] for x in ps if x['pik_class'] == 'origination') / W,
            'm3_added_pik': sum(x['w'] for x in ps if x['pik_class'] == 'added') / W,
            'm4_non_accrual': None,
            'latest_filed': max(x['filed'] for x in ps), 'periods': sorted({x['period'] for x in ps})}
    return res


def score_groups(gm, p):
    """Adds z per metric and the group z (weighted mean over metrics with a value and a positive weight)."""
    wts, anc = p['metric_weights'], p['anchors']
    for g in gm.values():
        zs, num, den = {}, 0.0, 0.0
        for m in METRICS:
            a = anc[m]
            v = g.get(m)
            z = engine.anchor_z(v, a['anchor'], a['scale'], a['higher_is_stress']) if v is not None else None
            zs[m] = z
            if z is not None and wts[m] > 0:
                num += wts[m] * z
                den += wts[m]
        g['z_metrics'] = zs
        g['z'] = num / den if den > 0 else None
    return gm


def evaluate(T, rows_by_acc, filings, method):
    """The layer at time T (a dict)."""
    p, eng = method['layers']['L3'], method['engine']
    cut, tau = config_cuts(method), eng['tau']
    pairs, tally = pairs_at(T, rows_by_acc, filings, p, eng)
    gm = score_groups(group_metrics(pairs, p), p)
    scored = {g: v for g, v in gm.items() if v['facilities'] >= p['min_matched_facilities'] and v['z'] is not None}
    base_all = sum(x['weight_usd'] for x in pairs)
    wf = (sum(x['w'] for x in pairs) / base_all) if base_all else 0.0
    pt = {'session': T, 'pairs': pairs, 'tally': tally, 'groups': gm, 'scored_groups': sorted(scored),
          'weight_factor': wf, 'latest_filed': max((v['latest_filed'] for v in scored.values()), default=None),
          'latest_period': max((max(v['periods']) for v in scored.values()), default=None)}
    if not scored:
        st = 'stale' if pairs and wf == 0 else 'unavailable'
        return dict(pt, status=st, Z=None, level=None, cap=None, cap_reason=None, groups_stressed=[], weights=None)
    base = {g: (v['matched_weight_usd'] if p['group_weights'] == 'matched_principal' else 1.0) for g, v in scored.items()}
    capw = engine.cap_weights(base, eng['group_cap'])
    Z = sum(capw['weights'][g] * scored[g]['z'] for g in scored)
    levels = {g: engine.logistic(v['z'], tau) for g, v in scored.items()}
    capb, why, hot = engine.two_group_cap(levels, cut, eng['min_groups_for_stressed'])
    return dict(pt, status='fresh', Z=Z, level=engine.logistic(Z, tau), cap=capb, cap_reason=why, groups_stressed=hot,
                weights=capw, group_levels=levels)


def release_points(filings, as_of):
    """Month ends from the first filing's month to as_of, plus as_of itself."""
    first = min((f['filed'] for f in filings if f['filed'] <= as_of), default=None)
    if first is None:
        return []
    d = dt.date.fromisoformat(first[:8] + '01')
    end = dt.date.fromisoformat(as_of)
    out = []
    while True:
        nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        me = nxt - dt.timedelta(days=1)
        if me >= end:
            break
        out.append(me.isoformat())
        d = nxt
    out.append(as_of)
    return out


def is_month_end(d):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=1)).day == 1


def month_end_of(d):
    x = dt.date.fromisoformat(d)
    return (dt.date(x.year + (x.month == 12), x.month % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()


def carried_weight_factor(last, T, eng):
    """Principal-weighted staleness on T of the vintages behind a carried value (its own pairs, not newer filings)."""
    st = eng['staleness']
    base = sum(x['weight_usd'] for x in last['pairs'])
    if not base:
        return 0.0
    return sum(x['weight_usd'] * engine.staleness_weight((dt.date.fromisoformat(T) - dt.date.fromisoformat(x['period'])).days,
                                                         st['full_weight_days'], st['zero_weight_days'])
               for x in last['pairs']) / base


def layer_series(rows_by_acc, filings, method, as_of):
    """One point per release date (oldest first), with the band replayed under senior hysteresis.

    L3 publishes at month ends (the cadence of the SEC's monthly BDC file). A month-end point is fresh when a scored
    filing arrived since the previous point. Any other point (no new filing, or the build date between two month ends)
    holds the last published value (carried; stale once its staleness weight is 0): only its weight_factor moves, never
    the value, and a filing made after the last month end waits for the next one. So an edition built mid-month equals
    what any later replay shows for that day."""
    eng = method['engine']
    pts, prev, last = [], None, None
    for T in release_points(filings, as_of):
        pt = evaluate(T, rows_by_acc, filings, method)
        release = is_month_end(T)
        new = release and pt['status'] == 'fresh' and (prev is None or pt['latest_filed'] > prev)
        if new:
            last = pt
        elif last is not None:
            wf = carried_weight_factor(last, T, eng)
            pt = dict(last, session=T, status='stale' if wf <= 0 else 'carried', weight_factor=wf, tally=pt['tally'])
        else:
            waiting = pt['status'] == 'fresh'
            pt = dict(pt, status='unavailable' if waiting else pt['status'], Z=None, level=None, cap=None,
                      cap_reason=None, groups_stressed=[], weights=None, groups={}, scored_groups=[],
                      latest_filed=None, latest_period=None,
                      not_released=(f'not released yet: L3 publishes at month ends (next release point '
                                    f'{month_end_of(T)})' if waiting else None))
        if release:
            prev = T
        pts.append(pt)
    h = eng['hysteresis']
    bands = engine.replay_bands(pts, 'senior', config_cuts(method), up_closes=h['junior_up_closes'],
                                up_releases=h['senior_up_releases'], down_margin=h['down_margin'])
    for pt, b in zip(pts, bands):
        pt.update(band=b['band'], raw_band=b['raw_band'], band_moved=b['moved'], band_reason=b['reason'])
    return pts


# --- exposure map (facts only, no score) -----------------------------------------------------------------------------

EXPOSURE_FIELDS = ['scope', 'table', 'holder', 'holder_cik', 'borrower_group', 'category', 'mark_date', 'filed',
                   'facilities', 'holders', 'names', 'funded_principal', 'principal_flagged', 'cost', 'fair_value',
                   'fv_to_cost', 'net_assets', 'ai_fv_pct_net_assets', 'filing_url']


def exposure_map(rows_by_acc, filings, as_of, nav_for):
    """Per holder and per borrower group, from each holder's latest filing filed on or before as_of: signed-off,
    named-obligor, funded rows (loans and equity), as filed. nav_for(filing) -> net assets or None."""
    sel = select_filings(filings, as_of)
    holders, borrowers = [], {}
    for cik, per in sorted(sel.items()):
        f = per[max(per)]
        rs = [r for r in rows_by_acc.get(f['accession'], [])
              if r['review_status'] == 'signed off' and r['match_rule'] == 'named obligor' and r['funded']]
        if not rs:
            continue
        cost = sum(r['cost'] or 0 for r in rs)
        fv = sum(r['fair_value'] or 0 for r in rs)
        na = nav_for(f)
        holders.append({'scope': SCOPE, 'table': 'holder', 'holder': rs[0]['holder'], 'holder_cik': cik,
                        'mark_date': f['period'], 'filed': f['filed'], 'facilities': len(rs),
                        'names': '; '.join(sorted({r['borrower_group'] for r in rs})), 'cost': round(cost, 2),
                        'fair_value': round(fv, 2), 'fv_to_cost': round(fv / cost, 4) if cost else None,
                        'net_assets': na, 'ai_fv_pct_net_assets': round(100 * fv / na, 2) if na else None,
                        'filing_url': rs[0]['filing_url']})
        for r in rs:
            b = borrowers.setdefault(r['borrower_group'], {'scope': SCOPE, 'table': 'borrower', 'borrower_group': r['borrower_group'],
                                                           'category': r['category'], 'holders': set(), 'facilities': 0,
                                                           'funded_principal': 0.0, 'principal_flagged': 0, 'cost': 0.0,
                                                           'fair_value': 0.0, 'mark_dates': set()})
            b['holders'].add(cik)
            b['facilities'] += 1
            if r['principal_ok']:
                b['funded_principal'] += r['principal'] or 0
            else:
                b['principal_flagged'] += 1
            b['cost'] += r['cost'] or 0
            b['fair_value'] += r['fair_value'] or 0
            b['mark_dates'].add(f['period'])
    holders.sort(key=lambda x: -(x['ai_fv_pct_net_assets'] or 0))
    bl = []
    for b in borrowers.values():
        md = sorted(b.pop('mark_dates'))
        bl.append(dict(b, holders=len(b['holders']), mark_date=md[0] if len(md) == 1 else f'{md[0]}..{md[-1]}',
                       cost=round(b['cost'], 2), fair_value=round(b['fair_value'], 2),
                       funded_principal=round(b['funded_principal'], 2),
                       fv_to_cost=round(b['fair_value'] / b['cost'], 4) if b['cost'] else None))
    bl.sort(key=lambda x: -x['fair_value'])
    return holders, bl


def write_exposure_map(path, holders, borrowers, as_of):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.csv.part')
    with open(tmp, 'w', newline='') as f:
        f.write(f'# {SCOPE}: AI-infrastructure holdings of business development companies as filed, as of {as_of}. '
                'Facts only, no stress score; most AI debt is held outside BDCs.\n')
        w = csv.DictWriter(f, fieldnames=EXPOSURE_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(holders)
        w.writerows(borrowers)
    os.replace(tmp, path)


# --- inputs: fetch, cache, manifest ----------------------------------------------------------------------------------

def month_start(m):
    q = re.fullmatch(r'(\d{4})q([1-4])', m)
    if q:
        return dt.date(int(q.group(1)), 3 * int(q.group(2)) - 2, 1).isoformat()
    y, mo = m.split('_')
    return dt.date(int(y), int(mo), 1).isoformat()


def _is_zip(b):
    if b[:2] != b'PK':
        return False
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return 'soi.tsv' in z.namelist()


def fetch_month(m, root, offline, opener=None):
    """FetchResult-like dict for one monthly zip (cached under data/raw/sec_bdc/)."""
    raw = pathlib.Path(root) / 'data' / 'raw'
    url = bx.URL.format(m)
    r = fetch_get(url, 'sec_bdc', f'{m}_bdc.zip', validate=_is_zip, offline=True, root=raw)
    if r['ok'] or offline:
        return dict(r)
    ua = (os.environ.get('SEC_USER_AGENT') or '').strip()
    if not ua:
        return dict(r, status='failed', error=UA_HELP)
    r = fetch_get(url, 'sec_bdc', f'{m}_bdc.zip', validate=_is_zip, root=raw, headers={'User-Agent': ua},
                  min_interval=0.125, timeout=600, opener=opener)
    return dict(r)


def record_manifest(path, name, url, sha, nbytes):
    """Append a zip to manifests/sec_bdc_sha256.csv (bdc_exposure's format) if absent. Returns match vs recorded."""
    path = pathlib.Path(path)
    rows = list(csv.DictReader(open(path, newline=''))) if path.exists() else []
    old = [r for r in rows if r['file'] == name]
    if old:
        return any(r['sha256'] == sha for r in old)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if new:
            w.writeheader()
        w.writerow({'file': name, 'url': url, 'bytes': nbytes, 'sha256': sha,
                    'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')})
    return True


def load_inputs(as_of, method, root, offline, opener=None):
    """(rows_by_accession, filings, month report, input records, zip paths by name)."""
    p = method['layers']['L3']
    root = pathlib.Path(root)
    rows_by_acc, filings, months, inputs, paths = {}, [], [], [], {}
    for m in p['months']:
        if month_start(m) > as_of:
            months.append({'month': m, 'status': 'after as_of: not read'})
            continue
        r = fetch_month(m, root, offline, opener)
        if not r['ok']:
            months.append({'month': m, 'status': r['status'], 'error': r.get('error')})
            continue
        path = pathlib.Path(r['path'])
        match = record_manifest(root / 'manifests' / 'sec_bdc_sha256.csv', path.name, bx.URL.format(m), r['sha256'], r['bytes'])
        rows, fl = load_zip(path)
        paths[path.name] = path
        rec = file_record(path, root)
        rec['manifest_match'] = match
        inputs.append(rec)
        months.append({'month': m, 'status': r['status'], 'sha256': r['sha256'], 'manifest_match': match,
                       'filings': len(fl), 'matched_rows': len(rows)})
        filings += fl
        for x in rows:
            rows_by_acc.setdefault(x['accession'], []).append(x)
    # a row is only as available as its filing: drop rows whose accession is not a 10-K / 10-Q in sub.tsv
    known = {f['accession'] for f in filings}
    rows_by_acc = {a: v for a, v in rows_by_acc.items() if a in known}
    return rows_by_acc, filings, months, inputs, paths


# --- the layer -------------------------------------------------------------------------------------------------------

def _r(x, k=4):
    return None if x is None else round(x, k)


def snapshot(as_of, rows_by_acc, filings, method):
    """The L3 result for as_of (dict). Only filings with filed <= as_of are read."""
    p = method['layers']['L3']
    filings = [f for f in filings if f['filed'] <= as_of]
    keep = {f['accession'] for f in filings}
    rows_by_acc = {a: v for a, v in rows_by_acc.items() if a in keep}
    base = {'layer': 'L3', 'name': p['name'], 'kind': 'senior', 'build_date': as_of, 'as_of': None, 'scope': SCOPE,
            'score_rows': p['score_rows'], 'metrics': {'m4_non_accrual': M4_STUB}}
    if p['score_rows'] != 'signed_off':
        base['warning'] = 'comparison run: named candidates without a human sign-off are scored'
    pts = layer_series(rows_by_acc, filings, method, as_of)
    if not pts:
        return dict(base, status='unavailable', reason='no BDC filing on or before the as-of date', available=None,
                    Z=None, level=None, published=None, band=None, _series=[])
    pt = pts[-1]
    last_fresh = next((x for x in reversed(pts) if x['status'] == 'fresh'), None)
    groups, attribution = {}, {}
    for g, v in sorted(pt['groups'].items()):
        groups[g] = {'facilities': v['facilities'], 'holders': v['holders'],
                     'matched_principal_usd': round(v['matched_principal_usd'], 2),
                     'staleness_weighted_usd': round(v['matched_weight_usd'], 2), 'periods': v['periods'],
                     'latest_filed': v['latest_filed'], 'm0_level_fv_to_cost': _r(v['m0_level']),
                     'm1_markdown': _r(v['m1_markdown']), 'm2_breadth': _r(v['m2_breadth']),
                     'm3_any_pik': _r(v['m3_any_pik']), 'm3_origination_pik_not_scored': _r(v['m3_origination_pik']),
                     'm3_added_pik': _r(v['m3_added_pik']), 'm4_non_accrual': 'not_implemented',
                     'z_metrics': {m: _r(z, 3) for m, z in v['z_metrics'].items()}, 'z': _r(v['z'], 3),
                     'scored': g in pt['scored_groups']}
    if pt.get('weights'):
        w = pt['weights']
        for g in pt['scored_groups']:
            z = pt['groups'][g]['z']
            attribution[g] = {'weight': _r(w['weights'][g]), 'contribution': _r(w['weights'][g] * z, 3),
                              'uncapped_weight': _r(w['uncapped'][g]), 'uncapped_contribution': _r(w['uncapped'][g] * z, 3)}
    t = pt['tally']
    match = {k: (_r(v) if isinstance(v, float) else v) for k, v in t.items()}
    match['key'] = ('holder CIK + borrower group + instrument class + maturity + spread (or fixed rate) + letters of '
                    'the obligor text; fallbacks: terms only, then text only, each when unique on both sides')
    status = pt['status']
    out = dict(base, status=status, session=pt['session'], as_of=pt.get('latest_period'),
               available=pt['latest_filed'], release_point=last_fresh['session'] if last_fresh else None,
               dates_note=('as_of: latest period end (vintage) of the scored facilities; available: latest filing date '
                           'among them; release_point: the monthly release point at which this value was first '
                           'published (on or after available); session: the build point'),
               Z=_r(pt['Z'], 3), level=_r(pt['level'], 2),
               published=engine.published(pt['level']), raw_band=pt.get('raw_band'), band=pt.get('band'),
               band_status='fresh' if status == 'fresh' else 'held (no new release: the band cannot move)',
               band_moved=pt.get('band_moved'), band_reason=pt.get('band_reason'),
               weight_factor=_r(pt['weight_factor']),
               weight_factor_note='principal-weighted staleness of the matched vintages (full to 120 days after period '
                                  'end, 0 at 180); the value is never decayed',
               two_group={'cap': pt['cap'], 'reason': pt['cap_reason'], 'groups_stressed_or_worse': pt['groups_stressed']},
               cap_infeasible=bool(pt.get('weights') and pt['weights']['cap_infeasible']),
               groups_live=len(pt['scored_groups']), groups=groups, attribution=attribution, match=match,
               group_note='group values are layer attribution over the observed BDC book, not a credit view of the borrower',
               _series=[{'session': x['session'], 'known': x['session'], 'Z': x['Z'], 'level': x['level'],
                         'status': x['status'], 'weight_factor': x['weight_factor'], 'band': x.get('band'),
                         'raw_band': x.get('raw_band'), 'band_moved': x.get('band_moved'),
                         'groups_stressed': x.get('groups_stressed') or []} for x in pts])
    if status != 'fresh':
        out['reason'] = {'carried': 'no new scored filing since the previous release point',
                         'stale': 'every matched vintage is older than the staleness limit',
                         'unavailable': 'no matched, signed-off, funded facility in consecutive vintages'}.get(status)
        if pt.get('not_released'):
            out['reason'] = pt['not_released']
    return out


def compute(as_of, method, ctx):
    """Build-facing entry point: (result, inputs). ctx: {'root': repo path, 'offline': bool}."""
    root = pathlib.Path(ctx.get('root') or ROOT)
    p = method['layers']['L3']
    rows_by_acc, filings, months, inputs, paths = load_inputs(as_of, method, root, ctx.get('offline', False),
                                                              ctx.get('opener'))
    log_p = root / 'research' / 'bdc' / 'review_log.csv'
    log = load_review_log(log_p)
    if log_p.exists():
        inputs.append(file_record(log_p, root))
    for v in rows_by_acc.values():
        apply_review(v, log)
    missing = [m for m in months if m['status'] not in ('cached', 'downloaded', 'refreshed', 'stale_cache')
               and not m['status'].startswith('after')]
    if not filings:
        errs = '; '.join(f"{m['month']}: {m.get('error') or m['status']}" for m in missing)
        return ({'layer': 'L3', 'name': p['name'], 'kind': 'senior', 'build_date': as_of, 'as_of': None, 'scope': SCOPE,
                 'status': 'unavailable', 'reason': f'no SEC BDC file available ({errs})', 'months': months,
                 'available': None, 'Z': None, 'band': None, 'metrics': {'m4_non_accrual': M4_STUB}, '_series': []}, inputs)
    res = snapshot(as_of, rows_by_acc, filings, method)
    # a zip read from the cache and one downloaded on this run are the same input (sha256 shown): same edition
    res['months'] = [dict(m, status='cached') if m['status'] in ('downloaded', 'refreshed') else m for m in months]
    if missing:
        res['missing_months'] = [m['month'] for m in missing]
    last = max(f['filed'] for f in filings)
    res['source_lag'] = (f'latest filing in the downloaded files: {last}; filings after the last published monthly '
                         'file are not in the data sets yet')
    nav_cache = {}

    def nav_for(f):
        src = f['source_file']
        if src not in nav_cache:
            nav_cache[src] = net_assets(paths[src]) if src in paths else {}
        v = nav_cache[src].get(f['cik'])
        return v['net_assets'] if v and v['period'] == f['period'] else None

    holders, borrowers = exposure_map(rows_by_acc, [f for f in filings if f['filed'] <= as_of], as_of, nav_for)
    out = root / 'data' / 'stack' / 'exposure_map.csv'
    write_exposure_map(out, holders, borrowers, as_of)
    res['exposure_map'] = {'path': str(out.relative_to(root)) if out.is_relative_to(root) else str(out), 'scope': SCOPE,
                           'holders': len(holders), 'borrower_groups': len(borrowers),
                           'cost': round(sum(h['cost'] for h in holders), 2),
                           'fair_value': round(sum(h['fair_value'] for h in holders), 2),
                           'note': 'facts as filed, no stress score on any named company'}
    return res, inputs


# --- register CLI ------------------------------------------------------------------------------------------------------

def register_rows(rows_by_acc, filings, as_of):
    """One row per holder x facility x period (latest filing per holder and period filed <= as_of), with the
    prior-vintage match of each funded debt facility."""
    sel = select_filings(filings, as_of)
    out = []
    for cik, per in sorted(sel.items()):
        for P, f in sorted(per.items()):
            rs = rows_by_acc.get(f['accession'], [])
            if not rs:
                continue
            fp = per.get(prior_quarter_end(P))
            cur = _aggregate(rs)
            prev = _aggregate(rows_by_acc.get(fp['accession'], [])) if fp else {}
            m = {c['key_exact']: (pv, tier) for c, pv, tier in match_facilities(cur, prev)}
            for r in rs:
                hit = m.get(r['key_exact'])
                if not r['funded']:
                    pm = 'unfunded: not matched'
                elif r['instrument_class'] != 'debt':
                    pm = f"{r['instrument_class']}: not matched"
                elif fp is None:
                    pm = 'no prior vintage in the files'
                elif hit and hit[0] is not None:
                    pm = hit[1]
                elif r['borrower_group'] not in {v['borrower_group'] for v in prev.values()}:
                    pm = 'unmatched: group not held in the prior vintage'
                else:
                    pm = 'unmatched'
                out.append(dict(r, period=P, prior_period=prior_quarter_end(P), match_key=r['key_exact'], prior_match=pm,
                                prior_fv_to_cost=_r(hit[0]['fvc']) if hit and hit[0] is not None else None,
                                bdc_facility_key=r['facility_key'], pik_rate=r['pik'], fv_to_cost=r['fv_to_cost']))
    out.sort(key=lambda r: (r['match_rule'] != 'named obligor', r['borrower_group'], r['holder'], r['period'], r['match_key']))
    return out


def main(argv=None):
    from . import config
    ap = argparse.ArgumentParser(prog='python -m stack.l3_bdc', description='L3 obligor register and match rate')
    ap.add_argument('--register', action='store_true', help='write research/bdc/stack_register.csv')
    ap.add_argument('--as-of', default=dt.date.today().isoformat())
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--config')
    a = ap.parse_args(argv)
    method, _ = config.load(a.config)
    rows_by_acc, filings, months, _, _ = load_inputs(a.as_of, method, ROOT, a.offline)
    log = load_review_log(ROOT / 'research' / 'bdc' / 'review_log.csv')
    for v in rows_by_acc.values():
        apply_review(v, log)
    rows = register_rows(rows_by_acc, filings, a.as_of)
    if a.register:
        path = ROOT / 'research' / 'bdc' / 'stack_register.csv'
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=REG_FIELDS, extrasaction='ignore')
            w.writeheader()
            w.writerows(rows)
    _, tally = pairs_at(a.as_of, rows_by_acc, filings, method['layers']['L3'], method['engine'])
    stat = {}
    for r in rows:
        stat.setdefault(r['review_status'], 0)
        stat[r['review_status']] += 1
    print(json.dumps({'as_of': a.as_of, 'register_rows': len(rows), 'review_status': stat,
                      'prior_match': {k: sum(1 for r in rows if r['prior_match'] == k) for k in sorted({r['prior_match'] for r in rows})},
                      'latest_vintage_match': {k: (_r(v) if isinstance(v, float) else v) for k, v in tally.items()},
                      'months': months}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
