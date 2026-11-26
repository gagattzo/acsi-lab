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
    }
    base.update(kw)
    return base


def level(rows, prev=None):
    lv, _ = dtcc.parse_day(rows, DAY, prev or {})
    return lv.get('ORCL')


# --- quirks of the raw data ------------------------------------------------------------------------------------------

def test_quoted_spread_is_used():
    assert level([row(**{'Spread-Leg 1': '0.0365'})])['bp'] == 365.0


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
    assert lv['bp'] == 365.0 and lv['trades'] == 1


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
    assert lv['trades'] == 2 and abs(lv['bp'] - 365) < 15


def test_infeasible_upfront_reading_is_never_taken():
    # A 23% upfront on a 5% coupon: one sign is a real spread, the other hits the solver's floor. Found by the schema
    # check on CoreWeave, 6 Feb 2026.
    rows = [row(**{'Dissemination Identifier': '2', 'Fixed rate-Leg 1': '0.05', 'Other payment amount': '2300000'})]
    lv = level(rows, prev={'ORCL': 660.0})
    assert lv is None or lv['bp'] >= 10


@pytest.mark.xfail(reason='issue #7: coupons written in percent (1 = 1%) are read as 100% and the trade is dropped', strict=True)
def test_percent_coupon_is_normalised():
    rows = [row(**{'Dissemination Identifier': '2', 'Fixed rate-Leg 1': '1', 'Other payment amount': '1030000'})]
    lv = level(rows, prev={'ORCL': 365.0})
    assert lv is not None and abs(lv['bp'] - 365) < 15


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
@pytest.mark.parametrize('date,entity,bp', [('2026-11-24', 'ORCL', 364.5), ('2026-11-25', 'ORCL', 373.2), ('2026-11-24', 'CRWV', 1372.7)])
def test_levels_match_the_published_site(date, entity, bp):
    rows = {(r['date'], r['entity']): float(r['bp']) for r in csv.DictReader(open(CANON / 'cds_daily.csv'))}
    if (date, entity) not in rows:
        pytest.skip('date outside the built window')
    assert abs(rows[(date, entity)] - bp) <= 1.0
