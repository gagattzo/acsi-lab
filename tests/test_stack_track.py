"""Edition tracking (stack/track.py) and the build CLI (stack/build.py) on a synthetic repo root: no network, no data/."""
import csv, datetime as dt, json, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import build, track  # noqa: E402

T0 = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)


def edition(as_of='2026-11-25', z=1.0):
    return {'as_of': as_of, 'method': {'name': 'stack', 'version': '0.0.1'}, 'config_sha256': 'c' * 64,
            'layers': {'L2': {'Z': z, 'status': 'fresh'}}}


INPUTS = [{'path': 'data/raw/dtcc/2026-11-25.zip', 'sha256': 'a' * 64, 'bytes': 10}]


def test_first_line_starts_the_chain(tmp_path):
    h = tmp_path / 'e.jsonl'
    rec, why = track.record(edition(), INPUTS, h, commit='abc', now=T0)
    assert why == 'new edition' and rec['seq'] == 1 and rec['prev_hash'] is None
    assert rec['hash'] == track.line_hash(rec) and rec['built_at'] == '2026-11-27T12:00:00+00:00'
    assert set(rec) == {'seq', 'as_of', 'built_at', 'method', 'config_sha256', 'code_commit', 'inputs', 'output', 'prev_hash', 'hash'}
    assert track.verify(h) == []


def test_rerun_with_the_same_output_and_inputs_appends_nothing(tmp_path):
    h = tmp_path / 'e.jsonl'
    track.record(edition(), INPUTS, h, now=T0)
    before = h.read_bytes()
    rec, why = track.record(edition(), INPUTS, h, now=T0 + dt.timedelta(hours=5), commit='other')
    assert rec is None and 'already recorded' in why and h.read_bytes() == before


def test_a_different_output_appends_a_correction_never_an_edit(tmp_path):
    h = tmp_path / 'e.jsonl'
    track.record(edition(z=1.0), INPUTS, h, now=T0)
    track.record(edition('2026-11-26', z=2.0), INPUTS, h, now=T0)
    first = h.read_text().splitlines()
    rec, why = track.record(edition(z=1.5), INPUTS, h, now=T0)
    assert rec['seq'] == 3 and rec['correction_of'] == 1 and rec['corrected'] == ['output'] and 'correction of seq 1' in why
    assert h.read_text().splitlines()[:2] == first  # earlier lines untouched
    rec2, _ = track.record(edition(z=1.7), INPUTS, h, now=T0)
    assert rec2['correction_of'] == 1 and rec2['prev_hash'] == rec['hash']
    assert track.record(edition(z=1.7), INPUTS, h, now=T0)[0] is None  # same as the latest line for that day
    assert track.verify(h) == []


def test_changed_inputs_with_the_same_output_are_recorded(tmp_path):
    h = tmp_path / 'e.jsonl'
    track.record(edition(), INPUTS, h, now=T0)
    rec, _ = track.record(edition(), [dict(INPUTS[0], sha256='b' * 64)], h, now=T0)
    assert rec['correction_of'] == 1 and rec['corrected'] == ['inputs']


def test_verify_detects_an_edited_line(tmp_path):
    h = tmp_path / 'e.jsonl'
    for d, z in (('2026-11-24', 1.0), ('2026-11-25', 2.0), ('2026-11-26', 3.0)):
        track.record(edition(d, z), INPUTS, h, now=T0)
    lines = h.read_text().splitlines()
    rec = json.loads(lines[1])
    rec['output']['layers']['L2']['Z'] = 0.1  # quietly lower a past print
    h.write_text('\n'.join([lines[0], track.canonical(rec), lines[2]]) + '\n')
    errs = track.verify(h)
    assert errs and 'line 2' in errs[0] and 'edited' in errs[0]
    assert track.main(['--verify', '--history', str(h)]) == 1


def test_verify_detects_a_rehashed_edit_and_a_deleted_line(tmp_path):
    h = tmp_path / 'e.jsonl'
    for d in ('2026-11-24', '2026-11-25', '2026-11-26'):
        track.record(edition(d), INPUTS, h, now=T0)
    lines = h.read_text().splitlines()
    rec = json.loads(lines[0])
    rec['output']['layers']['L2']['Z'] = 9.0
    rec['hash'] = track.line_hash(rec)  # a careful forger re-hashes the line: the next line's prev_hash breaks
    h.write_text('\n'.join([track.canonical(rec)] + lines[1:]) + '\n')
    assert any('line 2: prev_hash' in e for e in track.verify(h))
    h.write_text('\n'.join([lines[0], lines[2]]) + '\n')
    assert track.verify(h)


def test_record_refuses_to_extend_a_broken_chain(tmp_path):
    h = tmp_path / 'e.jsonl'
    track.record(edition(), INPUTS, h, now=T0)
    h.write_text(h.read_text().replace('"Z":1.0', '"Z":5.0'))
    with pytest.raises(SystemExit, match='broken'):
        track.record(edition('2026-11-26'), INPUTS, h, now=T0)


def test_verify_of_an_intact_chain_exits_zero(tmp_path, capsys):
    h = tmp_path / 'e.jsonl'
    track.record(edition(), INPUTS, h, now=T0)
    assert track.main(['--verify', '--history', str(h)]) == 0 and 'chain ok' in capsys.readouterr().out


# --- the build on a synthetic repo root ------------------------------------------------------------------------------

def fake_root(tmp_path, n=30):
    canon = tmp_path / 'data' / 'canonical'
    raw = tmp_path / 'data' / 'raw' / 'dtcc'
    canon.mkdir(parents=True)
    raw.mkdir(parents=True)
    d, days = dt.date(2026, 1, 5), []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    fields = ['date', 'entity', 'dissemination_id', 'exec_ts', 'kind', 'spread_bp', 'coupon', 'coupon_normalised',
              'upfront_pct', 'notional_capped']
    with open(canon / 'cds_trades.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, day in enumerate(days):
            (raw / f'{day}.zip').write_bytes(f'PK fake {day}'.encode())
            for e, lv in (('ORCL', 150), ('META', 80)):
                for k in range(3):
                    w.writerow({'date': day, 'entity': e, 'dissemination_id': f'{e}{i}{k}', 'exec_ts': day + 'T15:00:00Z',
                                'kind': 'quoted', 'spread_bp': lv + (i % 4), 'coupon': '', 'coupon_normalised': False,
                                'upfront_pct': '', 'notional_capped': False})
    return tmp_path, days


def test_build_reports_every_layer_and_holds_no_timestamps(tmp_path):
    root, days = fake_root(tmp_path)
    out, inputs = build.run(days[-1], None, offline=True, root=root, weekly=True)
    assert out['layers']['L1']['status'] == 'unavailable' and out['layers']['L1']['Z'] is None  # offline, no price cache
    assert out['layers']['L3']['status'] == 'unavailable' and out['layers']['L3']['Z'] is None  # offline, no SEC BDC cache
    assert out['layers']['L4']['status'] == 'unavailable' and out['layers']['L4']['Z'] is None  # offline, no SEC cache
    assert all(out['layers'][k]['reason'] for k in ('L1', 'L3', 'L4'))
    assert all(out['layers'][k]['build_date'] == days[-1] for k in out['layers']) and out['date_check']['ok']
    l2 = out['layers']['L2']
    assert l2['sessions_archived'] == 30 and not l2['scored']        # no live session yet, 126 needed: shown, not scored
    assert l2['gate']['raw_files'] == 30 and l2['gate']['sessions_live'] == 0
    assert out['junior']['weights_effective']['L2'] == 0.0 and out['junior']['status'] == 'insufficient_live_weight'
    assert out['senior']['status'] == 'insufficient_live_weight' and out['senior']['Z'] is None
    assert out['weekly_stack']['label'] == 'optional, not adopted' and out['weekly_stack']['Z'] is None
    # 30 sessions is less than z_min_history: no z, so no value and no band (never a filled-in number)
    assert l2['status'] == 'unavailable' and l2['Z'] is None and l2['band'] is None
    assert out['depth']['layers_without_band'] == ['L1', 'L2', 'L3', 'L4'] and out['depth']['depth'] == 0
    assert '_series' not in json.dumps(out) and 'built_at' not in json.dumps(out)
    assert len(inputs) == 31 and all(len(i['sha256']) == 64 for i in inputs)
    assert build.run(days[-1], None, offline=True, root=root, weekly=True) == (out, inputs)  # deterministic


def test_build_rejects_unknown_layers(tmp_path):
    root, days = fake_root(tmp_path, 5)
    with pytest.raises(SystemExit):
        build.run(days[-1], ['L9'], root=root)


def test_layers_not_requested_are_labelled(tmp_path):
    root, days = fake_root(tmp_path, 10)
    out, _ = build.run(days[-1], ['L2'], offline=True, root=root)
    assert out['layers']['L1']['status'] == 'not_requested' and out['layers_requested'] == ['L2']


def test_track_records_a_build_once(tmp_path):
    root, days = fake_root(tmp_path, 12)
    h = tmp_path / 'hist.jsonl'
    out, inputs = build.run(days[-1], ['L2'], offline=True, root=root)
    assert track.record(out, inputs, h, out['config_sha256'], None, now=T0)[0]['seq'] == 1
    out2, inputs2 = build.run(days[-1], ['L2'], offline=True, root=root)
    assert track.record(out2, inputs2, h, out2['config_sha256'], None, now=T0)[0] is None
    (root / 'data' / 'raw' / 'dtcc' / f'{days[0]}.zip').write_bytes(b'PK republished')
    out3, inputs3 = build.run(days[-1], ['L2'], offline=True, root=root)
    rec, _ = track.record(out3, inputs3, h, out3['config_sha256'], None, now=T0)
    assert rec['correction_of'] == 1 and 'inputs' in rec['corrected'] and track.verify(h) == []
