import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harness'))
import refresh_scoreboard as gate


def fixture(tmp_path):
    (tmp_path / 'methods').mkdir()
    (tmp_path / 'methods/a.yaml').write_text('name: a\n')
    canon = tmp_path / 'data/canonical'
    canon.mkdir(parents=True)
    for name in ('cds_daily.csv', 'cds_trades.csv'):
        (canon / name).write_text('date,entity\n2026-11-27,TEST\n')
    quality = {'decoder_version': gate.DECODER, 'generated_at': '2026-11-28T08:00:00+00:00',
               'source_quality': {'status': 'complete', 'calculation_eligible': True},
               'canonical_sha256': {n: gate.sha256(canon / n) for n in ('cds_daily.csv', 'cds_trades.csv')}}
    gate.write_json(canon / 'cds_source_quality.json', quality)
    (tmp_path / 'scoreboard').mkdir()
    old = b'{"updated":"2026-11-27","methods":[{"method":"previous"}]}\n'
    (tmp_path / 'scoreboard/scoreboard.json').write_bytes(old)
    return canon, quality, old


def test_partial_source_preserves_previous_scoreboard(tmp_path):
    canon, q, old = fixture(tmp_path)
    q['source_quality'] = {'status': 'partial', 'calculation_eligible': False}
    gate.write_json(canon / 'cds_source_quality.json', q)
    r = gate.refresh(tmp_path, scorer=lambda *_: (_ for _ in ()).throw(AssertionError('must not score')))
    assert r['status'] == 'blocked'
    assert (tmp_path / 'scoreboard/scoreboard.json').read_bytes() == old
    assert json.loads((tmp_path / 'scoreboard/last_attempt.json').read_text())['previous_scoreboard_retained']


def test_hash_mismatch_and_stale_sidecar_block_publication(tmp_path):
    canon, q, old = fixture(tmp_path)
    (canon / 'cds_daily.csv').write_text('tampered')
    r = gate.refresh(tmp_path, started_at=dt.datetime(2026, 11, 28, 9, tzinfo=dt.timezone.utc))
    assert 'canonical_hash_mismatch:cds_daily.csv' in r['reasons']
    assert 'source_quality_not_from_this_attempt' in r['reasons']
    assert (tmp_path / 'scoreboard/scoreboard.json').read_bytes() == old


def test_one_failed_method_does_not_replace_scoreboard_with_partial_results(tmp_path):
    _, _, old = fixture(tmp_path)
    (tmp_path / 'methods/b.yaml').write_text('name: b\n')
    def score(_, method, __):
        if method.stem == 'b':
            raise ValueError('failed')
        return {'method': 'a'}
    r = gate.refresh(tmp_path, scorer=score)
    assert r['reasons'] == ['score_failed:b']
    assert (tmp_path / 'scoreboard/scoreboard.json').read_bytes() == old


def test_complete_inputs_record_hashes_without_claiming_institutional_validation(tmp_path):
    _, _, _ = fixture(tmp_path)
    r = gate.refresh(tmp_path, scorer=lambda *_: {'method': 'a'})
    out = json.loads((tmp_path / 'scoreboard/scoreboard.json').read_text())
    assert r['status'] == 'published_research'
    assert out['decoder_version'] == gate.DECODER
    assert out['decoder_status'] == 'current'
    assert out['validation_scope'] == 'retrospective_research'
    assert out['settlement_eligible'] is False and out['institutional_use_eligible'] is False
    assert out['canonical_sha256'] and out['method_sha256']


def test_failed_collection_cannot_reuse_earlier_good_quality(tmp_path):
    _, _, old = fixture(tmp_path)
    r = gate.refresh(tmp_path, collection_ok=False)
    assert 'source_collection_failed' in r['reasons']
    assert (tmp_path / 'scoreboard/scoreboard.json').read_bytes() == old


def test_replaced_canonical_and_sidecar_during_scoring_cannot_certify_old_result(tmp_path):
    canon, q, old = fixture(tmp_path)
    def changed_source(*_):
        (canon / 'cds_daily.csv').write_text('new source')
        q['canonical_sha256']['cds_daily.csv'] = gate.sha256(canon / 'cds_daily.csv')
        gate.write_json(canon / 'cds_source_quality.json', q)
        return {'method': 'a'}
    r = gate.refresh(tmp_path, scorer=changed_source)
    assert 'source_changed_during_scoring' in r['reasons']
    assert (tmp_path / 'scoreboard/scoreboard.json').read_bytes() == old


def test_method_changed_during_scoring_cannot_be_published_under_new_hash(tmp_path):
    _, _, old = fixture(tmp_path)
    def changed_method(*_):
        (tmp_path / 'methods/a.yaml').write_text('name: changed\n')
        return {'method': 'a'}
    r = gate.refresh(tmp_path, scorer=changed_method)
    assert 'methods_changed_during_scoring' in r['reasons']
    assert (tmp_path / 'scoreboard/scoreboard.json').read_bytes() == old
