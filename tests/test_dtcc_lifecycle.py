"""Adversarial lifecycle/source-contract tests, using synthetic reports only."""
import csv
import io
import pathlib
import sys
import zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'harness'))
import dtcc
from dtcc_lifecycle import REQUIRED_COLUMNS, SourceSchemaError

DAY = '2026-11-24'


def row(identifier='1', action='NEWT', parent='', **kw):
    return {'Dissemination Identifier': identifier, 'Action type': action,
            'Original Dissemination Identifier': parent,
            'Underlying Asset Name': 'ORACLE', 'Execution Timestamp': DAY + 'T15:00:00Z',
            'Event timestamp': DAY + 'T15:00:00Z', 'Spread-Leg 1': '0.03', **kw}


def build(rows):
    return dtcc.ledger([(DAY, dtcc.slim(rows))])


def ids(result):
    return [r['Dissemination Identifier'] for rows in result.values() for r in rows]


def test_predecessor_correction_chain_counts_once_independent_of_file_order():
    rows = [row(), row('2', 'CORR', '1'), row('3', 'CORR', '2')]
    assert ids(build(rows)) == ['3']
    assert ids(build(list(reversed(rows)))) == ['3']


@pytest.mark.parametrize('target', ['1', '2', '3'])
def test_cancellation_of_any_chain_version_does_not_resurrect_an_old_price(target):
    rows = [row(), row('2', 'CORR', '1'), row('3', 'CORR', '2'), row('4', 'EROR', target)]
    result = build(rows)
    assert ids(result) == []
    assert result.quality['cancelled_lineages'] == 1


def test_correction_that_removes_entity_cannot_resurrect_original():
    correction = row('2', 'CORR', '1', **{'Underlying Asset Name': 'UNRELATED COMPANY'})
    assert ids(build([row(), correction])) == []


def test_root_references_need_unambiguous_event_order():
    earlier = row('2', 'CORR', '1', **{'Event timestamp': DAY + 'T16:00:00Z'})
    later = row('3', 'CORR', '1', **{'Event timestamp': DAY + 'T17:00:00Z'})
    assert ids(build([later, row(), earlier])) == ['3']
    later['Event timestamp'] = earlier['Event timestamp']
    result = build([row(), earlier, later])
    assert ids(result) == []
    assert result.quality['issue_counts']['ambiguous_correction_order'] == 1


def test_conflicting_duplicate_identifier_is_quarantined_but_exact_repeat_is_not():
    assert ids(build([row(), row()])) == ['1']
    result = build([row(), row(**{'Spread-Leg 1': '0.04'})])
    assert ids(result) == []
    assert result.quality['issue_counts']['conflicting_identifier'] == 1


def test_orphan_and_cycle_are_reported_without_guessing():
    orphan = build([row('2', 'CORR', 'missing')])
    assert ids(orphan) == []
    assert orphan.quality['issue_counts']['orphan_lineage'] == 1
    cycle = build([row('2', 'CORR', '3'), row('3', 'CORR', '2')])
    assert ids(cycle) == []
    assert cycle.quality['issue_counts']['cyclic_lineage'] == 1


def zipped(columns, rows=()):
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(columns)
    writer.writerows(rows)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('credits.csv', text.getvalue())
    return stream.getvalue()


def test_schema_validation_also_runs_on_empty_file():
    assert dtcc.rows_of(zipped(REQUIRED_COLUMNS)) == []
    columns = [c if c != 'UPI FISN' else 'UPI FISN renamed' for c in REQUIRED_COLUMNS]
    with pytest.raises(SourceSchemaError, match='UPI FISN'):
        dtcc.rows_of(zipped(columns))


def test_duplicate_columns_or_truncated_rows_are_invalid():
    with pytest.raises(SourceSchemaError, match='duplicate'):
        dtcc.rows_of(zipped([*REQUIRED_COLUMNS, REQUIRED_COLUMNS[0]]))
    with pytest.raises(SourceSchemaError, match='row width'):
        dtcc.rows_of(zipped(REQUIRED_COLUMNS, [['1']]))


def test_unknown_boolean_encoding_is_not_treated_as_false():
    values = ['' for _ in REQUIRED_COLUMNS]
    values[REQUIRED_COLUMNS.index('Package indicator')] = 'YES'
    with pytest.raises(SourceSchemaError, match='indicator'):
        dtcc.rows_of(zipped(REQUIRED_COLUMNS, [values]))


def test_invalid_schema_preserves_previous_canonical_prices(tmp_path, monkeypatch):
    canon = tmp_path / 'data' / 'canonical'
    canon.mkdir(parents=True)
    previous = canon / 'cds_daily.csv'
    previous.write_text('previous accepted data\n')
    monkeypatch.setattr(dtcc, 'ROOT', tmp_path)
    monkeypatch.setattr(dtcc, 'fetch', lambda *a: zipped(['renamed header']))
    monkeypatch.setattr(sys, 'argv', ['dtcc.py', '--days', '7', '--offline'])
    with pytest.raises(SystemExit, match='invalid'):
        dtcc.main()
    assert previous.read_text() == 'previous accepted data\n'
    import json
    quality = json.loads((canon / 'cds_source_quality.json').read_text())
    assert quality['source_quality']['status'] == 'invalid'
    assert quality['source_quality']['calculation_eligible'] is False
    assert 'canonical_sha256' not in quality


def test_unrelated_orphan_does_not_block_tracked_entities():
    result = build([row(), row('2', 'CORR', 'missing', **{'Underlying Asset Name': 'UNRELATED'})])
    assert ids(result) == ['1']
    assert result.quality['status'] == 'complete'


# --- input_impact: which names a quarantined record could still touch (the website's headline gate reads this) ---

def impact(rows):
    return build(rows).quality['input_impact']


def test_a_clean_ledger_reports_no_impact():
    assert impact([row(), row('2', 'CORR', '1')]) == {}


def test_an_orphan_correction_reports_its_name_and_execution_day():
    out = impact([row('9', 'CORR', 'Z1', **{'Underlying Asset Name': 'COREWEAVE', 'Execution Timestamp': '2026-10-15T15:00:00Z'})])
    assert out == {'CRWV': {'latest_execution_date': '2026-10-15', 'invalid_execution_dates': 0}}


def test_every_version_of_a_conflicting_identifier_counts():
    rows = [row('5'), row('5', **{'Execution Timestamp': '2026-11-25T15:00:00Z', 'Spread-Leg 1': '0.04'})]
    assert impact(rows) == {'ORCL': {'latest_execution_date': '2026-11-25', 'invalid_execution_dates': 0}}


def test_a_version_naming_no_entity_is_unidentified_and_another_name_is_left_out():
    rows = [row('7', 'CORR', 'Z2'), row('8', 'CORR', '7', **{'Underlying Asset Name': ''}),
            row('9', 'CORR', '8', **{'Underlying Asset Name': 'SOME OTHER ISSUER'})]
    out = impact(rows)
    assert set(out) == {'ORCL', 'UNIDENTIFIED'} and out['UNIDENTIFIED']['latest_execution_date'] == DAY


def test_unreadable_execution_days_and_unknown_actions_fail_closed():
    out = impact([row('3', 'XXXX', **{'Execution Timestamp': 'later'}), row('4', 'CORR', 'Z3', **{'Execution Timestamp': ''})])
    assert out == {'ORCL': {'latest_execution_date': None, 'invalid_execution_dates': 2}}
