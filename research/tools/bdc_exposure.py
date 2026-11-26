"""BDC exposure map, facts only (AGENT-BRIEF P3.1). From the SEC's BDC Data Sets: which listed and non-traded business
development companies hold loans or equity of AI-infrastructure borrowers, at what cost and fair value, per filing.

  python research/tools/bdc_exposure.py 2026_08 2026_09 2026_10 [--cache data/raw/sec_bdc]

Writes:
  research/bdc/register.csv      every candidate row with its match rule, funded flag and review status
  research/bdc/holders.csv       per BDC: matched fair value, net assets, share of net assets
  research/bdc/borrowers.csv     per borrower group: holders, funded principal, cost, fair value
  manifests/sec_bdc_sha256.csv   sha256 of each zip used (append-only)

Rules:
  - current-period marks only (ddate = period); each BDC's latest period is used
  - roll-up rows ("... [Member]") are dropped: they repeat the facility rows below them
  - a row is funded if its cost or fair value is positive; unfunded commitments are listed, never summed
  - a named-obligor match (CoreWeave, Crusoe, ...) is a candidate until a human signs it off in review_log.csv;
    a keyword match ("data center", "GPU") is a lead, never counted
  - no stress score on any named private company: this table is marks as filed
"""
import argparse, csv, datetime as dt, hashlib, io, json, pathlib, re, sys, urllib.request, zipfile

csv.field_size_limit(10 ** 9)
ROOT = pathlib.Path(__file__).resolve().parents[2]
URL = 'https://www.sec.gov/files/datastandardsinnovation/data/business-development-company-bdc-data-sets/{}_bdc.zip'
UA = 'acsi-lab research (aistress.si/lab)'
GROUPS = [  # (pattern, borrower group, category). Named obligors first; keyword leads last.
    (r'core\s*weave', 'CoreWeave', 'neocloud'), (r'crusoe', 'Crusoe', 'neocloud'), (r'lambda\s*(labs|,? inc|cloud)', 'Lambda', 'neocloud'),
    (r'nebius', 'Nebius', 'neocloud'), (r'fluidstack', 'Fluidstack', 'neocloud'), (r'voltage\s*park', 'Voltage Park', 'neocloud'),
    (r'vultr|constant\s*company', 'Vultr', 'neocloud'), (r'tensorwave', 'TensorWave', 'neocloud'), (r'nscale', 'Nscale', 'neocloud'),
    (r'together\s*(ai|computer)', 'Together AI', 'neocloud'), (r'firmus', 'Firmus', 'neocloud'), (r'denvr', 'Denvr', 'neocloud'),
    (r'applied\s*digital', 'Applied Digital', 'ai-hpc'), (r'core\s*scientific', 'Core Scientific', 'ai-hpc'),
    (r'\biren\b|iris\s*energy', 'IREN', 'ai-hpc'), (r'cipher\s*mining', 'Cipher Mining', 'ai-hpc'), (r'terawulf|lake\s*mariner', 'TeraWulf', 'ai-hpc'),
    (r'\bhut\s*8\b', 'Hut 8', 'ai-hpc'), (r'galaxy\s*(digital|helios)', 'Galaxy Digital', 'ai-hpc'), (r'bitdeer', 'Bitdeer', 'ai-hpc'),
    (r'vantage\s*data', 'Vantage Data Centers', 'data-center'), (r'aligned\s*data', 'Aligned Data Centers', 'data-center'),
    (r'\bqts\b', 'QTS', 'data-center'), (r'stack\s*infrastructure', 'Stack Infrastructure', 'data-center'), (r'\bswitch,?\s*(ltd|inc)', 'Switch', 'data-center'),
    (r'cyrusone', 'CyrusOne', 'data-center'), (r'databank', 'DataBank', 'data-center'), (r'flexential', 'Flexential', 'data-center'),
    (r'edgeconnex', 'EdgeConneX', 'data-center'), (r'compass\s*datacenters?', 'Compass Datacenters', 'data-center'), (r'yondr', 'Yondr', 'data-center'),
    (r'cologix', 'Cologix', 'data-center'), (r'tierpoint', 'TierPoint', 'data-center'), (r'evoque', 'Evoque', 'data-center'),
    (r'element\s*critical', 'Element Critical', 'data-center'),
    (r'data\s*cent(er|re)s?\b|datacent(er|re)', '(keyword: data center)', 'keyword'), (r'\bgpus?\b', '(keyword: GPU)', 'keyword'),
    (r'hyperscale', '(keyword: hyperscale)', 'keyword'),
]
RX = [(re.compile(p, re.I), g, c) for p, g, c in GROUPS]
F = {'principal': 'Investment Owned, Balance, Principal Amount', 'cost': 'Investment Owned, Cost', 'fair_value': 'Investment Owned, Fair Value',
     'pct_net_assets': 'Investment Owned, Net Assets, Percentage', 'pik_rate': 'Investment, Interest Rate, Paid in Kind',
     'rate': 'Investment Interest Rate', 'spread': 'Investment, Basis Spread, Variable Rate', 'maturity': 'Investment Maturity Date',
     'acquired': 'Investment, Acquisition Date', 'instrument': 'Investment Type Axis'}
REG_FIELDS = ['facility_key', 'holder', 'holder_cik', 'borrower_group', 'category', 'match_rule', 'obligor_text', 'instrument', 'funded',
              'principal', 'principal_ok', 'cost', 'fair_value', 'fv_to_cost', 'pct_net_assets', 'rate', 'spread', 'pik_rate', 'maturity', 'acquired',
              'mark_date', 'form', 'filed', 'filing_url', 'accession', 'source_file', 'status']


def num(v):
    try:
        return float(v) if str(v).strip() else None
    except ValueError:
        return None


def get_zip(month, cache, man_path):
    p = cache / f'{month}_bdc.zip'
    if not p.exists():
        req = urllib.request.Request(URL.format(month), headers={'User-Agent': UA})
        b = urllib.request.urlopen(req, timeout=300).read()
        if b[:2] != b'PK':
            sys.exit(f'{month}: not a ZIP')
        p.write_bytes(b)
    b = p.read_bytes()
    h = hashlib.sha256(b).hexdigest()
    seen = {r['file'] for r in csv.DictReader(open(man_path))} if man_path.exists() else set()
    if p.name not in seen:
        new = not man_path.exists()
        with open(man_path, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['file', 'url', 'bytes', 'sha256', 'fetched_at'])
            if new:
                w.writeheader()
            w.writerow({'file': p.name, 'url': URL.format(month), 'bytes': len(b), 'sha256': h,
                        'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')})
    return zipfile.ZipFile(io.BytesIO(b)), p.name


def net_assets(z):
    sub = {r['adsh']: r for r in csv.DictReader(io.TextIOWrapper(z.open('datasets/sub.tsv'), encoding='utf-8', errors='replace'), delimiter='\t')}
    out = {}
    r = csv.reader(io.TextIOWrapper(z.open('datasets/num.tsv'), encoding='utf-8', errors='replace'), delimiter='\t')
    ix = {k: i for i, k in enumerate(next(r))}
    for row in r:
        tag = row[ix['tag']]
        if tag not in ('AssetsNet', 'StockholdersEquity') or row[ix['qtrs']] != '0':
            continue
        if any(row[ix[c]].strip() for c in ('segments', 'coreg') if c in ix):  # whole-company value only
            continue
        s = sub.get(row[ix['adsh']])
        if not s or row[ix['ddate']] != s['period']:
            continue
        prev = out.get(s['cik'])
        if prev and prev['period'] >= s['period'] and (prev['tag'] == 'AssetsNet' or tag != 'AssetsNet'):
            continue
        out[s['cik']] = {'period': s['period'], 'net_assets': float(row[ix['value']]), 'tag': tag}
    return out


def scan(z, fname):
    r = csv.reader(io.TextIOWrapper(z.open('soi.tsv'), encoding='utf-8', errors='replace'), delimiter='\t')
    hdr = next(r); ix = {h: i for i, h in enumerate(hdr)}
    g = lambda row, c: row[ix[c]] if c in ix and ix[c] < len(row) else ''
    out = []
    for row in r:
        if g(row, 'ddate') != g(row, 'period'):
            continue
        text = ' | '.join(x for x in (g(row, 'Investment, Identifier Axis'), g(row, 'Investment, Issuer Name Axis'), g(row, 'Investment, Name Axis')) if x)
        if not text or text.rstrip().endswith('[Member]'):
            continue
        hit = next(((gr, c) for rx, gr, c in RX if c != 'keyword' and rx.search(text)), None) or next(((gr, c) for rx, gr, c in RX if c == 'keyword' and rx.search(text)), None)
        if not hit:
            continue
        v = {k: num(g(row, c)) if k not in ('maturity', 'acquired', 'instrument') else g(row, c) for k, c in F.items()}
        funded = (v['cost'] or 0) > 0 or (v['fair_value'] or 0) > 0
        # Principal is sometimes the commitment, or tagged at the wrong scale (1,000x): trusted only near cost.
        principal_ok = bool(v['principal'] and v['cost'] and v['cost'] > 0 and 0.5 <= v['principal'] / v['cost'] <= 1.5)
        key = hashlib.sha1(f"{g(row, 'cik')}|{re.sub(r'[^a-z0-9]', '', text.lower())}|{v['maturity']}".encode()).hexdigest()[:12]
        out.append({
            'facility_key': key, 'holder': g(row, 'name'), 'holder_cik': g(row, 'cik'), 'borrower_group': hit[0], 'category': hit[1],
            'match_rule': 'keyword lead' if hit[1] == 'keyword' else 'named obligor', 'obligor_text': text[:300],
            'funded': funded, **v, 'principal_ok': principal_ok,
            'fv_to_cost': round(v['fair_value'] / v['cost'], 4) if funded and v['cost'] and v['cost'] > 0 and v['fair_value'] is not None else None,
            'mark_date': g(row, 'ddate'), 'form': g(row, 'form'), 'filed': g(row, 'filed'), 'filing_url': g(row, 'inlineurl'),
            'accession': g(row, 'adsh'), 'source_file': fname,
            'status': 'lead: not counted' if hit[1] == 'keyword' else 'candidate: pending human sign-off',
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('months', nargs='+'); ap.add_argument('--cache', default=str(ROOT / 'data' / 'raw' / 'sec_bdc'))
    a = ap.parse_args()
    cache = pathlib.Path(a.cache); cache.mkdir(parents=True, exist_ok=True)
    man = ROOT / 'manifests' / 'sec_bdc_sha256.csv'
    rows, nav = [], {}
    for m in a.months:
        z, name = get_zip(m, cache, man)
        rows += scan(z, name)
        for k, v in net_assets(z).items():
            if k not in nav or v['period'] > nav[k]['period']:
                nav[k] = v
    latest = {}
    for r in rows:
        latest[r['holder_cik']] = max(latest.get(r['holder_cik'], ''), r['mark_date'])
    rows = [r for r in rows if r['mark_date'] == latest[r['holder_cik']]]
    # Human sign-offs (research/bdc/review_log.csv) override the status; a rejection removes the row from the sums.
    log_p = ROOT / 'research' / 'bdc' / 'review_log.csv'
    decisions = {}
    if log_p.exists():
        for d in csv.DictReader(open(log_p)):
            if d.get('reviewer_kind') == 'human':
                decisions[d['facility_key']] = d['decision']
    for r in rows:
        if r['facility_key'] in decisions:
            r['status'] = 'signed off' if decisions[r['facility_key']] == 'accept' else 'rejected'
    out = ROOT / 'research' / 'bdc'; out.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (r['category'] == 'keyword', r['borrower_group'], r['holder'], -(r['fair_value'] or 0)))
    with open(out / 'register.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=REG_FIELDS, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    count = [r for r in rows if r['match_rule'] == 'named obligor' and r['funded'] and r['status'] != 'rejected']
    holders, borrowers = {}, {}
    for r in count:
        h = holders.setdefault(r['holder_cik'], {'holder': r['holder'], 'holder_cik': r['holder_cik'], 'mark_date': r['mark_date'], 'facilities': 0,
                                                 'cost': 0.0, 'fair_value': 0.0, 'groups': set(), 'filing_url': r['filing_url']})
        h['facilities'] += 1; h['cost'] += r['cost'] or 0; h['fair_value'] += r['fair_value'] or 0; h['groups'].add(r['borrower_group'])
        b = borrowers.setdefault(r['borrower_group'], {'borrower_group': r['borrower_group'], 'category': r['category'], 'holders': set(),
                                                       'facilities': 0, 'principal': 0.0, 'principal_flagged': 0, 'cost': 0.0, 'fair_value': 0.0})
        b['holders'].add(r['holder_cik']); b['facilities'] += 1
        b['principal'] += (r['principal'] or 0) if r['principal_ok'] else 0; b['principal_flagged'] += 0 if r['principal_ok'] else 1; b['cost'] += r['cost'] or 0; b['fair_value'] += r['fair_value'] or 0
    hl = []
    for h in holders.values():
        na = nav.get(h['holder_cik'])
        hl.append({**h, 'groups': '; '.join(sorted(h['groups'])), 'net_assets': na['net_assets'] if na else None,
                   'ai_fv_pct_nav': round(100 * h['fair_value'] / na['net_assets'], 2) if na and na['net_assets'] else None,
                   'fv_to_cost': round(h['fair_value'] / h['cost'], 4) if h['cost'] else None})
    hl.sort(key=lambda x: -(x['ai_fv_pct_nav'] or 0))
    with open(out / 'holders.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['holder', 'holder_cik', 'mark_date', 'facilities', 'groups', 'cost', 'fair_value', 'fv_to_cost',
                                          'net_assets', 'ai_fv_pct_nav', 'filing_url']); w.writeheader(); w.writerows(hl)
    bl = [{**b, 'holders': len(b['holders']), 'fv_to_cost': round(b['fair_value'] / b['cost'], 4) if b['cost'] else None} for b in borrowers.values()]
    bl.sort(key=lambda x: -x['fair_value'])
    with open(out / 'borrowers.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['borrower_group', 'category', 'holders', 'facilities', 'principal', 'principal_flagged', 'cost', 'fair_value', 'fv_to_cost'])
        w.writeheader(); w.writerows(bl)
    tot_c = sum(r['cost'] or 0 for r in count); tot_f = sum(r['fair_value'] or 0 for r in count)
    print(json.dumps({'rows': len(rows), 'counted': len(count), 'holders': len(hl), 'borrower_groups': len(bl), 'cost': tot_c, 'fair_value': tot_f,
                      'leads': sum(1 for r in rows if r['match_rule'] == 'keyword lead'),
                      'unfunded': sum(1 for r in rows if not r['funded'])}, indent=1))


if __name__ == '__main__':
    main()
