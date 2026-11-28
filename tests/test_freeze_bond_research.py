import hashlib
import json
from pathlib import Path

import pytest

from harness.freeze_bond_research import prepare_bond_fixing, ROOT
from harness.research_fixing import FixingError


@pytest.fixture
def replay(tmp_path):
    def write(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path
    raw = tmp_path / "secret.parquet"; raw.write_bytes(b"private rows")
    universe = write("universe.json", [{"cusip": "SECRET001", "issuer": "Company A"}, {"cusip": "SECRET002", "issuer": "Company B"}])
    treasury = tmp_path / "treasury.csv"; treasury.write_text("Date,5 Yr\n11/27/2026,4")
    calendar = write("calendar.json", {"valid_from": "2026-11-24", "valid_through": "2026-11-30", "closed_dates": ["2026-11-26"], "open_overrides": []})
    defaults = write("defaults.json", {"events": []})
    write("acsi_bond_manifest.json", {"private_candidate": True, "publication_eligible": False,
          "method_version": "bond-research-v1", "generated_at": "2026-11-28T11:00:00Z",
          "input_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "max_carry_sessions": 5,
          "minimum_customer_trade_size": 100000, "minimum_eligible_prints": 3,
          "replay_input_hashes": {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in
          {"raw_tape": raw, "universe": universe, "treasury": treasury, "calendar": calendar, "default_events": defaults,
           "calculator": ROOT / "harness/bond_spreads.py", "snapshot_adapter": ROOT / "harness/bond_provenance.py",
           "snapshot_cleaner": ROOT / "harness/finra_trace.py"}.items()}})
    write("acsi_bond_spreads_daily.json", [{"date": "2026-11-27", "status": "complete_research", "g_spread_issuer_capped_bp": 600.0}])
    write("acsi_bond_inputs_daily.json", [{"date": "2026-11-27", "id": f"ACSI-BOND-{number:02d}", "status": "active",
          "source_date": "2026-11-27", "spread_bp": spread, "fixed_weight": .5} for number, spread in [(1,400), (2,800)]])
    return {"replay_dir": tmp_path, "trades": raw, "universe": universe, "treasury": treasury,
            "observation_date": "2026-11-27", "recorded_at": "2026-11-28T12:00:00Z", "published_at": "2026-11-28T12:01:00Z",
            "calendar": calendar, "default_events": defaults}


def test_native_replay_adapter_reconstructs_and_sanitizes(replay):
    artifact = prepare_bond_fixing(**replay)
    assert artifact["value"] == 600
    assert artifact["settlement_eligible"] is False
    assert artifact["fresh_weight"] == 1
    for source in artifact["sources"]:
        assert source["availability_basis"] == "local_snapshot_observed"
        assert source["point_in_time_verified"] is False
        assert source["completeness_scope"] == "local_snapshot"
        assert source["upstream_feed_complete"] is None
    text = json.dumps(artifact)
    assert "SECRET001" not in text and "Company A" not in text and "secret.parquet" not in text


def test_replay_raw_source_mismatch_blocks(replay):
    replay["trades"].write_bytes(b"different input")
    with pytest.raises(FixingError, match="Raw snapshot hash"):
        prepare_bond_fixing(**replay)


def test_replay_missing_bond_cannot_reweight_survivor(replay):
    path = replay["replay_dir"] / "acsi_bond_inputs_daily.json"
    path.write_text(json.dumps(json.loads(path.read_text())[:1]))
    with pytest.raises(FixingError, match="availability disagrees"):
        prepare_bond_fixing(**replay)
    path = replay["replay_dir"] / "acsi_bond_spreads_daily.json"
    rows = json.loads(path.read_text()); rows[0]["g_spread_issuer_capped_bp"] = None; rows[0]["status"] = "incomplete_coverage"
    path.write_text(json.dumps(rows))
    artifact = prepare_bond_fixing(**replay)
    assert artifact["value"] is None and len(artifact["constituents"]) == 2
    assert artifact["constituents"][1]["weight"] == .5


def test_replay_new_default_cannot_use_old_healthy_summary(replay):
    replay["default_events"].write_text(json.dumps({"events": [{"issuer_group": "Company A", "default_date": "2026-11-27"}]}))
    manifest_path = replay["replay_dir"] / "acsi_bond_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["replay_input_hashes"]["default_events"] = hashlib.sha256(replay["default_events"].read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(FixingError, match="availability disagrees"):
        prepare_bond_fixing(**replay)
    path = replay["replay_dir"] / "acsi_bond_spreads_daily.json"
    rows = json.loads(path.read_text()); rows[0]["g_spread_issuer_capped_bp"] = None; rows[0]["status"] = "default_rule_pending"
    path.write_text(json.dumps(rows))
    artifact = prepare_bond_fixing(**replay)
    assert artifact["value"] is None
    assert all(part["status"] == "default_rule_pending" for part in artifact["constituents"])
    assert sum(part["weight"] for part in artifact["constituents"]) == 1


def test_replay_aggregate_and_weight_mismatches_rejected(replay):
    path = replay["replay_dir"] / "acsi_bond_spreads_daily.json"
    rows = json.loads(path.read_text()); rows[0]["g_spread_issuer_capped_bp"] = 610
    path.write_text(json.dumps(rows))
    with pytest.raises(FixingError, match="independently reconstructed"):
        prepare_bond_fixing(**replay)
    path = replay["replay_dir"] / "acsi_bond_inputs_daily.json"
    rows = json.loads(path.read_text()); rows[0]["fixed_weight"] = .75
    path.write_text(json.dumps(rows))
    with pytest.raises(FixingError, match="weights"):
        prepare_bond_fixing(**replay)


def test_replay_cannot_precede_generation(replay):
    replay["recorded_at"] = "2026-11-28T10:00:00Z"
    with pytest.raises(FixingError, match="predates replay generation"):
        prepare_bond_fixing(**replay)


def test_replay_calculation_rules_change_requires_new_series(replay, tmp_path):
    from harness.research_fixing import persist_fixing
    first = prepare_bond_fixing(**replay)
    persist_fixing(first, tmp_path / "fixings")
    path = replay["replay_dir"] / "acsi_bond_manifest.json"
    manifest = json.loads(path.read_text()); manifest["minimum_eligible_prints"] = 4
    path.write_text(json.dumps(manifest))
    replay["published_at"] = "2026-11-28T12:02:00Z"
    replay["supersedes"] = first["artifact_sha256"]
    updated = prepare_bond_fixing(**replay)
    assert first["method"]["implementation_sha256"] != updated["method"]["implementation_sha256"]
    with pytest.raises(FixingError, match="new series_id"):
        persist_fixing(updated, tmp_path / "fixings")


def test_replay_altered_universe_or_curve_fails_lineage(replay):
    replay["treasury"].write_text("changed curve")
    with pytest.raises(FixingError, match="hash mismatch: treasury"):
        prepare_bond_fixing(**replay)
