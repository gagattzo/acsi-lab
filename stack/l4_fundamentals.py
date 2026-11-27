"""L4 fundamentals from SEC EDGAR: companyfacts (XBRL) and submissions (8-K item codes), point in time by filing date.

Metrics per issuer (each higher = more stress):
  f1 funding gap      (debt due within 24 months + capex TTM - operating cash flow TTM) / (cash + undrawn facilities)
  f2 leverage         (debt + finance lease + operating lease liabilities - cash) / revenue TTM
  f3 interest burden  leverage effect (change in debt x prior effective rate). TODO: not implemented, never a number
  f4 8-K debt events  8-K and 8-K/A filings in the last 90 days (filing date <= as-of) with item 2.03 or 2.04; item 1.01
                      is counted and shown but not scored
Each scored metric: level_weight x anchor z ((x - a) / s) + (1 - level_weight) x 4-quarter change z ((x - x a year
earlier) / s, the same anchor scale). A missing part lowers the metric's weight; it never counts as calm. Quarterly
metrics carry the engine's staleness weight (full to 120 days after the period end, 0 at 180; the value is never moved
toward neutral). Issuer score = mean of its live metric z; issuer weight = mean of its scored metrics' weights (f3 is
not a scored metric until it is implemented). Group z = issuer-weighted mean; layer Z = capped group weights x group z
(20% cap, equal weights and cap_infeasible below 5 groups); the layer is published only when the live issuer weight is
at least min_live_share of the target. Band: senior hysteresis (one new release moves it up) with the 2-group rule,
replayed over every release date from history_start to the as-of date.

Point in time. Every XBRL fact is available from its `filed` date. For as-of D and one (concept, period) the facts filed
on or before D are read in filing order: the latest value applies, but its `filed` date is the first filing that
reported that same value (a comparative column repeating a number does not make it new). A restatement (a later filing
with a different value) applies only from its own filing date; the original value and date are kept in the output, so
a past edition rebuilt today shows what was known then. Filing dates are EDGAR filing dates (an acceptance after 17:30
ET is dated the next business day), so they never precede public availability.

TTM. From duration facts of one tag: an annual fact (350 to 380 days) ending on the period end, else four consecutive
quarters, where a quarter is a direct 80 to 100 day fact or the difference of two year-to-date facts with the same start
(this also derives Q4 as the fiscal year minus nine months). Fiscal years that end in May (Oracle) or June (IREN) work
the same way; an issuer that files only annual reports gets a TTM at fiscal year ends only.

Tag coverage. companyfacts holds only standard-taxonomy facts without dimensions, so issuer-specific (extension) tags
are invisible here. Debt is read from a ladder of recipes (DEBT_RECIPES); a partial recipe (convertibles only, or one
instrument's carrying amount) is used only when no full recipe exists at that period end and no full recipe in the
previous 365 days reported more debt (otherwise flagged debt_tag_gap and the metric is missing). Undrawn facilities are
almost never tagged without a dimension: f1 then uses cash only and flags undrawn_missing, which overstates the gap.
Nebius files 20-F and 6-K: one annual period is tagged and 6-K filings carry no 8-K item codes (coverage gap, shown).

Network. Downloads go to data/raw/sec/<UTC date>/{companyfacts,submissions}/ through stack/fetch.py (atomic, hashed,
<= 8 requests per second by default). The User-Agent comes from the environment variable SEC_USER_AGENT, required for
any download; --offline reads the newest cached snapshot and never needs it.

Anchors, scales, windows and weights in stack/stack-v0.yaml are proposed examples, not calibrated.
"""
import bisect, datetime as dt, json, os, pathlib, statistics

from . import engine
from .config import cuts as config_cuts
from .fetch import file_record, get as fetch_get

ROOT = pathlib.Path(__file__).resolve().parent.parent
CF_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
SUB_URL = 'https://data.sec.gov/submissions/CIK{cik:010d}.json'
SUB_PAGE_URL = 'https://data.sec.gov/submissions/{name}'
FILING_URL = 'https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/'
UA_ENV = 'SEC_USER_AGENT'
SCORED = ('f1', 'f2', 'f4')
EVENT_FORMS = ('8-K', '8-K/A')
NOT_IMPLEMENTED_F3 = ('f3 leverage effect of interest burden needs effective rate = interest expense TTM / average debt '
                      'and a reviewed scale; not implemented, so it is shown and never scored')

# --- tag map (decoding, not method: one reviewed change like harness/dtcc.py ENTITIES) --------------------------------
CASH = ['us-gaap:CashAndCashEquivalentsAtCarryingValue', 'ifrs-full:CashAndCashEquivalents',
        'us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents']
OCF = ['us-gaap:NetCashProvidedByUsedInOperatingActivities',
       'us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations',
       'ifrs-full:CashFlowsFromUsedInOperatingActivities']
CAPEX = ['us-gaap:PaymentsToAcquirePropertyPlantAndEquipment', 'us-gaap:PaymentsToAcquireProductiveAssets',
         'ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities',
         'ifrs-full:PurchaseOfPropertyPlantAndEquipment']
REVENUE = ['us-gaap:Revenues', 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
           'us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax', 'ifrs-full:Revenue']
CURRENT_DEBT = ['us-gaap:DebtCurrent', 'us-gaap:LongTermDebtCurrent', 'us-gaap:NotesPayableCurrent',
                'us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent', 'us-gaap:ConvertibleDebtCurrent',
                'us-gaap:OtherLongTermDebtCurrent', 'us-gaap:ShortTermBorrowings']
MAT12 = 'us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths'
MAT24 = 'us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo'
UNDRAWN = ['us-gaap:LineOfCreditFacilityRemainingBorrowingCapacity']
# (name, required tags summed, optional tags added when present, quality)
DEBT_RECIPES = [
    ('LongTermDebt', ['us-gaap:LongTermDebt'], [], 'full'),
    ('DebtLongtermAndShorttermCombinedAmount', ['us-gaap:DebtLongtermAndShorttermCombinedAmount'], [], 'full'),
    ('LongTermDebtNoncurrent+current', ['us-gaap:LongTermDebtNoncurrent'],
     ['us-gaap:LongTermDebtCurrent|us-gaap:DebtCurrent'], 'full'),
    ('LongTermNotesAndLoans+NotesPayableCurrent', ['us-gaap:LongTermNotesAndLoans'], ['us-gaap:NotesPayableCurrent'], 'full'),
    ('LongTermNotesPayable+NotesPayableCurrent', ['us-gaap:LongTermNotesPayable'], ['us-gaap:NotesPayableCurrent'], 'full'),
    ('IFRS Borrowings', ['ifrs-full:Borrowings'], [], 'full'),
    ('IFRS NoncurrentBorrowings+CurrentBorrowings', ['ifrs-full:NoncurrentPortionOfNoncurrentBorrowings'],
     ['ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings'], 'full'),
    ('DebtInstrumentCarryingAmount', ['us-gaap:DebtInstrumentCarryingAmount'], [], 'partial'),
    ('ConvertibleDebt', ['us-gaap:ConvertibleDebt'], [], 'partial'),
    ('ConvertibleNotesPayable', ['us-gaap:ConvertibleNotesPayable'], [], 'partial'),
    ('ConvertibleDebtNoncurrent+current', ['us-gaap:ConvertibleDebtNoncurrent'], ['us-gaap:ConvertibleDebtCurrent'], 'partial'),
]
LEASES = {
    'operating_lease': [['us-gaap:OperatingLeaseLiability'],
                        ['us-gaap:OperatingLeaseLiabilityCurrent', 'us-gaap:OperatingLeaseLiabilityNoncurrent'],
                        ['ifrs-full:LeaseLiabilities']],
    'finance_lease': [['us-gaap:FinanceLeaseLiability'],
                      ['us-gaap:FinanceLeaseLiabilityCurrent', 'us-gaap:FinanceLeaseLiabilityNoncurrent']],
}
NONNEG = set(REVENUE) | set(CAPEX)
ALL_TAGS = sorted({t for ts in (CASH, OCF, CAPEX, REVENUE, CURRENT_DEBT, UNDRAWN, [MAT12, MAT24]) for t in ts}
                  | {x for _, req, opt, _ in DEBT_RECIPES for t in req + opt for x in t.split('|')}
                  | {t for rs in LEASES.values() for r in rs for t in r})


class SecUserAgentMissing(RuntimeError):
    pass


def same_value(a, b):
    """Equal up to rounding: later filings often repeat a comparative rounded to thousands or millions (CoreWeave's
    2025 nine-month operating cash flow is 1,499,051,000 in the 2025 10-Q and 1,499,000,000 in the 2026 one)."""
    return abs(a - b) <= max(500_000.0, 1e-3 * max(abs(a), abs(b)))


def _d(s):
    return dt.date.fromisoformat(s)


def _days(a, b):
    return (_d(b) - _d(a)).days


def _shift(s, days):
    return (_d(s) + dt.timedelta(days=days)).isoformat()


# --- point-in-time facts -------------------------------------------------------------------------------------------

class Facts:
    """Index of one companyfacts payload: USD facts of the tags in ALL_TAGS (or `tags`), by period."""

    def __init__(self, cf, tags=None):
        self.cik = cf.get('cik')
        self.entity = cf.get('entityName')
        want = set(tags or ALL_TAGS)
        self.inst, self.dur, self.filed_dates = {}, {}, set()
        for tax, block in (cf.get('facts') or {}).items():
            for tag, d in (block or {}).items():
                key = f'{tax}:{tag}'
                if key not in want:
                    continue
                for x in ((d.get('units') or {}).get('USD') or []):
                    if x.get('val') is None or not x.get('end') or not x.get('filed'):
                        continue
                    e = (x['filed'], x.get('accn') or '', x['val'], x.get('form'))
                    if x.get('start'):
                        self.dur.setdefault(key, {}).setdefault((x['start'], x['end']), []).append(e)
                    else:
                        self.inst.setdefault(key, {}).setdefault(x['end'], []).append(e)
                    self.filed_dates.add(x['filed'])
        for m in (self.inst, self.dur):
            for per in m.values():
                for k in per:
                    per[k].sort(key=lambda e: (e[0], e[1]))
                    per[k] = (per[k], [e[0] for e in per[k]])
        self._cache = {}

    @staticmethod
    def pick(entries, as_of):
        """Point-in-time value of one (concept, period) as of `as_of`, or None if nothing was filed by then."""
        rows, filed = entries
        n = bisect.bisect_right(filed, as_of)
        if not n:
            return None
        first = cur = rows[0]
        for e in rows[1:n]:
            if not same_value(e[2], cur[2]):
                cur = e
        out = {'val': cur[2], 'filed': cur[0], 'accn': cur[1], 'form': cur[3], 'restated': cur is not first}
        if cur is not first:
            out.update(original_val=first[2], original_filed=first[0], original_accn=first[1])
        return out

    def instant(self, tag, end, as_of):
        per = self.inst.get(tag, {})
        return self.pick(per[end], as_of) if end in per else None

    def instant_ends(self, tags, as_of):
        out = set()
        for t in tags:
            for e, ent in self.inst.get(t, {}).items():
                if ent[1][0] <= as_of:
                    out.add(e)
        return sorted(out, reverse=True)

    def durations(self, tag, as_of):
        key = ('dur', tag, as_of)
        if key not in self._cache:
            out = {}
            for k, ent in self.dur.get(tag, {}).items():
                p = self.pick(ent, as_of)
                if p:
                    out[k] = p
            self._cache[key] = out
        return self._cache[key]

    def quarters(self, tag, as_of):
        key = ('q', tag, as_of)
        if key not in self._cache:
            self._cache[key] = quarter_values(self.durations(tag, as_of), nonneg=tag in NONNEG)
        return self._cache[key]

    def ttm(self, tags, end, as_of):
        """TTM of the first tag in `tags` that yields one at `end`: dict(val, tag, basis, filed, accns, ...)."""
        for t in tags:
            r = ttm_value(self.durations(t, as_of), self.quarters(t, as_of), end)
            if r:
                return dict(r, tag=t)
        return None


def quarter_values(dur, nonneg=False):
    """{end: {'val', 'basis', 'parts': [picks]}} from duration picks {(start, end): pick}: direct 80-100 day facts first,
    else year-to-date differences with the same start (Q2 = 6M - 3M, Q3 = 9M - 6M, Q4 = FY - 9M). With nonneg (revenue,
    capex) a negative derived quarter is dropped: it means the two year-to-date facts have different scopes (Applied
    Digital's FY2025 revenue was restated for discontinued operations in the 10-K, its nine months were not)."""
    q, by_start = {}, {}
    for (s, e), p in dur.items():
        by_start.setdefault(s, []).append((e, p))
    for (s, e), p in sorted(dur.items()):
        n = _days(s, e)
        if 80 <= n <= 100:
            rank = abs(n - 91)
            if e not in q or rank < q[e]['_rank']:
                q[e] = {'val': p['val'], 'basis': 'quarter', 'parts': [p], 'start': s, '_rank': rank}
    for (s, e), p in sorted(dur.items()):
        if e in q or not 100 < _days(s, e) <= 380:
            continue
        for e2, p2 in sorted(by_start[s]):
            if 80 <= _days(e2, e) <= 100:
                if nonneg and p['val'] - p2['val'] < 0:
                    continue
                q[e] = {'val': p['val'] - p2['val'], 'basis': 'ytd_difference', 'parts': [p, p2], 'start': e2, '_rank': 99}
                break
    return q


def ttm_value(dur, q, end):
    annual = sorted(((abs(_days(s, e) - 365), s, p) for (s, e), p in dur.items() if e == end and 350 <= _days(s, e) <= 380))
    if annual:
        _, s, p = annual[0]
        return _ttm_out(p['val'], 'annual', [p], [end])
    if end not in q:
        return None
    chain, cur = [end], end
    for _ in range(3):
        prev = sorted((abs(_days(e, cur) - 91), e) for e in q if 80 <= _days(e, cur) <= 100)
        if not prev:
            return None
        cur = prev[0][1]
        chain.append(cur)
    parts = [p for e in chain for p in q[e]['parts']]
    basis = 'four_quarters' + ('' if all(q[e]['basis'] == 'quarter' for e in chain) else ' (some derived from year-to-date)')
    return _ttm_out(sum(q[e]['val'] for e in chain), basis, parts, chain)


def _ttm_out(val, basis, parts, ends):
    last = max(parts, key=lambda p: (p['filed'], p['accn']))
    out = {'val': val, 'basis': basis, 'quarter_ends': ends, 'filed': last['filed'], 'accn': last['accn'],
           'form': last['form'], 'restated': any(p['restated'] for p in parts)}
    return out


def _inst_first(F, tags, end, as_of):
    for t in tags:
        p = F.instant(t, end, as_of)
        if p:
            return dict(p, tag=t)
    return None


def _sum_tags(F, req, opt, end, as_of):
    parts = []
    for t in req:
        p = F.instant(t, end, as_of)
        if not p:
            return None
        parts.append(dict(p, tag=t))
    for alts in opt:
        for t in alts.split('|'):
            p = F.instant(t, end, as_of)
            if p:
                parts.append(dict(p, tag=t))
                break
    return parts


def debt_at(F, end, as_of):
    """(value, info) of total debt at `end` from the first recipe that resolves, or (None, info)."""
    full_prior = None
    for name, req, opt, quality in DEBT_RECIPES:
        parts = _sum_tags(F, req, opt, end, as_of)
        if not parts:
            continue
        val = sum(p['val'] for p in parts)
        flags = []
        if name == 'LongTermDebt':  # some filers tag LongTermDebt with the noncurrent amount only
            nc = F.instant('us-gaap:LongTermDebtNoncurrent', end, as_of)
            cur = F.instant('us-gaap:LongTermDebtCurrent', end, as_of)
            if nc and cur and nc['val'] == val and cur['val'] > 0:
                parts.append(dict(cur, tag='us-gaap:LongTermDebtCurrent'))
                val += cur['val']
                flags.append('long_term_debt_equals_noncurrent: current portion added')
        info = {'basis': name, 'quality': quality, 'tags': [p['tag'] for p in parts], 'parts': parts, 'flags': flags}
        if quality == 'partial':
            if full_prior is None:
                full_prior = _full_debt_prior(F, end, as_of)
            if full_prior and full_prior[0] > val:
                return None, {'basis': name, 'quality': quality, 'flags': [
                    f'debt_tag_gap: only {name} is tagged at {end} ({val:,.0f}), less than the full debt tagged at '
                    f'{full_prior[1]} ({full_prior[0]:,.0f}); the rest is likely in extension tags, so the metric is missing']}
            info['flags'].append(f'debt_partial: only {name} is tagged at this period end (may understate debt)')
        return val, info
    return None, {'basis': None, 'flags': ['debt_missing: no debt tag at this period end']}


def _full_debt_prior(F, end, as_of):
    best = None
    for e in F.instant_ends([t for _, req, _, q in DEBT_RECIPES if q == 'full' for t in req], as_of):
        if not 0 < _days(e, end) <= 365:
            continue
        for name, req, opt, quality in DEBT_RECIPES:
            if quality != 'full':
                continue
            parts = _sum_tags(F, req, opt, e, as_of)
            if parts:
                v = sum(p['val'] for p in parts)
                if best is None or v > best[0]:
                    best = (v, e)
                break
    return best


def debt_due_24m(F, end, as_of):
    """(value, info): principal due within 24 months of `end`."""
    m12, m24 = F.instant(MAT12, end, as_of), F.instant(MAT24, end, as_of)
    if m12 and m24:
        return m12['val'] + m24['val'], {'basis': 'maturity_schedule (next 12 months + year two)',
                                         'parts': [dict(m12, tag=MAT12), dict(m24, tag=MAT24)], 'flags': []}
    cur = dict(m12, tag=MAT12) if m12 else _inst_first(F, CURRENT_DEBT, end, as_of)
    if not cur:
        return None, {'basis': None, 'flags': ['debt_due_missing: neither a maturity schedule nor current debt is tagged']}
    # year two from the latest maturity schedule in the previous 366 days (an annual report's table)
    prev = [e for e in F.instant_ends([MAT24], as_of) if 0 < _days(e, end) <= 366]
    if prev:
        y2 = F.instant(MAT24, prev[0], as_of)
        return cur['val'] + y2['val'], {
            'basis': f'current debt at {end} + year two of the schedule at {prev[0]}', 'parts': [cur, dict(y2, tag=MAT24)],
            'flags': [f'year_two_from_earlier_schedule: year two is from the {prev[0]} schedule']}
    return cur['val'], {'basis': f'current debt only ({cur["tag"]})', 'parts': [cur],
                        'flags': ['year_two_missing: only debt due within 12 months is tagged (understates f1)']}


def lease_at(F, kind, end, as_of):
    for recipe in LEASES[kind]:
        parts = _sum_tags(F, recipe, [], end, as_of)
        if parts:
            return sum(p['val'] for p in parts), parts
    return None, []


# --- metric values at one period end -------------------------------------------------------------------------------

def _src(parts):
    """filed = when the value became computable (latest component filing), accession of that filing."""
    parts = [p for p in parts if p]
    last = max(parts, key=lambda p: (p['filed'], p['accn']))
    return {'filed': last['filed'], 'accession': last['accn'], 'form': last['form'],
            'restated_inputs': sorted({p.get('tag', '?') for p in parts if p.get('restated')})}


def f1_at(F, end, as_of):
    """(value, detail) or (None, reason)."""
    ocf, capex, cash = F.ttm(OCF, end, as_of), F.ttm(CAPEX, end, as_of), _inst_first(F, CASH, end, as_of)
    miss = [n for n, v in (('operating cash flow TTM', ocf), ('capex TTM', capex), ('cash', cash)) if v is None]
    if miss:
        return None, f'{", ".join(miss)} not available at {end}'
    due, dinfo = debt_due_24m(F, end, as_of)
    if due is None:
        return None, '; '.join(dinfo['flags'])
    undrawn = _inst_first(F, UNDRAWN, end, as_of)
    flags = list(dinfo['flags'])
    if undrawn is None:
        flags.append('undrawn_missing: undrawn facilities not tagged, cash only (overstates the gap)')
    denom = cash['val'] + (undrawn['val'] if undrawn else 0)
    if denom <= 0:
        return None, f'cash + undrawn facilities is {denom} at {end}'
    v = (due + capex['val'] - ocf['val']) / denom
    det = {'debt_due_24m': due, 'debt_due_basis': dinfo['basis'], 'capex_ttm': capex['val'], 'capex_tag': capex['tag'],
           'capex_basis': capex['basis'], 'ocf_ttm': ocf['val'], 'ocf_tag': ocf['tag'], 'ocf_basis': ocf['basis'],
           'cash': cash['val'], 'cash_tag': cash['tag'], 'undrawn': undrawn['val'] if undrawn else None, 'flags': flags}
    det.update(_src([ocf, capex, cash, undrawn] + dinfo['parts']))
    return v, det


def f2_at(F, end, as_of):
    rev, cash = F.ttm(REVENUE, end, as_of), _inst_first(F, CASH, end, as_of)
    miss = [n for n, v in (('revenue TTM', rev), ('cash', cash)) if v is None]
    if miss:
        return None, f'{", ".join(miss)} not available at {end}'
    if rev['val'] <= 0:
        return None, f'revenue TTM is {rev["val"]} at {end}'
    debt, dinfo = debt_at(F, end, as_of)
    if debt is None:
        return None, '; '.join(dinfo['flags'])
    flags = list(dinfo['flags'])
    leases, parts = {}, []
    for kind in ('finance_lease', 'operating_lease'):
        v, ps = lease_at(F, kind, end, as_of)
        if v is None:
            flags.append(f'{kind}_missing: not tagged at {end}, counted as 0 (understates f2)')
        leases[kind] = v
        parts += ps
    if any('CapitalLease' in t for t in dinfo['tags']) and leases['finance_lease']:
        flags.append('finance_lease_in_debt_tag: finance lease not added again')
        leases['finance_lease'] = None
    v = (debt + (leases['finance_lease'] or 0) + (leases['operating_lease'] or 0) - cash['val']) / rev['val']
    det = {'debt': debt, 'debt_basis': dinfo['basis'], 'debt_tags': dinfo['tags'], 'finance_lease': leases['finance_lease'],
           'operating_lease': leases['operating_lease'], 'cash': cash['val'], 'cash_tag': cash['tag'],
           'revenue_ttm': rev['val'], 'revenue_tag': rev['tag'], 'revenue_basis': rev['basis'], 'flags': flags}
    det.update(_src([rev, cash] + dinfo['parts'] + parts))
    return v, det


VALUE_AT = {'f1': f1_at, 'f2': f2_at}
PERIOD_TAGS = CASH  # candidate period ends: balance-sheet dates with a cash fact


def latest_value(F, metric, as_of, before=None):
    """(end, value, detail, misses) at the latest period end (<= before, if given) where `metric` can be computed."""
    misses = []
    for e in F.instant_ends(PERIOD_TAGS, as_of):
        if e > as_of or (before and e > before):
            continue
        v, det = VALUE_AT[metric](F, e, as_of)
        if v is not None:
            return e, v, det, misses
        misses.append({'period_end': e, 'reason': det})
        if len(misses) >= 8:
            break
    return None, None, None, misses


def prior_value(F, metric, end, as_of, change_days, tol):
    """The metric about `change_days` before `end` (period end within tol days), with facts known at as_of."""
    target = _shift(end, -change_days)
    cands = sorted((abs(_days(e, target)), e) for e in F.instant_ends(PERIOD_TAGS, as_of) if abs(_days(e, target)) <= tol)
    reasons = []
    for _, e in cands:
        v, det = VALUE_AT[metric](F, e, as_of)
        if v is not None:
            return e, v, det
        reasons.append(f'{e}: {det}')
    return None, None, '; '.join(reasons) or f'no period end within {tol} days of {target}'


# --- 8-K items -------------------------------------------------------------------------------------------------------

def load_events(subs):
    """Filings from submissions payloads (the main JSON plus any extra pages): list of dicts, newest first, deduplicated
    by accession. Each: filed, form, items (list), accession, report_date."""
    seen, out = set(), []
    for s in subs:
        block = s.get('filings', {}).get('recent') if 'filings' in s else s
        if not block:
            continue
        n = len(block.get('accessionNumber', []))
        for i in range(n):
            acc = block['accessionNumber'][i]
            if acc in seen:
                continue
            seen.add(acc)
            items = [x.strip() for x in (block.get('items', [''] * n)[i] or '').split(',') if x.strip()]
            out.append({'filed': block['filingDate'][i], 'form': block['form'][i], 'items': items, 'accession': acc,
                        'report_date': (block.get('reportDate') or [None] * n)[i]})
    out.sort(key=lambda r: (r['filed'], r['accession']), reverse=True)
    return out


def count_events(events, as_of, window_days, items):
    """8-K / 8-K/A filings with as_of - window_days < filed <= as_of whose items include any of `items`."""
    lo = _shift(as_of, -window_days)
    hits = [e for e in events if e['form'] in EVENT_FORMS and lo < e['filed'] <= as_of and set(e['items']) & set(items)]
    return {'count': len(hits), 'amendments': sum(1 for e in hits if e['form'] == '8-K/A'),
            'window': [_shift(lo, 1), as_of], 'filings': [{k: e[k] for k in ('filed', 'form', 'items', 'accession')} for e in hits]}


def f4_value(events, as_of, p, coverage_from):
    """f4 now and a year earlier. coverage_from: oldest filing date in the loaded submissions (None = no filings)."""
    win, items, extra = p['event_window_days'], p['event_items'], p.get('event_items_unscored', [])
    if not any(e['form'] in EVENT_FORMS for e in events):
        return None, {'reason': 'no 8-K filings in the submissions history (a foreign private issuer files 6-K, which '
                                'has no item codes): f4 is missing for this issuer, not zero'}
    if coverage_from is None or coverage_from > _shift(as_of, -win):
        return None, {'reason': f'submissions history starts {coverage_from}, after the {win}-day window opens'}
    now = count_events(events, as_of, win, items)
    det = {'count': now['count'], 'amendments': now['amendments'], 'window': now['window'], 'filings': now['filings'],
           'items_scored': items}
    for it in extra:
        c = count_events(events, as_of, win, [it])
        det[f'unscored_item_{it}'] = {'count': c['count'], 'note': 'shown, not scored'}
    prior_d = _shift(as_of, -p['change_days'])
    if coverage_from <= _shift(prior_d, -win):
        det['prior'] = {'as_of': prior_d, 'count': count_events(events, prior_d, win, items)['count']}
    else:
        det['prior'] = None
        det['prior_reason'] = f'submissions history starts {coverage_from}, too late for the window at {prior_d}'
    last = now['filings'][0] if now['filings'] else None
    det.update(filed=last['filed'] if last else None, accession=last['accession'] if last else None)
    return now['count'], det


# --- scoring ----------------------------------------------------------------------------------------------------------

def _combine(za, zc, lw):
    parts = [(lw, za)] + ([(1 - lw, zc)] if zc is not None else [])
    share = sum(w for w, _ in parts)
    return sum(w * z for w, z in parts) / share, share


def score_metric(metric, value, prior, mp, lw, period_end, as_of, stale):
    """dict with anchor_z, change_z, z, part_share, staleness weight, weight."""
    a, s = mp['anchor'], mp['scale']
    za = engine.anchor_z(value, a, s)
    zc = None if prior is None else (value - prior) / s
    z, share = _combine(za, zc, lw)
    if period_end is None:
        age, sw = None, 1.0
    else:
        age = _days(period_end, as_of)
        sw = engine.staleness_weight(age, stale['full_weight_days'], stale['zero_weight_days'])
    return {'anchor': a, 'scale': s, 'anchor_z': za, 'change_z': zc, 'z': z, 'part_share': share, 'age_days': age,
            'staleness_weight': sw, 'weight': share * sw, 'status': 'live' if share * sw > 0 else 'stale'}


def issuer_snapshot(iss, F, events, cov_from, as_of, method):
    p, eng = method['layers']['L4'], method['engine']
    lw, stale = p['level_weight'], eng['staleness']
    metrics = {}
    for m in ('f1', 'f2'):
        if m not in p['metrics']:
            continue
        if F is None:
            metrics[m] = {'status': 'missing', 'reason': 'companyfacts not available'}
            continue
        end, v, det, misses = latest_value(F, m, as_of)
        if end is None:
            metrics[m] = {'status': 'missing', 'reason': 'no period end with every input tagged',
                          'tried': misses[:4], 'z': None, 'weight': 0.0}
            continue
        pe, pv, pdet = prior_value(F, m, end, as_of, p['change_days'], p['change_tolerance_days'])
        sc = score_metric(m, v, pv, p['metrics'][m], lw, end, as_of, stale)
        rec = {'value': v, 'period_end': end, 'filed': det.pop('filed'), 'accession': det.pop('accession'),
               'form': det.pop('form'), 'inputs': det}
        rec['filing_url'] = _filing_url(iss['cik'], rec['accession'])
        rec['change'] = ({'prior_period_end': pe, 'prior_value': pv, 'delta': v - pv,
                          'prior_filed': pdet.get('filed'), 'prior_accession': pdet.get('accession')}
                         if pe else {'prior_period_end': None, 'reason': pdet})
        if misses:
            rec['newer_periods_skipped'] = misses[:4]
        rec.update(sc)
        metrics[m] = rec
    metrics['f3'] = {'status': 'not_implemented', 'reason': NOT_IMPLEMENTED_F3, 'z': None, 'weight': 0.0}
    if 'f4' in p['metrics']:
        v, det = f4_value(events, as_of, p, cov_from) if events is not None else (None, {'reason': 'submissions not available'})
        if v is None:
            metrics['f4'] = dict(det, status='missing', z=None, weight=0.0)
        else:
            prior = det['prior']['count'] if det.get('prior') else None
            sc = score_metric('f4', v, prior, p['metrics']['f4'], lw, None, as_of, stale)
            metrics['f4'] = dict(value=v, period_end=None, as_of=as_of, filed=det.pop('filed'), accession=det.pop('accession'),
                                 inputs=det, **sc)
            if metrics['f4']['accession']:
                metrics['f4']['filing_url'] = _filing_url(iss['cik'], metrics['f4']['accession'])
    scored = [m for m in SCORED if m in p['metrics']]
    live = [m for m in scored if metrics[m].get('z') is not None and metrics[m].get('weight', 0) > 0]
    score = statistics.fmean(metrics[m]['z'] for m in live) if live else None
    weight = sum(metrics[m]['weight'] for m in live) / len(scored) if scored else 0.0
    coverage = {m: metrics[m].get('status', 'live') if m not in live else 'live' for m in list(scored) + ['f3']}
    return {'issuer': iss['id'], 'cik': iss['cik'], 'group': iss['group'], 'entity_name': F.entity if F else None,
            'score': score, 'weight': weight, 'metrics_live': live, 'coverage': coverage, 'metrics': metrics}


def _filing_url(cik, accn):
    if not accn:
        return None
    return FILING_URL.format(cik=int(cik), acc=accn.replace('-', ''))


def layer_point(issuers, as_of, method):
    """Layer Z on one date from issuer snapshots (dict id -> snapshot)."""
    eng = method['engine']
    cut, tau = config_cuts(method), eng['tau']
    n = len(issuers)
    groups = {}
    for s in issuers.values():
        groups.setdefault(s['group'], []).append(s)
    gz = {}
    for g, ss in groups.items():
        live = [s for s in ss if s['score'] is not None and s['weight'] > 0]
        if not live:
            continue
        wt = sum(s['weight'] for s in live)
        z = sum(s['weight'] * s['score'] for s in live) / wt
        lv = engine.logistic(z, tau)
        gz[g] = {'issuers': [s['issuer'] for s in live], 'z': z, 'base_weight': wt / len(ss), 'level': lv,
                 'band': engine.BANDS[engine.raw_band(lv, cut)]}
    live_share = sum(s['weight'] for s in issuers.values()) / n if n else 0.0
    out = {'as_of': as_of, 'groups': gz, 'live_share': live_share, 'min_live_share': eng['min_live_share']}
    if not gz or live_share + 1e-12 < eng['min_live_share']:
        return dict(out, status='insufficient_live_weight', Z=None, level=None, cap=None, cap_reason=None,
                    groups_stressed=[], weights=None,
                    reason=f'live issuer weight {live_share:.0%} of target, needs {eng["min_live_share"]:.0%}')
    capw = engine.cap_weights({g: v['base_weight'] for g, v in gz.items()}, eng['group_cap'])
    Z = sum(capw['weights'][g] * gz[g]['z'] for g in gz)
    capb, why, hot = engine.two_group_cap({g: v['level'] for g, v in gz.items()}, cut, eng['min_groups_for_stressed'])
    return dict(out, status='ok', Z=Z, level=engine.logistic(Z, tau), cap=capb, cap_reason=why, groups_stressed=hot,
                weights=capw)


# --- inputs ---------------------------------------------------------------------------------------------------------

def _json_ok(kind):
    def ok(b):
        j = json.loads(b)
        if kind == 'companyfacts':
            return isinstance(j, dict) and isinstance(j.get('facts'), dict)
        if kind == 'submissions':
            return isinstance(j, dict) and isinstance(j.get('filings'), dict)
        return isinstance(j, dict) and isinstance(j.get('accessionNumber'), list)
    return ok


def _latest_cached(sec_dir, kind, name, validate):
    snaps = sorted((p for p in sec_dir.glob(f'*/{kind}/{name}') if p.is_file()), key=lambda p: p.parts[-3], reverse=True)
    for p in snaps:
        try:
            if validate(p.read_bytes()):
                return p
        except Exception:  # noqa: BLE001 - an unreadable cache file is skipped
            continue
    return None


def load_json(kind, name, url, ctx, p):
    """(payload, record) for one SEC file. Online: today's snapshot (downloaded once per UTC day). Offline or when a
    download fails: the newest valid cached snapshot, labelled. record: path, sha256, snapshot, fetch status, url."""
    root = pathlib.Path(ctx.get('root') or ROOT)
    raw = root / 'data' / 'raw'
    sec = raw / 'sec'
    validate = _json_ok(kind)
    err = None
    if not ctx.get('offline'):
        ua = os.environ.get(UA_ENV, '').strip()
        if not ua:
            raise SecUserAgentMissing(f'{UA_ENV} is not set: SEC downloads need a descriptive User-Agent with a contact, '
                                      f'e.g. {UA_ENV}="<organisation> <contact address>" (or build with --offline from the cache)')
        today = ctx.get('today') or dt.datetime.now(dt.timezone.utc).date().isoformat()
        r = fetch_get(url, 'sec', f'{today}/{kind}/{name}', validate=validate, root=raw, headers={'User-Agent': ua},
                      min_interval=1.0 / p.get('max_requests_per_second', 8), opener=ctx.get('opener'))
        if r['ok']:
            return json.loads(r.data), {'kind': kind, 'url': url, 'path': r['path'], 'snapshot': today, 'fetch': r['status']}
        err = r.get('error')
    path = _latest_cached(sec, kind, name, validate)
    if path is None:
        return None, {'kind': kind, 'url': url, 'path': None, 'snapshot': None, 'fetch': 'missing',
                      'error': err or 'offline and no cached snapshot'}
    return json.loads(path.read_bytes()), {'kind': kind, 'url': url, 'path': str(path), 'snapshot': path.parts[-3],
                                           'fetch': 'cached_snapshot' + (f' (download failed: {err})' if err else '')}


def issuers_from(method):
    """[{'id', 'cik', 'group'}] from layers.L4 (ciks and issuer_groups), with consistency errors."""
    p = method['layers']['L4']
    owner, errs = {}, []
    for g, ids in p['issuer_groups'].items():
        for i in ids:
            if i in owner:
                errs.append(f'layers.L4.issuer_groups: {i} is in both {owner[i]} and {g}')
            owner[i] = g
    for i in p['ciks']:
        if i not in owner:
            errs.append(f'layers.L4.ciks: {i} has no group in layers.L4.issuer_groups')
    for i in owner:
        if i not in p['ciks']:
            errs.append(f'layers.L4.issuer_groups: {i} has no CIK in layers.L4.ciks')
    return [{'id': i, 'cik': int(c), 'group': owner.get(i)} for i, c in p['ciks'].items()], errs


def load_inputs(issuers, ctx, p):
    """Per issuer: Facts (or None), events (or None), oldest filing date, records. Raises SecUserAgentMissing."""
    data, records = {}, []
    hist = p['history_start']
    for iss in issuers:
        name = f'CIK{iss["cik"]:010d}.json'
        cf, rcf = load_json('companyfacts', name, CF_URL.format(cik=iss['cik']), ctx, p)
        sub, rsub = load_json('submissions', name, SUB_URL.format(cik=iss['cik']), ctx, p)
        recs = [rcf, rsub]
        subs = [sub] if sub else []
        if sub:
            recent = sub['filings'].get('recent') or {}
            oldest = min(recent.get('filingDate') or ['9999'])
            need = _shift(hist, -(p['change_days'] + p['event_window_days']))
            for f in sub['filings'].get('files') or []:
                if oldest > need and f.get('filingTo', '') >= need:
                    page, rp = load_json('submissions_pages', f['name'], SUB_PAGE_URL.format(name=f['name']), ctx, p)
                    recs.append(rp)
                    if page:
                        subs.append(page)
        events = load_events(subs) if subs else None
        cov = min((e['filed'] for e in events), default=None) if events else None
        data[iss['id']] = {'facts': Facts(cf) if cf else None, 'events': events, 'coverage_from': cov, 'records': recs}
        records += recs
    return data, records


# --- the layer -------------------------------------------------------------------------------------------------------

def release_dates(data, start, as_of, p):
    """Dates with new L4 information: a filing date of any fact the layer reads, or of a scored 8-K."""
    out = set()
    for d in data.values():
        F = d['facts']
        if F:
            out |= {x for x in F.filed_dates if start <= x <= as_of}
        for e in d['events'] or []:
            if e['form'] in EVENT_FORMS and set(e['items']) & set(p['event_items']) and start <= e['filed'] <= as_of:
                out.add(e['filed'])
    return sorted(out)


def snapshot_all(data, issuers, as_of, method):
    return {i['id']: issuer_snapshot(i, data[i['id']]['facts'], data[i['id']]['events'], data[i['id']]['coverage_from'],
                                     as_of, method) for i in issuers}


def layer_series(data, issuers, as_of, method):
    """Points on every release date from history_start to as_of (fresh), plus as_of itself (carried) when it is not a
    release date. Band replayed with senior hysteresis."""
    p, eng = method['layers']['L4'], method['engine']
    dates = release_dates(data, p['history_start'], as_of, p)
    releases = set(dates)
    if not dates or dates[-1] != as_of:
        dates.append(as_of)
    series = []
    for d in dates:
        pt = layer_point(snapshot_all(data, issuers, d, method), d, method)
        st = ('fresh' if d in releases else 'carried') if pt['status'] == 'ok' else pt['status']
        series.append(dict(pt, session=d, status=st))
    h = eng['hysteresis']
    bands = engine.replay_bands(series, 'senior', config_cuts(method), up_releases=h['senior_up_releases'],
                                down_margin=h['down_margin'])
    for pt, b in zip(series, bands):
        pt.update(band=b['band'], raw_band=b['raw_band'], band_moved=b['moved'], band_reason=b['reason'])
    return series


def _r(x, k=4):
    return None if x is None else round(x, k)


def _round_issuer(s):
    out = dict(s, score=_r(s['score'], 3), weight=_r(s['weight'], 4))
    ms = {}
    for m, r in s['metrics'].items():
        r = dict(r)
        for k in ('value', 'anchor_z', 'change_z', 'z', 'part_share', 'staleness_weight', 'weight'):
            if isinstance(r.get(k), float):
                r[k] = _r(r[k], 3 if k.endswith('z') else 4)
        if isinstance(r.get('change'), dict):
            c = dict(r['change'])
            for k in ('prior_value', 'delta'):
                if isinstance(c.get(k), float):
                    c[k] = _r(c[k])
            r['change'] = c
        ms[m] = r
    out['metrics'] = ms
    return out


def compute(as_of, method, ctx):
    """Build-facing entry point: (result, inputs). ctx: {'root', 'offline', optional 'today', 'opener'}."""
    p = method['layers']['L4']
    base = {'layer': 'L4', 'name': p['name'], 'kind': 'senior', 'as_of': as_of,
            'note': 'anchors, scales and windows are proposed examples, not calibrated'}

    def unavailable(reason, **kw):
        return dict(base, status='unavailable', reason=reason, available=None, Z=None, level=None, published=None,
                    band=None, _series=[], **kw), []

    if 'ciks' not in p:
        return unavailable('layers.L4 has no issuers configured (ciks, issuer_groups)')
    issuers, errs = issuers_from(method)
    if errs:
        return unavailable('; '.join(errs))
    try:
        data, records = load_inputs(issuers, ctx, p)
    except SecUserAgentMissing as e:
        return unavailable(str(e))
    root = pathlib.Path(ctx.get('root') or ROOT)
    inputs = []
    for r in records:
        if r.get('path'):
            fr = file_record(r['path'], root)
            inputs.append(dict(fr, url=r['url'], snapshot=r['snapshot']))
    snaps = sorted({r['snapshot'] for r in records if r.get('snapshot')})
    missing = [r['url'] for r in records if not r.get('path')]
    if all(d['facts'] is None and d['events'] is None for d in data.values()):
        return unavailable('no SEC payload available: ' + '; '.join(f"{r['url']}: {r.get('error')}" for r in records))
    series = layer_series(data, issuers, as_of, method)
    pt = series[-1]
    iss = snapshot_all(data, issuers, as_of, method)
    releases = release_dates(data, p['history_start'], as_of, p)
    last_release = releases[-1] if releases else None
    last_published = max((s['session'] for s in series if s['status'] == 'fresh'), default=None)
    groups = {g: {'issuers': v['issuers'], 'z': _r(v['z'], 3), 'level': _r(v['level'], 2), 'band': v['band'],
                  'base_weight': _r(v['base_weight'])} for g, v in pt['groups'].items()}
    attribution = {}
    if pt.get('weights'):
        w = pt['weights']
        for g, v in pt['groups'].items():
            attribution[g] = {'weight': _r(w['weights'][g]), 'contribution': _r(w['weights'][g] * v['z'], 3),
                              'uncapped_weight': _r(w['uncapped'][g]), 'uncapped_contribution': _r(w['uncapped'][g] * v['z'], 3)}
    status = pt['status']
    coverage = {s['issuer']: s['coverage'] for s in iss.values()}
    gaps = sorted(f'{s["issuer"]} {m}: {c}' for s in iss.values() for m, c in s['coverage'].items() if c != 'live' and m != 'f3')
    held = (f'held from the last publishable release ({last_published}): ' +
            ('no new release on the as-of date' if status == 'carried' else 'no publishable value on the as-of date') +
            ', so the band cannot move') if last_published else 'no publishable release yet'
    out = dict(base, status=status, available=last_release, last_published_release=last_published,
               Z=_r(pt['Z'], 3), level=_r(pt['level'], 2),
               published=engine.published(pt['level']), raw_band=pt['raw_band'], band=pt['band'],
               band_status='fresh' if status == 'fresh' else held,
               band_moved=pt['band_moved'], band_reason=pt['band_reason'],
               two_group={'cap': pt['cap'], 'reason': pt['cap_reason'], 'groups_stressed_or_worse': pt['groups_stressed']},
               cap_infeasible=bool(pt.get('weights') and pt['weights']['cap_infeasible']),
               live_share=_r(pt['live_share']), min_live_share=pt['min_live_share'],
               groups_live=len(pt['groups']), groups_configured=len({i['group'] for i in issuers}),
               releases_in_history=sum(1 for s in series if s['status'] == 'fresh'), history_start=p['history_start'],
               groups=groups, attribution=attribution, coverage=coverage, coverage_gaps=gaps,
               issuers={k: _round_issuer(v) for k, v in iss.items()},
               sources={'snapshots': snaps, 'missing': missing,
                        'fetch': {pathlib.Path(r['path']).name + ' ' + r['kind'] if r.get('path') else r['url']: r['fetch']
                                  for r in records}},
               stubs={'f3': NOT_IMPLEMENTED_F3},
               _series=[{'session': s['session'], 'Z': s['Z'], 'status': s['status']} for s in series])
    if snaps and snaps[-1] < as_of:
        out['snapshot_warning'] = (f'newest SEC snapshot is {snaps[-1]}, before the as-of date: filings after it are missing')
    if status != 'fresh' and pt.get('reason'):
        out['reason'] = pt['reason']
    return out, inputs


__all__ = ['Facts', 'quarter_values', 'ttm_value', 'debt_at', 'debt_due_24m', 'f1_at', 'f2_at', 'latest_value',
           'prior_value', 'load_events', 'count_events', 'f4_value', 'score_metric', 'issuer_snapshot', 'layer_point',
           'layer_series', 'load_json', 'compute']
