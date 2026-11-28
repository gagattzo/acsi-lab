import importlib.util,json
from pathlib import Path
from types import SimpleNamespace

def test_source_failure_updates_quality_and_preserves_old_observation(tmp_path,monkeypatch):
    harness=Path(__file__).parents[1]/'harness'
    monkeypatch.syspath_prepend(str(harness))
    spec=importlib.util.spec_from_file_location('daily_runner',harness/'daily_trace_update.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    (tmp_path/'data').mkdir()
    manifest=tmp_path/'data/acsi_bond_manifest.json'
    manifest.write_text(json.dumps({'as_of':'2026-11-25','quality_status':'complete_research','updated_at':'old','settlement_eligible':False}))
    runner.ROOT=tmp_path
    monkeypatch.setattr(runner,'expected_session',lambda:'2026-11-27')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1))
    archived=[];monkeypatch.setattr(runner,'seal',lambda *a:archived.append(a))
    monkeypatch.chdir(tmp_path)
    assert runner.main()==1
    m=json.loads(manifest.read_text())
    assert m['as_of']=='2026-11-25'
    assert m['quality_status']==m['status']=='source_error'
    assert m['updated_at']==m['generated_at']!='old'
    assert m['expected_session']=='2026-11-27'
    assert not m['publication_eligible'] and not m['perp_eligible']
    assert archived
