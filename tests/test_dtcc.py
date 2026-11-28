"""Tests for the DTCC pipeline. Every known quirk of the data has a test here; an open one is marked xfail with its
issue number. Unit tests use synthetic rows; integration tests run on data/ when it has been built and skip otherwise."""
import csv, hashlib, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'harness'))
import dtcc  # noqa: E402

DAY = '2026-11-24'


def row(**kw):
    base = {
        'Action type': 'NEWT', 'Dissemination Identifier': '1', 'Original Dissemination Identifier': '',
        'Execution Timestamp': f'{DAY}T15:00:00Z', 'Expiration Date': '2031-12-20',  # on-the-run 5Y after 20 Sep 2026
        'Underlying Asset Name': 'ORACLE CORPORATION', 'UPI Underlier Name': '', 'Underlier ID-Leg 1': '6EC42L',
        'Fixed rate-Leg 1': '0.01', 'Spread-Leg 1': '', 'Notional amount-Leg 1': '10000000', 'Other payment amount': '',
        'Other payment type': 'UFRO', 'Event type': 'TRAD', 'Notional currency-Leg 1': 'USD', 'Other payment currency': 'USD',
        'UPI FISN': 'NA/CDS Corp SN Sr', 'Package indicator': 'FALSE', 'Non-standardized term indicator': 'FALSE',
        'Post-priced swap indicator': 'false',
    }
    base.update(kw)
    return base


def level(rows, prev=None):
    lv, _ = dtcc.parse_day(rows, DAY, prev or {})
    return lv.get('ORCL')


# --- quirks of the raw data ------------------------------------------------------------------------------------------

def test_quoted_spread_is_used():
    lv = level([row(**{'Spread-Leg 1': '0.0365'})])
    assert lv['official_bp'] == 365.0 and lv['official_kind'] == 'quoted'


def test_quoted_spread_in_raw_bp_is_read_as_bp():
    assert level([row(**{'Spread-Leg 1': '365'})])['official_bp'] == 365.0


def test_ambiguous_quoted_spread_is_ignored():
    assert level([row(**{'Spread-Leg 1': '2'})]) is None  # neither 2.0 as a decimal (20,000 bp) nor 2 bp is a price


def test_blank_upfront_is_missing_not_zero():
    # Number('') is 0 in JavaScript: this priced trades at their coupon on the live site until 26 Nov 2026.
    assert level([row()]) is None


def test_zero_spread_means_not_quoted():
    assert level([row(**{'Spread-Leg 1': '0'})]) is None


def test_implausible_quoted_spread_is_ignored():
    assert level([row(**{'Spread-Leg 1': '0.9'})]) is None  # 9,000 bp in the spread field is a price or a scaling error


def test_cancelled_trade_is_removed():
    rows = [row(**{'Spread-Leg 1': '0.0365'}), row(**{'Action type': 'EROR', 'Dissemination Identifier': '2', 'Original Dissemination Identifier': '1'})]
    assert level(rows) is None


def test_correction_replaces_original():
    rows = [row(**{'Spread-Leg 1': '0.0300'}),
            row(**{'Action type': 'CORR', 'Dissemination Identifier': '2', 'Original Dissemination Identifier': '1', 'Spread-Leg 1': '0.0365'})]
    lv = level(rows)
    assert lv['official_bp'] == 365.0 and lv['official_trades'] == 1


def test_off_the_run_maturity_is_ignored():
    assert level([row(**{'Spread-Leg 1': '0.0365', 'Expiration Date': '2030-12-20'})]) is None


def test_trade_executed_on_another_day_is_ignored():
    assert level([row(**{'Spread-Leg 1': '0.0365', 'Execution Timestamp': '2026-11-23T15:00:00Z'})]) is None


def test_loans_and_total_return_swaps_are_excluded():
    assert level([row(**{'Spread-Leg 1': '0.0365', 'Underlier ID-Leg 1': 'LX123456'})]) is None
    assert level([row(**{'Spread-Leg 1': '0.0365', 'UPI Underlier Name': 'ORACLE TRS'})]) is None


def test_upfront_converts_close_to_the_quote():
    # A 10.3% upfront on a 1% coupon prices at about 365 bp over five years (flat hazard, 40% recovery).
    rows = [row(**{'Spread-Leg 1': '0.0365'}),
            row(**{'Dissemination Identifier': '2', 'Other payment amount': '1030000'})]
    lv = level(rows)
    assert lv['official_trades'] == 2 and abs(lv['official_bp'] - 365) < 15 and lv['official_kind'] == 'mixed'
    assert lv['quoted_median_bp'] == 365.0 and abs(lv['upfront_implied_bp'] - 365) < 15


def test_quoted_and_implied_spreads_are_kept_apart():
    rows = [row(**{'Spread-Leg 1': '0.0400'}),
            row(**{'Dissemination Identifier': '2', 'Other payment amount': '1030000'})]
    lv = level(rows)
    assert lv['quoted_median_bp'] == 400.0 and lv['quoted_trades'] == 1 and lv['upfront_trades'] == 1
    assert abs(lv['upfront_implied_bp'] - 365) < 15


def test_upfront_only_day_is_flagged():
    lv = level([row(**{'Dissemination Identifier': '2', 'Other payment amount': '1030000'})], prev={'ORCL': 365.0})
    assert lv['official_kind'] == 'upfront_implied' and lv['quoted_median_bp'] is None


def test_unwind_payment_is_not_an_upfront():
    # UWIN is the payment to close a trade, not the price of a new one.
    assert level([row(**{'Other payment type': 'UWIN', 'Other payment amount': '1030000'})], prev={'ORCL': 365.0}) is None


def test_payment_without_a_type_is_not_an_upfront():
    assert level([row(**{'Other payment type': '', 'Other payment amount': '1030000'})], prev={'ORCL': 365.0}) is None


def test_capped_notional_is_counted():
    rows = [row(**{'Spread-Leg 1': '0.0365', 'Notional amount-Leg 1': '5,000,000+'})]
    assert level(rows)['n_capped'] == 1


def test_infeasible_upfront_reading_is_never_taken():
    # A 23% upfront on a 5% coupon: one sign is a real spread, the other hits the solver's floor. Found by the schema
    # check on CoreWeave, 6 Feb 2026.
    rows = [row(**{'Dissemination Identifier': '2', 'Fixed rate-Leg 1': '0.05', 'Other payment amount': '2300000'})]
    lv = level(rows, prev={'ORCL': 660.0})
    assert lv is None or lv['official_bp'] >= 10


def test_percent_coupon_is_normalised():
    # Issue #7: about 1 in 30 Oracle records write the 1% coupon as 1. Same trade, both spellings, same spread.
    pct = level([row(**{'Dissemination Identifier': '2', 'Fixed rate-Leg 1': '1', 'Other payment amount': '1030000'})], prev={'ORCL': 365.0})
    dec = level([row(**{'Dissemination Identifier': '2', 'Fixed rate-Leg 1': '0.01', 'Other payment amount': '1030000'})], prev={'ORCL': 365.0})
    assert pct is not None and abs(pct['official_bp'] - dec['official_bp']) < 0.05 and pct['n_percent_coupon_normalised'] == 1


def test_coupon_outside_the_standard_range_is_dropped():
    assert dtcc.coupon('25') == (None, True)      # 25% after normalisation: not a CDS coupon
    assert dtcc.coupon('0.05') == (0.05, False)
    assert dtcc.coupon('5') == (0.05, True)
    assert dtcc.coupon('') == (None, False)


def test_neither_spread_nor_upfront_prices_nothing():
    # P0.4: no coupon-priced spread from a row that carries no price at all.
    assert level([row(), row(**{'Dissemination Identifier': '2', 'Other payment type': ''})], prev={'ORCL': 365.0}) is None


# --- which trades may price a level (aistress.si/api/cds applies the same rules; parity in tests/test_site_parity.py) --

CRWV = {'Underlying Asset Name': 'COREWEAVE, INC.', 'Underlier ID-Leg 1': '239FAV', 'Fixed rate-Leg 1': '0.05'}


def crwv_level(rows, prev=None):
    lv, _ = dtcc.parse_day(rows, DAY, prev or {})
    return lv.get('CRWV')


@pytest.mark.parametrize('field,value', [
    ('Notional amount-Leg 1', '500,000'),                 # odd lot: under $1M prices nothing
    ('Notional currency-Leg 1', 'EUR'),                   # USD trades only
    ('Other payment currency', 'CHF'),
    ('Event type', 'NOVA'),                               # a novation transfers an old trade; it is not a price
    ('Package indicator', 'TRUE'),                        # one leg of a package is priced against the other legs
    ('Non-standardized term indicator', 'TRUE'),
    ('Post-priced swap indicator', 'true'),
    ('UPI FISN', 'NA/CDS Corp SN'),                       # no seniority: bond-referenced and short-dated contracts
    ('UPI FISN', 'NA/Cr Sw SN CDS Corp Ph'),              # physically settled
])
def test_only_standard_usd_senior_trades_of_1m_or_more_price(field, value):
    assert level([row(**{'Spread-Leg 1': '0.0365'})]) is not None
    assert level([row(**{'Spread-Leg 1': '0.0365', field: value})]) is None


def test_blank_event_type_is_a_trade():
    # Older DTCC files leave Event type blank; requiring TRAD dropped 94 of CoreWeave's 170 days.
    assert level([row(**{'Spread-Leg 1': '0.0365', 'Event type': ''})])['official_bp'] == 365.0


def test_one_dollar_quote_cannot_flip_the_sign():
    # Attack found on 28 Nov 2026: a $1 quote at 50 bp was the day's reference, and the nearest reading of every
    # CoreWeave upfront flipped to the wrong sign (1,373 bp read as 21 bp). An odd-lot quote is not eligible now.
    up = dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': '2309720'})   # 23.1% on $10M
    bad = dict(CRWV, **{'Dissemination Identifier': '3', 'Spread-Leg 1': '0.0050', 'Notional amount-Leg 1': '1'})
    lv = crwv_level([row(**up), row(**bad)], prev={'CRWV': 1138.6})
    assert lv['official_trades'] == 1 and 1300 < lv['official_bp'] < 1450


def test_impossible_reading_loses_to_any_reference():
    # On a 5% coupon a 23% upfront read the other way is a negative spread: the trade alone decides, so even a
    # wrong reference (an eligible quote at 50 bp) cannot flip it.
    up = dict(CRWV, **{'Dissemination Identifier': '2', 'Other payment amount': '2309720'})
    bad = dict(CRWV, **{'Dissemination Identifier': '3', 'Spread-Leg 1': '0.0050'})
    lv = crwv_level([row(**up), row(**bad)], prev={'CRWV': 1138.6})
    assert lv['upfront_trades'] == 1 and lv['upfront_implied_bp'] > 1300


def test_two_plausible_readings_take_the_one_nearer_the_reference():
    # A 2.2% upfront on a 1% coupon reads about 150 bp or about 50 bp; both are possible, so the prior level decides.
    rows = [row(**{'Dissemination Identifier': '2', 'Other payment amount': '220000'})]
    wide, tight = level(rows, prev={'ORCL': 150.0}), level(rows, prev={'ORCL': 60.0})
    assert wide['official_bp'] > 130 and tight['official_bp'] < 70 and wide['n_sign_from_reference'] == 1


def test_small_upfront_without_any_reference_is_dropped():
    lv = level([row(**{'Dissemination Identifier': '2', 'Other payment amount': '50000'})])
    assert lv is None


def test_non_standard_coupon_upfront_is_not_converted():
    assert level([row(**{'Dissemination Identifier': '2', 'Fixed rate-Leg 1': '0.03', 'Other payment amount': '1030000'})], prev={'ORCL': 365.0}) is None


META = {'Underlying Asset Name': 'META PLATFORMS, INC.', 'Underlier ID-Leg 1': 'UZEAAG'}


def test_basket_legs_sharing_one_upfront_across_names_do_not_price():
    # 21 Sep 2026: Oracle and Meta legs with the same 1.9746% upfront, seconds apart, priced Oracle at 145 bp while its
    # own trades said 205-208.
    o = row(**{'Dissemination Identifier': '2', 'Other payment amount': '197460', 'Execution Timestamp': f'{DAY}T15:00:00Z'})
    m = row(**dict(META, **{'Dissemination Identifier': '3', 'Other payment amount': '197460', 'Execution Timestamp': f'{DAY}T15:01:00Z'}))
    real = row(**{'Dissemination Identifier': '4', 'Other payment amount': '465890'})
    lv, _ = dtcc.parse_day([o, m, real], DAY, {'ORCL': 208.0, 'META': 90.0})
    assert lv['ORCL']['official_trades'] == 1 and lv['ORCL']['n_basket_legs'] == 1 and 'META' not in lv


def test_same_upfront_minutes_apart_or_on_one_name_is_not_a_basket():
    apart = [row(**{'Dissemination Identifier': '2', 'Other payment amount': '197460'}),
             row(**dict(META, **{'Dissemination Identifier': '3', 'Other payment amount': '197460', 'Execution Timestamp': f'{DAY}T15:10:00Z'}))]
    same = [row(**{'Dissemination Identifier': '2', 'Other payment amount': '197460'}),
            row(**{'Dissemination Identifier': '3', 'Other payment amount': '197460', 'Execution Timestamp': f'{DAY}T15:00:30Z'})]
    lv, _ = dtcc.parse_day(apart, DAY, {'ORCL': 150.0, 'META': 150.0})
    assert lv['ORCL']['n_basket_legs'] == 0 and 'META' in lv
    lv, _ = dtcc.parse_day(same, DAY, {'ORCL': 150.0})
    assert lv['ORCL']['official_trades'] == 2


def _file(day, rows):
    return (day, dtcc.slim(rows))


def test_cancellation_in_a_later_file_removes_the_trade():
    # 4 CoreWeave trades at 860 bp on 29 Sep 2026 were cancelled in a later file; the old per-file cleaning kept them.
    t = row(**{'Spread-Leg 1': '0.0365'})
    cancel = row(**{'Action type': 'EROR', 'Dissemination Identifier': '9', 'Original Dissemination Identifier': '1',
                    'Execution Timestamp': f'{DAY}T15:00:00Z'})
    by_day = dtcc.ledger([_file(DAY, [t]), _file('2026-11-25', [cancel])])
    assert not by_day.get(DAY)


def test_correction_in_a_later_file_replaces_the_trade():
    t = row(**{'Spread-Leg 1': '0.0300'})
    fix = row(**{'Action type': 'CORR', 'Dissemination Identifier': '9', 'Original Dissemination Identifier': '1', 'Spread-Leg 1': '0.0365'})
    by_day = dtcc.ledger([_file(DAY, [t]), _file('2026-11-25', [fix])])
    lv, _ = dtcc.parse_day(by_day[DAY], DAY, {}, cleaned=True)
    assert lv['ORCL']['official_bp'] == 365.0 and lv['ORCL']['official_trades'] == 1


def test_late_report_is_priced_on_its_execution_day():
    late = row(**{'Spread-Leg 1': '0.0365'})                     # executed 24 Nov, disseminated in the 25 Nov file
    by_day = dtcc.ledger([_file('2026-11-25', [late])])
    assert len(by_day[DAY]) == 1 and '2026-11-25' not in by_day


def test_weekend_execution_is_not_a_trading_day():
    t = row(**{'Spread-Leg 1': '0.0365', 'Execution Timestamp': '2026-11-28T15:00:00Z'})
    assert dtcc.ledger([_file('2026-11-30', [t])]) == {}


# --- downloads and provenance ----------------------------------------------------------------------------------------

def make_zip(text='a,b\n1,2\n'):
    import io, zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('SEC_CUMULATIVE_CREDITS.csv', text)
    return b.getvalue()


def test_failed_download_writes_nothing_and_is_retried(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dtcc, 'download', lambda day: calls.append(day) or None)
    assert dtcc.fetch('2025-01-02', tmp_path, False) is None
    assert not list(tmp_path.iterdir())
    good = make_zip()
    monkeypatch.setattr(dtcc, 'download', lambda day: good)
    assert dtcc.fetch('2025-01-02', tmp_path, False) == good and (tmp_path / '2025-01-02.zip').read_bytes() == good


def test_non_zip_body_is_not_kept(monkeypatch):
    class R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(dtcc.urllib.request, 'urlopen', lambda req, timeout: R(b'<html>rate limited</html>'))
    monkeypatch.setattr(dtcc.time, 'sleep', lambda s: None)
    assert dtcc.download('2025-01-02') is None


def test_empty_file_from_an_old_version_is_fetched_again(tmp_path, monkeypatch):
    (tmp_path / '2025-01-02.zip').write_bytes(b'')
    good = make_zip()
    monkeypatch.setattr(dtcc, 'download', lambda day: good)
    assert dtcc.fetch('2025-01-02', tmp_path, False) == good


def test_existing_file_is_never_overwritten(tmp_path, monkeypatch):
    good = make_zip()
    (tmp_path / '2025-01-02.zip').write_bytes(good)
    monkeypatch.setattr(dtcc, 'download', lambda day: make_zip('a,b\n9,9\n'))
    assert dtcc.fetch('2025-01-02', tmp_path, False) == good


def test_republished_file_is_a_logged_conflict(tmp_path, monkeypatch):
    import csv as _csv, hashlib as _h
    monkeypatch.setattr(dtcc, 'ROOT', tmp_path)
    raw = tmp_path / 'raw'; raw.mkdir()
    man, conf = tmp_path / 'm.csv', tmp_path / 'c.csv'
    orig, new = make_zip(), make_zip('a,b\n9,9\n')
    dtcc.append_rows(man, dtcc.MAN_FIELDS, [{'date': '2025-01-02', 'url': 'u', 'bytes': len(orig), 'sha256': _h.sha256(orig).hexdigest(), 'fetched_at': ''}])
    before = man.read_text()
    blobs = {'2025-01-02': new, '2025-01-03': orig}
    added, conflicts = dtcc.check_provenance(['2025-01-02', '2025-01-03'], blobs, raw, man, conf)
    assert len(conflicts) == 1 and blobs['2025-01-02'] is None          # left out of the tables
    assert man.read_text().startswith(before) and added == 1            # manifest only grows
    row = next(_csv.DictReader(open(conf)))
    assert row['new_sha256'] == _h.sha256(new).hexdigest() and (tmp_path / row['path']).read_bytes() == new


# --- the built data (skips until harness/dtcc.py has run) -------------------------------------------------------------

CANON = ROOT / 'data' / 'canonical'
needs_data = pytest.mark.skipif(not (CANON / 'cds_daily.csv').exists(), reason='run python harness/dtcc.py first')


@needs_data
def test_canonical_tables_match_their_schemas():
    import validate
    errs = validate.check(ROOT / 'schema' / 'cds_trades.yaml') + validate.check(ROOT / 'schema' / 'cds_daily.yaml')
    assert not errs, errs[:10]


@needs_data
def test_raw_files_match_the_committed_manifest():
    man = {r['date']: r['sha256'] for r in csv.DictReader(open(ROOT / 'manifests' / 'dtcc_sha256.csv'))}
    raw = ROOT / 'data' / 'raw' / 'dtcc'
    checked = 0
    for d, h in man.items():
        p = raw / f'{d}.zip'
        if p.exists() and p.stat().st_size:
            assert hashlib.sha256(p.read_bytes()).hexdigest() == h, d
            checked += 1
    assert checked > 0


@needs_data
@pytest.mark.parametrize('date,entity,bp', [('2026-11-24', 'ORCL', 364.5), ('2026-11-25', 'ORCL', 373.2), ('2026-11-24', 'CRWV', 1372.7),
                                            ('2026-11-25', 'META', 124.2)])
def test_levels_match_the_published_site(date, entity, bp):
    rows = {(r['date'], r['entity']): float(r['official_bp']) for r in csv.DictReader(open(CANON / 'cds_daily.csv'))}
    if (date, entity) not in rows:
        pytest.skip('date outside the built window')
    assert abs(rows[(date, entity)] - bp) <= 1.0


@needs_data
@pytest.mark.parametrize('date,old_bug', [('2025-11-03', 102.9), ('2026-02-24', 134.8), ('2026-04-24', 102.3), ('2026-05-11', 54.2),
                                          ('2026-05-12', 54.9), ('2026-07-28', 212.5), ('2026-10-28', 265.4)])
def test_blank_upfront_days_never_show_the_old_reading(date, old_bug):
    # research/findings/2026-11-26-blank-upfronts.md: the site once priced blank upfronts at the coupon.
    rows = {r['date']: float(r['official_bp']) for r in csv.DictReader(open(CANON / 'cds_daily.csv')) if r['entity'] == 'ORCL'}
    if date not in rows:
        pytest.skip('date outside the built window')
    trades = [r for r in csv.DictReader(open(CANON / 'cds_trades.csv')) if r['date'] == date and r['entity'] == 'ORCL']
    assert trades and all(r['kind'] == 'quoted' or r['upfront_pct'] != '' for r in trades)  # nothing priced from a blank
    # The level differs from the old reading too, except on 28 Oct 2026: with the $95k odd lot at 293 bp out (28 Nov),
    # the correct median is 265.4, the old reading's value by coincidence.
    assert abs(rows[date] - old_bug) > 1.0 or date == '2026-10-28'


@needs_data
def test_coreweave_is_labelled_as_model_implied():
    kinds = {r['official_kind'] for r in csv.DictReader(open(CANON / 'cds_daily.csv')) if r['entity'] == 'CRWV'}
    assert kinds <= {'upfront_implied', 'mixed', 'quoted'} and kinds  # whatever it is, it is stated


def test_no_unresolved_raw_file_conflicts():
    p = ROOT / 'manifests' / 'dtcc_conflicts.csv'
    assert not p.exists() or len(p.read_text().strip().splitlines()) <= 1, 'DTCC republished a file: a human must decide'
