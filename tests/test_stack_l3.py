"""L3 private loan marks (stack/l3_bdc.py) on synthetic SEC BDC zips written to a temporary directory. No network, no
data/ needed."""
import copy, csv, io, json, pathlib, sys, zipfile
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import build, config, l3_bdc  # noqa: E402

BASE = yaml.safe_load(open(ROOT / 'stack' / 'stack-v0.yaml'))
SOI_COLS = ['adsh', 'cik', 'name', 'form', 'filed', 'period', 'ddate', 'inlineurl', 'Investment, Identifier Axis',
            'Investment, Issuer Name Axis', 'Investment, Name Axis', 'Investment Type Axis',
            'Investment Owned, Balance, Principal Amount', 'Investment Owned, Cost', 'Investment Owned, Fair Value',
            'Investment Owned, Net Assets, Percentage', 'Investment, Interest Rate, Paid in Kind', 'Investment Interest Rate',
            'Investment, Basis Spread, Variable Rate', 'Investment Maturity Date', 'Investment, Acquisition Date']
OLD_COLS = SOI_COLS + ['Adjusted cost basis', 'Initial fair value of Investment']
LOG_COLS = ['facility_key', 'borrower_group', 'holder', 'reviewer', 'reviewer_kind', 'date', 'decision', 'reason', 'evidence_url']


def method(**over):
    m = copy.deepcopy(BASE)
    m['layers']['L3']['months'] = ['2026_02', '2026_05', '2026_08']
    m['layers']['L3'].update(over)
    return m


def loan(text, principal, cost, fv, *, pik='', spread='0.045', maturity='2031-11-15', rate=''):
    return {'Investment, Identifier Axis': text, 'Investment Owned, Balance, Principal Amount': principal,
            'Investment Owned, Cost': cost, 'Investment Owned, Fair Value': fv, 'Investment, Interest Rate, Paid in Kind': pik,
            'Investment, Basis Spread, Variable Rate': spread, 'Investment Maturity Date': maturity,
            'Investment Interest Rate': rate}


def filing(cik, name, period, filed, adsh, rows, form='10-Q'):
    return {'cik': cik, 'name': name, 'period': period, 'filed': filed, 'adsh': adsh, 'form': form, 'rows': rows}


def write_zip(path, filings, old_labels=False, net_assets=None):
    """A monthly zip with soi.tsv, datasets/sub.tsv and datasets/num.tsv. old_labels writes cost and fair value under
    the labels the files before 2026_08 use, leaving the standard columns blank."""
    cols = OLD_COLS if old_labels else SOI_COLS
    soi = io.StringIO()
    w = csv.DictWriter(soi, fieldnames=cols, delimiter='\t', lineterminator='\n')
    w.writeheader()
    sub = io.StringIO()
    ws = csv.DictWriter(sub, fieldnames=['adsh', 'cik', 'name', 'form', 'period', 'filed'], delimiter='\t', lineterminator='\n')
    ws.writeheader()
    num = io.StringIO()
    wn = csv.DictWriter(num, fieldnames=['adsh', 'tag', 'version', 'ddate', 'qtrs', 'uom', 'segments', 'coreg', 'value'],
                        delimiter='\t', lineterminator='\n')
    wn.writeheader()
    for f in filings:
        ws.writerow({'adsh': f['adsh'], 'cik': f['cik'], 'name': f['name'], 'form': f['form'],
                     'period': f['period'].replace('-', ''), 'filed': f['filed'].replace('-', '')})
        if net_assets and f['cik'] in net_assets:
            wn.writerow({'adsh': f['adsh'], 'tag': 'AssetsNet', 'version': 'us-gaap/2024', 'ddate': f['period'].replace('-', ''),
                         'qtrs': '0', 'uom': 'USD', 'segments': '', 'coreg': '', 'value': net_assets[f['cik']]})
        for r in f['rows']:
            row = {'adsh': f['adsh'], 'cik': f['cik'], 'name': f['name'], 'form': f['form'], 'filed': f['filed'],
                   'period': f['period'], 'ddate': r.pop('ddate', f['period']), 'inlineurl': f'https://example.test/{f["adsh"]}.htm'}
            row.update(r)
            if old_labels:
                row['Adjusted cost basis'] = row.pop('Investment Owned, Cost', '')
                row['Initial fair value of Investment'] = row.pop('Investment Owned, Fair Value', '')
            w.writerow(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('soi.tsv', soi.getvalue())
        z.writestr('datasets/sub.tsv', sub.getvalue())
        z.writestr('datasets/num.tsv', num.getvalue())


def write_log(root, rows):
    p = root / 'research' / 'bdc' / 'review_log.csv'
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: '' for k in LOG_COLS}, **r})


def human_accept(group):
    return {'facility_key': 'x' + group, 'borrower_group': group, 'reviewer': 'a maintainer', 'reviewer_kind': 'human',
            'date': '2026-11-26', 'decision': 'accept', 'reason': 'group-level decision'}


def load(root, files):
    """rows_by_acc and filings from zips in root, review log applied."""
    rows_by_acc, filings = {}, []
    for name in files:
        rows, fl = l3_bdc.load_zip(root / 'data' / 'raw' / 'sec_bdc' / name)
        filings += fl
        for r in rows:
            rows_by_acc.setdefault(r['accession'], []).append(r)
    log = l3_bdc.load_review_log(root / 'research' / 'bdc' / 'review_log.csv')
    for v in rows_by_acc.values():
        l3_bdc.apply_review(v, log)
    return rows_by_acc, filings


CW = 'CoreWeave Compute Acquisition Co. IV, LLC | First Lien Debt | {} (S + 6.00%) | 5/16/2030 | {}% of net assets'


def two_quarters(tmp_path, q1_rows, q2_rows, old_labels_q1=True):
    raw = tmp_path / 'data' / 'raw' / 'sec_bdc'
    write_zip(raw / '2026_05_bdc.zip', [filing('111', 'ALPHA BDC', '2026-03-31', '2026-05-11', 'A-1', q1_rows)], old_labels=old_labels_q1)
    write_zip(raw / '2026_08_bdc.zip', [filing('111', 'ALPHA BDC', '2026-06-30', '2026-08-11', 'A-2', q2_rows)],
              net_assets={'111': '10000'})
    return ['2026_05_bdc.zip', '2026_08_bdc.zip']


# --- reading the files ------------------------------------------------------------------------------------------------

def test_old_column_labels_are_coalesced_into_the_standard_ones(tmp_path):
    files = two_quarters(tmp_path, [loan(CW.format('10.23%', '1.2'), '100', '98', '97')], [])
    rows, _ = l3_bdc.load_zip(tmp_path / 'data' / 'raw' / 'sec_bdc' / files[0])
    assert len(rows) == 1 and rows[0]['cost'] == 98.0 and rows[0]['fair_value'] == 97.0 and rows[0]['funded']
    assert rows[0]['filed'] == '2026-05-11' and rows[0]['period'] == '2026-03-31'


def test_an_empty_soi_file_gives_no_rows(tmp_path):
    p = tmp_path / '2026_06_bdc.zip'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('soi.tsv', '')
        z.writestr('datasets/sub.tsv', 'adsh\tcik\tname\tform\tperiod\tfiled\n')
    assert l3_bdc.load_zip(p) == ([], [])


# --- matching --------------------------------------------------------------------------------------------------------

def test_the_same_facility_is_matched_across_quarters_when_the_rate_and_share_text_change(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    files = two_quarters(tmp_path, [loan(CW.format('10.23%', '1.2'), '1000', '1000', '1000')],
                         [loan(CW.format('9.73%', '1.4'), '1000', '1000', '960')])
    rba, fl = load(tmp_path, files)
    pairs, tally = l3_bdc.pairs_at('2026-09-01', rba, fl, method()['layers']['L3'], BASE['engine'])
    assert len(pairs) == 1 and pairs[0]['tier'] == 'exact'
    assert pairs[0]['prior_fvc'] == 1.0 and pairs[0]['fvc'] == 0.96 and pairs[0]['change'] == pytest.approx(-0.04)
    assert tally['match_rate_facilities'] == 1.0


def test_a_reworded_facility_matches_on_its_terms_and_a_new_one_does_not_match(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    q1 = [loan('CoreWeave Compute Acquisition Co. IV, LLC, First lien', '1000', '1000', '1000')]
    q2 = [loan('Investment | Non-Affiliated Issuer | CoreWeave Compute Acquisition Co. IV LLC | Senior secured', '1000', '1000', '990'),
          loan('CoreWeave Financing DDTL V, LLC | Term Loan', '500', '500', '500', maturity='2031-11-06')]
    rba, fl = load(tmp_path, two_quarters(tmp_path, q1, q2))
    pairs, tally = l3_bdc.pairs_at('2026-09-01', rba, fl, method()['layers']['L3'], BASE['engine'])
    assert [p['tier'] for p in pairs] == ['terms']
    assert tally['facilities'] == 2 and tally['matched'] == 1 and tally['match_rate_weight'] == pytest.approx(1000 / 1500)


def test_unfunded_commitments_and_equity_are_never_matched_or_scored(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave'), human_accept('Nscale')])
    q1 = [loan(CW.format('10.23%', '1'), '1000', '1000', '1000'),
          loan('CoreWeave Compute Acquisition Co. IV, LLC | Delayed Draw Term Loan', '5000', '', '-50', spread='', maturity=''),
          loan('Nscale Global Holdings Limited | Preferred equity', '', '100', '100', spread='', maturity='')]
    q2 = [loan(CW.format('9.73%', '1'), '1000', '1000', '1000'),
          loan('CoreWeave Compute Acquisition Co. IV, LLC | Delayed Draw Term Loan', '5000', '0', '-400', spread='', maturity=''),
          loan('Nscale Global Holdings Limited | Preferred equity', '', '100', '250', spread='', maturity=''),
          loan('CoreWeave Compute Acquisition Co. IV, LLC | Unfunded commitment', '300', '10', '5', maturity='2030-05-16')]
    rba, fl = load(tmp_path, two_quarters(tmp_path, q1, q2))
    p = method()['layers']['L3']
    pairs, tally = l3_bdc.pairs_at('2026-09-01', rba, fl, p, BASE['engine'])
    assert len(pairs) == 1 and pairs[0]['change'] == 0.0           # the -400 unfunded mark and the 2.5x equity are out
    assert tally['excluded_unfunded'] == 2 and tally['excluded_equity_or_other'] == 1
    gm = l3_bdc.group_metrics(pairs, p)
    assert set(gm) == {'CoreWeave'} and gm['CoreWeave']['m1_markdown'] == 0.0


def test_a_facility_whose_principal_is_far_from_cost_is_listed_not_matched(tmp_path):
    # a partly drawn delayed-draw line: principal is the commitment, cost and FV mix drawn and undrawn parts
    write_log(tmp_path, [human_accept('Nscale')])
    q1 = [loan('NSCALE SERVICES UK LTD | First lien delayed draw term loan', '1600', '1000', '1000')]
    q2 = [loan('NSCALE SERVICES UK LTD | First lien delayed draw term loan', '1600', '1000', '1120')]
    rba, fl = load(tmp_path, two_quarters(tmp_path, q1, q2))
    pairs, tally = l3_bdc.pairs_at('2026-09-01', rba, fl, method()['layers']['L3'], BASE['engine'])
    assert pairs == [] and tally['excluded_principal_not_near_cost'] == 1


# --- metrics ---------------------------------------------------------------------------------------------------------

def test_breadth_is_the_principal_share_below_095_of_cost(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    a = 'CoreWeave Compute Acquisition Co. II, LLC | First Lien'
    b = 'CoreWeave Compute Acquisition Co. IV, LLC | First Lien'
    q1 = [loan(a, '3000', '3000', '3000', maturity='2028-07-31'), loan(b, '1000', '1000', '1000')]
    q2 = [loan(a, '3000', '3000', '2880', maturity='2028-07-31'), loan(b, '1000', '1000', '940')]   # 0.96 and 0.94
    rba, fl = load(tmp_path, two_quarters(tmp_path, q1, q2))
    p = method()['layers']['L3']
    gm = l3_bdc.group_metrics(l3_bdc.pairs_at('2026-09-01', rba, fl, p, BASE['engine'])[0], p)['CoreWeave']
    assert gm['m2_breadth'] == pytest.approx(0.25)
    assert gm['m1_markdown'] == pytest.approx((3000 * -0.04 + 1000 * -0.06) / 4000)
    assert gm['m0_level'] == pytest.approx((3000 * 0.96 + 1000 * 0.94) / 4000)


def test_only_pik_added_since_the_prior_vintage_scores(tmp_path):
    write_log(tmp_path, [human_accept('Crusoe')])
    a, b, c = ('Crusoe Energy Systems LLC | Term Loan A', 'Crusoe Energy Systems LLC | Term Loan B',
               'Crusoe Energy Systems LLC | Term Loan C')
    q1 = [loan(a, '1000', '1000', '1000', pik='0.02', maturity='2029-01-01'),     # PIK at origination
          loan(b, '1000', '1000', '1000', maturity='2029-02-01'),                 # PIK added later
          loan(c, '2000', '2000', '2000', maturity='2029-03-01')]                 # no PIK
    q2 = [loan(a, '1000', '1000', '1000', pik='0.02', maturity='2029-01-01'),
          loan(b, '1000', '1000', '1000', pik='0.03', maturity='2029-02-01'),
          loan(c, '2000', '2000', '2000', maturity='2029-03-01')]
    rba, fl = load(tmp_path, two_quarters(tmp_path, q1, q2))
    p = method()['layers']['L3']
    pairs = l3_bdc.pairs_at('2026-09-01', rba, fl, p, BASE['engine'])[0]
    assert sorted(x['pik_class'] for x in pairs) == ['added', 'none', 'origination']
    g = l3_bdc.score_groups(l3_bdc.group_metrics(pairs, p), p)['Crusoe']
    assert g['m3_any_pik'] == 0.5 and g['m3_origination_pik'] == 0.25 and g['m3_added_pik'] == 0.25
    a3 = p['anchors']['m3_added_pik']
    assert g['z_metrics']['m3_added_pik'] == pytest.approx((0.25 - a3['anchor']) / a3['scale'])
    assert g['m4_non_accrual'] is None and g['z_metrics']['m4_non_accrual'] is None


# --- point in time ---------------------------------------------------------------------------------------------------

def test_a_filing_after_the_as_of_date_is_invisible(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    files = two_quarters(tmp_path, [loan(CW.format('10.23%', '1'), '1000', '1000', '1000')],
                         [loan(CW.format('9.73%', '1'), '1000', '1000', '900')])
    rba, fl = load(tmp_path, files)
    m = method()
    before = l3_bdc.snapshot('2026-08-10', rba, fl, m)     # the 30 Jun 10-Q is filed on 11 Aug
    assert before['status'] == 'unavailable' or before['Z'] is None
    assert all(p['session'] <= '2026-08-10' for p in l3_bdc.layer_series(rba, fl, m, '2026-08-10'))
    after = l3_bdc.snapshot('2026-08-11', rba, fl, m)
    assert after['status'] == 'fresh' and after['available'] == '2026-08-11' and after['groups']['CoreWeave']['m1_markdown'] == -0.1
    # an amendment replaces the original only from its own filing date
    raw = tmp_path / 'data' / 'raw' / 'sec_bdc'
    write_zip(raw / '2026_10_bdc.zip', [filing('111', 'ALPHA BDC', '2026-06-30', '2026-10-05', 'A-3',
                                              [loan(CW.format('9.73%', '1'), '1000', '1000', '1000')], form='10-Q/A')])
    rba, fl = load(tmp_path, files + ['2026_10_bdc.zip'])
    assert l3_bdc.snapshot('2026-10-04', rba, fl, m)['groups']['CoreWeave']['m1_markdown'] == -0.1
    assert l3_bdc.snapshot('2026-10-05', rba, fl, m)['groups']['CoreWeave']['m1_markdown'] == 0.0


def test_a_carried_point_holds_the_value_and_goes_stale(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    files = two_quarters(tmp_path, [loan(CW.format('10.23%', '1'), '1000', '1000', '1000')],
                         [loan(CW.format('9.73%', '1'), '1000', '1000', '970')])
    rba, fl = load(tmp_path, files)
    m = method()
    fresh = l3_bdc.snapshot('2026-08-31', rba, fl, m)
    later = l3_bdc.snapshot('2026-11-25', rba, fl, m)       # 148 days after period end: weight 0.53
    assert fresh['status'] == 'fresh' and later['status'] == 'carried'
    assert later['Z'] == fresh['Z'] and later['available'] == '2026-08-11' and later['release'] == '2026-08-31'
    assert later['weight_factor'] == pytest.approx((180 - 148) / 60, abs=1e-4)
    assert later['band_status'].startswith('held')
    assert l3_bdc.snapshot('2026-12-31', rba, fl, m)['status'] == 'stale'   # 184 days


# --- review status ---------------------------------------------------------------------------------------------------

def test_rows_without_a_human_sign_off_are_listed_not_scored(tmp_path):
    write_log(tmp_path, [
        human_accept('CoreWeave'),
        {'facility_key': 'zz', 'borrower_group': 'Lambda', 'reviewer': 'an agent', 'reviewer_kind': 'agent',
         'date': '2026-11-26', 'decision': 'propose accept'},                    # an agent cannot sign off
    ])
    q = lambda fv: [loan(CW.format('9%', '1'), '1000', '1000', '1000'),
                    loan('Lambda Labs, Inc. | First Lien', '1000', '1000', fv, maturity='2029-01-01'),
                    loan('Ark Data Centers, LLC | First Lien', '1000', '1000', fv, maturity='2029-02-01')]  # keyword lead
    rba, fl = load(tmp_path, two_quarters(tmp_path, q('1000'), q('500')))
    status = {r['borrower_group']: r['review_status'] for v in rba.values() for r in v}
    assert status == {'CoreWeave': 'signed off', 'Lambda': 'candidate', '(keyword: data center)': 'candidate'}
    m = method()
    out = l3_bdc.snapshot('2026-09-01', rba, fl, m)
    assert set(out['groups']) == {'CoreWeave'} and out['match']['candidates_listed'] == 2
    # the comparison run scores the named candidate, never the keyword lead
    out2 = l3_bdc.snapshot('2026-09-01', rba, fl, method(score_rows='named_candidates'))
    assert set(out2['groups']) == {'CoreWeave', 'Lambda'} and 'warning' in out2


def test_a_human_rejection_removes_a_facility(tmp_path):
    files = two_quarters(tmp_path, [loan(CW.format('9%', '1'), '1000', '1000', '1000')],
                         [loan(CW.format('9%', '1'), '1000', '1000', '900')])
    rows, _ = l3_bdc.load_zip(tmp_path / 'data' / 'raw' / 'sec_bdc' / files[1])
    write_log(tmp_path, [human_accept('CoreWeave'), {'facility_key': rows[0]['facility_key'], 'borrower_group': 'CoreWeave',
                                                     'reviewer_kind': 'human', 'decision': 'reject'}])
    rba, fl = load(tmp_path, files)
    # bdc_exposure's facility_key (holder, text, maturity) is the same in both quarters when the text is unchanged
    assert [r['review_status'] for v in rba.values() for r in v] == ['rejected', 'rejected']
    assert l3_bdc.snapshot('2026-09-01', rba, fl, method())['groups'] == {}


# --- the layer: cap, 2-group rule, build, exposure map -----------------------------------------------------------------

def _groups_marked_down(tmp_path, drops):
    """One BDC, one facility per group, each marked from 1.00 to 1 - drop."""
    write_log(tmp_path, [human_accept(g) for g in drops])
    text = {'CoreWeave': 'CoreWeave Compute Acquisition Co. IV, LLC', 'Crusoe': 'Crusoe Energy Systems LLC',
            'Lambda': 'Lambda Labs, Inc.', 'Nebius': 'Nebius Group N.V.', 'Vultr': 'Vultr Holdings'}
    q1 = [loan(text[g] + ' | First Lien', '1000', '1000', '1000', maturity=f'2030-0{i + 1}-01') for i, g in enumerate(drops)]
    q2 = [loan(text[g] + ' | First Lien', '1000', '1000', str(1000 * (1 - d)), maturity=f'2030-0{i + 1}-01')
          for i, (g, d) in enumerate(drops.items())]
    return load(tmp_path, two_quarters(tmp_path, q1, q2))


def test_one_group_cannot_make_the_layer_stressed(tmp_path):
    rba, fl = _groups_marked_down(tmp_path, {'CoreWeave': 0.20, 'Crusoe': 0.20, 'Lambda': 0.0, 'Nebius': 0.0, 'Vultr': 0.0})
    out = l3_bdc.snapshot('2026-09-01', rba, fl, method())
    assert out['groups_live'] == 5 and not out['cap_infeasible']
    assert out['two_group']['groups_stressed_or_worse'] == ['CoreWeave', 'Crusoe'] and out['two_group']['cap'] is None
    rba, fl = _groups_marked_down(tmp_path / 'b', {'CoreWeave': 0.40, 'Crusoe': 0.0, 'Lambda': 0.0})
    out = l3_bdc.snapshot('2026-09-01', rba, fl, method())
    assert out['two_group']['cap'] == 'Watch' and out['two_group']['groups_stressed_or_worse'] == ['CoreWeave']
    assert out['cap_infeasible'] and out['band'] in ('Calm', 'Normal', 'Watch')
    assert all(a['weight'] == pytest.approx(1 / 3, abs=1e-4) for a in out['attribution'].values())


def test_build_runs_l3_offline_and_writes_the_exposure_map(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    two_quarters(tmp_path, [loan(CW.format('10%', '1'), '1000', '1000', '1000')],
                 [loan(CW.format('9%', '1'), '1000', '1000', '980'),
                  loan('Lambda Labs, Inc. | First Lien', '1000', '1000', '1000', maturity='2029-01-01')])
    out, inputs = build.run('2026-08-20', ['L3'], offline=True, root=tmp_path)
    l3 = out['layers']['L3']
    assert l3['status'] == 'fresh' and l3['scope'] == 'observed BDC book' and l3['Z'] is not None
    assert l3['metrics']['m4_non_accrual']['status'] == 'not_implemented'
    st = {m['month']: m['status'] for m in l3['months']}                  # the shipped month list
    assert st['2026_05'] == st['2026_08'] == 'cached' and st['2026_02'] == 'offline_missing'
    assert st['2026_09'] == 'after as_of: not read' and '2026_05' not in l3['missing_months']
    assert {i['path'] for i in inputs} >= {'data/raw/sec_bdc/2026_05_bdc.zip', 'research/bdc/review_log.csv'}
    lines = (tmp_path / 'data' / 'stack' / 'exposure_map.csv').read_text().splitlines()
    assert lines[0].startswith('# observed BDC book')
    rows = list(csv.DictReader(lines[1:]))
    assert all(r['scope'] == 'observed BDC book' for r in rows)
    assert not any(k in rows[0] for k in ('z', 'Z', 'level', 'band', 'score'))
    h = next(r for r in rows if r['table'] == 'holder')
    assert h['names'] == 'CoreWeave' and float(h['fair_value']) == 980 and float(h['ai_fv_pct_net_assets']) == 9.8 and float(h['net_assets']) == 10000
    assert [r['borrower_group'] for r in rows if r['table'] == 'borrower'] == ['CoreWeave']   # Lambda is unsigned
    man = list(csv.DictReader(open(tmp_path / 'manifests' / 'sec_bdc_sha256.csv')))
    assert [r['file'] for r in man] == ['2026_05_bdc.zip', '2026_08_bdc.zip'] and all(len(r['sha256']) == 64 for r in man)
    assert build.run('2026-08-20', ['L3'], offline=True, root=tmp_path) == (out, inputs)   # deterministic


def test_a_network_fetch_without_sec_user_agent_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)

    def opener(*a, **k):
        raise AssertionError('no request may be sent without a User-Agent')

    m = method(months=['2026_08'])
    res, inputs = l3_bdc.compute('2026-09-01', m, {'root': str(tmp_path), 'offline': False, 'opener': opener})
    assert res['status'] == 'unavailable' and 'SEC_USER_AGENT' in res['reason'] and inputs == []
    assert not (tmp_path / 'data' / 'raw' / 'sec_bdc' / '2026_08_bdc.zip').exists()


def test_a_download_is_cached_and_months_after_the_as_of_date_are_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', 'test-suite research@example.test')
    src = tmp_path / 'src.zip'
    write_zip(src, [filing('111', 'ALPHA BDC', '2026-06-30', '2026-08-11', 'A-2', [])])
    seen = []

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header('User-agent')))
        return Resp(src.read_bytes())

    m = method(months=['2026_08', '2026_10'])
    res, _ = l3_bdc.compute('2026-09-01', m, {'root': str(tmp_path / 'r'), 'offline': False, 'opener': opener})
    assert len(seen) == 1 and seen[0][0].endswith('2026_08_bdc.zip') and seen[0][1] == 'test-suite research@example.test'
    assert [x['status'] for x in res['months']] == ['downloaded', 'after as_of: not read']
    assert (tmp_path / 'r' / 'data' / 'raw' / 'sec_bdc' / '2026_08_bdc.zip').exists()


def test_the_shipped_l3_settings_are_valid_and_unknown_keys_are_rejected():
    assert config.validate(BASE) == []
    m = copy.deepcopy(BASE)
    m['layers']['L3']['fill_forward'] = True
    assert any('L3: unknown key' in e for e in config.validate(m))
    m = copy.deepcopy(BASE)
    m['layers']['L3']['anchors']['m1_markdown']['scale'] = 0
    assert any('m1_markdown.scale' in e for e in config.validate(m))
    m = copy.deepcopy(BASE)
    m['layers']['L3']['months'] = [202608]
    assert any('months' in e for e in config.validate(m))
    assert all(isinstance(x, str) for x in BASE['layers']['L3']['months'])


def test_the_register_lists_every_row_with_its_prior_match(tmp_path):
    write_log(tmp_path, [human_accept('CoreWeave')])
    rba, fl = load(tmp_path, two_quarters(tmp_path, [loan(CW.format('10%', '1'), '1000', '1000', '1000')],
                                          [loan(CW.format('9%', '1'), '1000', '1000', '990'),
                                           loan('CoreWeave DDTL | Delayed Draw', '500', '', '-5', spread='', maturity='')]))
    reg = l3_bdc.register_rows(rba, fl, '2026-09-01')
    assert sorted((r['period'], r['prior_match'], r['review_status']) for r in reg) == [
        ('2026-03-31', 'no prior vintage in the files', 'signed off'),
        ('2026-06-30', 'exact', 'signed off'),
        ('2026-06-30', 'unfunded: not matched', 'signed off')]
    q1, q2 = (next(r for r in reg if r['period'] == P and r['funded']) for P in ('2026-03-31', '2026-06-30'))
    assert q2['match_key'] == q1['match_key'] and q2['prior_fv_to_cost'] == 1.0
