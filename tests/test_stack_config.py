"""The Stack method file and its schema (stack/config.py, schema/stack_method.yaml). No network, no data/."""
import copy, pathlib, sys
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stack import config  # noqa: E402

BASE = yaml.safe_load(open(ROOT / 'stack' / 'stack-v0.yaml'))


def errors_after(change):
    m = copy.deepcopy(BASE)
    change(m)
    return config.validate(m)


def test_the_shipped_method_file_is_valid():
    m, h = config.load()
    assert m['name'] == 'stack' and len(h) == 64
    assert config.cuts(m) == (35.0, 50.0, 65.0, 80.0)


def test_the_method_file_is_not_under_methods():
    # the score workflow scores every methods/*.yaml with the v0.x harness
    assert not list((ROOT / 'methods').glob('stack*'))


def test_unknown_top_level_key_is_rejected():
    errs = errors_after(lambda m: m.update(code='import os'))
    assert any("unknown key 'code'" in e for e in errs)


def test_unknown_nested_keys_are_rejected():
    assert any('engine: unknown key' in e for e in errors_after(lambda m: m['engine'].update(clip=100)))
    assert any('L2: unknown key' in e for e in errors_after(lambda m: m['layers']['L2'].update(fill_forward=True)))
    assert any('junior: unknown key' in e for e in errors_after(lambda m: m['composites']['junior'].update(boost=2)))


def test_missing_keys_are_rejected():
    assert any("missing key 'tau'" in e for e in errors_after(lambda m: m['engine'].pop('tau')))
    assert any("missing key 'L4'" in e for e in errors_after(lambda m: m['layers'].pop('L4')))


def test_wrong_types_and_ranges_are_rejected():
    assert any('must be number' in e for e in errors_after(lambda m: m['engine'].update(tau='2')))
    assert any('must be int' in e for e in errors_after(lambda m: m['layers']['L2'].update(min_prints=5.5)))
    assert any('must be int' in e for e in errors_after(lambda m: m['layers']['L2'].update(min_prints=True)))
    assert any('above' in e for e in errors_after(lambda m: m['engine'].update(group_cap=1.5)))
    assert any('not one of' in e for e in errors_after(lambda m: m['layers']['L2'].update(prints='best')))
    assert any('outside' in e for e in errors_after(lambda m: m['composites']['junior']['layers'].update(L2=3)))


def test_bands_must_rise():
    assert any('rise strictly' in e for e in errors_after(lambda m: m['engine']['bands'].update(watch=70)))


def test_staleness_must_fall_after_full_weight():
    assert any('zero_weight_days' in e for e in errors_after(lambda m: m['engine']['staleness'].update(zero_weight_days=100)))


def test_every_l2_entity_needs_a_borrower_group():
    assert any('NBIS has no borrower group' in e for e in errors_after(lambda m: m['layers']['L2']['entities'].append('NBIS')))
    assert any('in both' in e for e in errors_after(lambda m: m['groups'].update(Other=['ORCL'])))


def test_l2_window_rules_are_consistent():
    assert any('cannot exceed window_sessions' in e for e in errors_after(lambda m: m['layers']['L2'].update(min_sessions_with_prints=6)))
    assert any('z_min_history' in e for e in errors_after(lambda m: m['layers']['L2'].update(z_min_history=300)))


def test_composites_name_known_layers_of_the_right_kind():
    assert any('unknown layer L9' in e for e in errors_after(lambda m: m['composites']['junior']['layers'].update(L9=0.1)))
    assert any('not junior' in e for e in errors_after(lambda m: m['composites']['junior']['layers'].update(L3=0.1)))


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    text = (ROOT / 'stack' / 'stack-v0.yaml').read_text().replace('  tau: 2.0\n', '  tau: 2.0\n  tau: 9.0\n')
    p = tmp_path / 'm.yaml'
    p.write_text(text)
    with pytest.raises(config.ConfigError, match='duplicate key'):
        config.load(p)


def test_load_reports_every_error(tmp_path):
    m = copy.deepcopy(BASE)
    m['extra'] = 1
    m['engine']['tau'] = -1
    p = tmp_path / 'm.yaml'
    p.write_text(yaml.safe_dump(m))
    with pytest.raises(config.ConfigError) as e:
        config.load(p)
    assert len(e.value.errors) >= 2


def test_config_hash_changes_with_the_file(tmp_path):
    p = tmp_path / 'm.yaml'
    p.write_text((ROOT / 'stack' / 'stack-v0.yaml').read_text())
    _, h1 = config.load(p)
    p.write_text(p.read_text() + '\n# comment\n')
    _, h2 = config.load(p)
    assert h1 != h2
