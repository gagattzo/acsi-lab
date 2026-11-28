"""Canonical schema checks use synthetic rows and need no downloaded market data."""
import csv

import pytest
import yaml

from test_dtcc import ROOT, DAY, row, dtcc
import validate


SCHEMAS = [ROOT / 'schema' / 'cds_daily.yaml', ROOT / 'schema' / 'cds_trades.yaml']


def write_tables(directory, daily, trades):
    canonical = directory / 'data' / 'canonical'
    canonical.mkdir(parents=True, exist_ok=True)
    for name, fields, rows in [('cds_daily.csv', dtcc.DAILY_FIELDS, daily), ('cds_trades.csv', dtcc.TRADE_FIELDS, trades)]:
        with (canonical / name).open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def accepted_quote(quote):
    daily, trades = dtcc.parse_day([row(**{'Spread-Leg 1': quote})], DAY, {})
    return list(daily.values()), trades


def errors():
    return [error for schema in SCHEMAS for error in validate.check(schema)]


@pytest.mark.parametrize('quote,expected_bp', [('.0001', 1), ('.0000001', 0), ('20000', 20000)])
def test_schema_accepts_finite_low_rounded_zero_and_high_quotes(tmp_path, monkeypatch, quote, expected_bp):
    monkeypatch.setattr(validate, 'ROOT', tmp_path)
    daily, trades = accepted_quote(quote)
    assert daily[0]['official_bp'] == expected_bp
    write_tables(tmp_path, daily, trades)
    assert errors() == []


@pytest.mark.parametrize('value', ['NaN', 'Inf', '-Inf', '1e999'])
@pytest.mark.parametrize('table,field', [('daily', 'official_bp'), ('daily', 'quoted_median_bp'), ('daily', 'loo_bp'),
                                         ('trades', 'spread_bp'), ('trades', 'coupon'), ('trades', 'upfront_pct')])
def test_nonfinite_float_is_rejected_even_without_an_upper_limit(tmp_path, monkeypatch, value, table, field):
    monkeypatch.setattr(validate, 'ROOT', tmp_path)
    daily, trades = accepted_quote('.0365')
    (daily if table == 'daily' else trades)[0][field] = value
    write_tables(tmp_path, daily, trades)
    assert any(field in e and 'non-finite' in e for e in errors())


def test_negative_spreads_and_reference_signs_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, 'ROOT', tmp_path)
    daily, trades = accepted_quote('.0365')
    daily[0]['official_bp'] = -1
    daily[0]['n_sign_from_reference'] = 1
    trades[0]['spread_bp'] = -1
    trades[0]['sign_by'] = 'reference'
    write_tables(tmp_path, daily, trades)
    found = errors()
    for field in ['official_bp', 'n_sign_from_reference', 'spread_bp', 'sign_by']:
        assert any(field in e for e in found)


def test_empty_canonical_tables_are_valid_but_still_require_correct_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, 'ROOT', tmp_path)
    write_tables(tmp_path, [], [])
    assert errors() == []
    (tmp_path / 'data' / 'canonical' / 'cds_daily.csv').write_text('date,entity,old_schema\n')
    assert any('columns' in e for e in errors())


def test_schema_columns_match_decoder_output_including_exclusion_counters():
    for schema, fields in zip(SCHEMAS, [dtcc.DAILY_FIELDS, dtcc.TRADE_FIELDS]):
        with schema.open() as f:
            columns = yaml.safe_load(f)['columns']
        assert [c['name'] for c in columns] == fields
