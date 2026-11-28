"""Contract/adversarial tests for research publication, not a model backtest."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from harness.research_fixing import FixingError, build_fixing, canonical_bytes, digest, load_fixing, persist_fixing


@pytest.fixture
def inputs(tmp_path):
    snapshot = tmp_path / "private-trades.csv"
    snapshot.write_text("private source data never copied to a fixing\n")
    method = {"id": "test-credit-level", "version": "v1", "series_kind": "cash_bond_candidate", "unit": "bp",
              "aggregation": "weighted_mean", "implementation_sha256": "a" * 64, "constituents": [{"id": "group_a", "weight": .75}, {"id": "group_b", "weight": .25}],
              "freshness": {"basis": "calendar_days", "maximum_age": 5}}
    candidate = {"series_id": "private-bond-v1", "observation_date": "2026-11-27", "cutoff_at": "2026-11-28T12:00:00Z",
                 "published_at": "2026-11-28T12:05:00Z", "sources": [{"id": "tape", "available_at": "2026-11-28T11:00:00Z",
                 "complete": True, "point_in_time_verified": False}], "constituents": [
                     {"id": "group_a", "value": 400, "observed_date": "2026-11-27", "available_at": "2026-11-28T11:00:00Z", "status": "accepted", "source_ids": ["tape"]},
                     {"id": "group_b", "value": 800, "observed_date": "2026-11-27", "available_at": "2026-11-28T11:00:00Z", "status": "accepted", "source_ids": ["tape"]}]}
    return candidate, method, {"tape": snapshot}


def test_deterministic_fixed_weights_private_and_no_raw_content(inputs):
    c, m, s = inputs
    one = build_fixing(c, m, s)
    assert one == build_fixing(c, m, s)
    assert one["value"] == 500
    assert one["calculation_status"] == "calculated"
    assert one["settlement_eligible"] is False and one["publication_authorized"] is False
    assert "source_point_in_time_unverified:tape" in one["release_blockers"]
    text = canonical_bytes(one).decode()
    assert "private source data" not in text and "private-trades.csv" not in text
    assert one["method"]["sha256"] == digest(m)


def test_missing_constituent_keeps_weight_and_blocks_aggregate(inputs):
    c, m, s = inputs
    c["constituents"].pop()
    fixing = build_fixing(c, m, s)
    assert fixing["value"] is None
    assert fixing["constituents"][1]["weight"] == .25
    assert fixing["calculation_blockers"] == ["missing:group_b"]


@pytest.mark.parametrize("status", ["ambiguous", "solver_domain", "default_rule_pending", "source_incomplete", "missing", "stale"])
def test_unresolved_input_blocks_and_never_drops_weight(inputs, status):
    c, m, s = inputs
    c["constituents"][0]["status"] = status
    fixing = build_fixing(c, m, s)
    assert fixing["value"] is None and fixing["constituents"][0]["value"] is None
    assert fixing["constituents"][0]["weight"] == .75
    assert f"{status}:group_a" in fixing["calculation_blockers"]


def test_future_source_cannot_be_used_for_old_cutoff(inputs):
    c, m, s = inputs
    c["sources"][0]["available_at"] = "2026-11-29T00:00:00Z"
    with pytest.raises(FixingError, match="unavailable"):
        build_fixing(c, m, s)


def test_future_constituent_and_future_observation_rejected(inputs):
    c, m, s = inputs
    c["constituents"][0]["available_at"] = "2026-11-29T00:00:00Z"
    with pytest.raises(FixingError, match="unavailable"):
        build_fixing(c, m, s)
    c["constituents"][0]["available_at"] = "2026-11-28T11:00:00Z"
    c["constituents"][0]["observed_date"] = "2026-11-28"
    with pytest.raises(FixingError, match="after the fixing date"):
        build_fixing(c, m, s)


def test_constituent_cannot_precede_source_availability(inputs):
    c, m, s = inputs
    c["constituents"][0]["available_at"] = "2026-11-28T10:00:00Z"
    with pytest.raises(FixingError, match="before its source"):
        build_fixing(c, m, s)


def test_late_collection_is_retrospective_not_point_in_time_evidence(inputs):
    c, m, s = inputs
    fixing = build_fixing(c, m, s)
    assert fixing["observation_date"] == "2026-11-27"
    assert fixing["cutoff_at"].startswith("2026-11-28")
    assert "source_point_in_time_unverified:tape" in fixing["release_blockers"]


def test_carry_counts_calendar_days_and_expires(inputs):
    c, m, s = inputs
    c["constituents"][0]["observed_date"] = "2026-11-22"
    fixing = build_fixing(c, m, s)
    assert fixing["value"] == 500 and fixing["carried_weight"] == .75
    c["constituents"][0]["observed_date"] = "2026-11-21"
    fixing = build_fixing(c, m, s)
    assert fixing["value"] is None and fixing["constituents"][0]["status"] == "stale"


def test_session_calendar_holiday_and_boundary(inputs):
    c, m, s = inputs
    m["freshness"] = {"basis": "sessions", "maximum_age": 1, "session_dates": ["2026-11-24", "2026-11-25", "2026-11-27"]}
    c["constituents"][0]["observed_date"] = "2026-11-25"
    assert build_fixing(c, m, s)["value"] == 500
    c["constituents"][0]["observed_date"] = "2026-11-24"
    assert build_fixing(c, m, s)["value"] is None
    c["constituents"][0]["observed_date"] = "2026-11-23"
    with pytest.raises(FixingError, match="calendar"):
        build_fixing(c, m, s)


def test_incomplete_source_nulls_value_without_claiming_feed_empty(inputs):
    c, m, s = inputs
    c["sources"][0]["complete"] = False
    fixing = build_fixing(c, m, s)
    assert fixing["value"] is None
    assert "source_incomplete:tape" in fixing["calculation_blockers"]


def test_source_hash_changes_when_bytes_change(inputs):
    c, m, s = inputs
    first = build_fixing(c, m, s)
    s["tape"].write_text("a valid later correction")
    second = build_fixing(c, m, s)
    assert first["sources"][0]["sha256"] != second["sources"][0]["sha256"]
    assert first["artifact_sha256"] != second["artifact_sha256"]


def test_idempotent_original_and_append_only_correction(inputs, tmp_path):
    c, m, s = inputs
    first = build_fixing(c, m, s)
    path = persist_fixing(first, tmp_path / "out")
    original = path.read_bytes()
    assert persist_fixing(first, tmp_path / "out") == path
    c["constituents"][0]["value"] = 420
    c["published_at"] = "2026-11-28T12:10:00Z"
    later = build_fixing(c, m, s, supersedes=first["artifact_sha256"])
    second = persist_fixing(later, tmp_path / "out")
    assert second != path and path.read_bytes() == original
    assert len(list(path.parent.glob("*.json"))) == 2
    assert load_fixing(second)["value"] == 515
    assert persist_fixing(first, tmp_path / "out") == path


def test_unannounced_rewrite_and_revision_fork_rejected(inputs, tmp_path):
    c, m, s = inputs
    first = build_fixing(c, m, s)
    persist_fixing(first, tmp_path)
    c["constituents"][0]["value"] = 420
    c["published_at"] = "2026-11-28T12:10:00Z"
    with pytest.raises(FixingError, match="supersede"):
        persist_fixing(build_fixing(c, m, s), tmp_path)
    second = build_fixing(c, m, s, supersedes=first["artifact_sha256"])
    persist_fixing(second, tmp_path)
    c["constituents"][0]["value"] = 430
    c["published_at"] = "2026-11-28T12:15:00Z"
    with pytest.raises(FixingError, match="latest"):
        persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)


def test_correction_cannot_go_back_in_publication_or_cutoff(inputs, tmp_path):
    c, m, s = inputs
    first = build_fixing(c, m, s)
    persist_fixing(first, tmp_path)
    c["constituents"][0]["value"] = 420
    with pytest.raises(FixingError, match="published later"):
        persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)
    c["published_at"] = "2026-11-28T12:10:00Z"
    c["cutoff_at"] = "2026-11-28T11:59:00Z"
    with pytest.raises(FixingError, match="cutoff backward"):
        persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)


def test_method_change_requires_different_series(inputs, tmp_path):
    c, m, s = inputs
    persist_fixing(build_fixing(c, m, s), tmp_path)
    m["version"] = "v2"
    c["observation_date"] = "2026-11-28"
    with pytest.raises(FixingError, match="new series_id"):
        persist_fixing(build_fixing(c, m, s), tmp_path)
    c["series_id"] = "private-bond-v2"
    persist_fixing(build_fixing(c, m, s), tmp_path)


def test_tamper_and_deleted_vintage_detected(inputs, tmp_path):
    c, m, s = inputs
    first = build_fixing(c, m, s)
    path = persist_fixing(first, tmp_path)
    altered = deepcopy(first); altered["value"] = 123
    path.write_text(json.dumps(altered))
    with pytest.raises(FixingError, match="hash mismatch"):
        load_fixing(path)
    with pytest.raises(FixingError, match="hash mismatch"):
        persist_fixing(first, tmp_path)
    path.write_bytes(canonical_bytes(first))
    c["published_at"] = "2026-11-28T12:10:00Z"
    persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)
    path.unlink()
    with pytest.raises(FixingError, match="chain is broken"):
        persist_fixing(first, tmp_path)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "400"])
def test_nonfinite_and_non_numeric_values_rejected(inputs, bad):
    c, m, s = inputs
    c["constituents"][0]["value"] = bad
    with pytest.raises(FixingError):
        build_fixing(c, m, s)


def test_unknown_fields_cannot_leak_private_payload(inputs):
    c, m, s = inputs
    c["raw_trades"] = "private"
    with pytest.raises(FixingError, match="Expected fields"):
        build_fixing(c, m, s)


def test_claimed_release_approval_cannot_switch_gate(inputs):
    c, m, s = inputs
    c["sources"][0]["point_in_time_verified"] = True
    fixing = build_fixing(c, m, s)
    assert fixing["settlement_eligible"] is False
    fixing["settlement_eligible"] = True
    with pytest.raises(FixingError, match="authorization"):
        persist_fixing(fixing, s["tape"].parent / "out")


def test_cli_produces_artifact_without_raw_output(inputs, tmp_path):
    c, m, s = inputs
    candidate_path = tmp_path / "candidate.json"; candidate_path.write_text(json.dumps(c))
    method_path = tmp_path / "method.json"; method_path.write_text(json.dumps(m))
    run = subprocess.run([sys.executable, "-m", "harness.research_fixing", "--candidate", str(candidate_path),
                          "--method", str(method_path), "--source", f"tape={s['tape']}", "--output-dir", str(tmp_path / "out")],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["settlement_eligible"] is False
    assert "private source data" not in run.stdout


def test_local_snapshot_never_claims_complete_upstream_feed(inputs):
    c, m, s = inputs
    c["sources"][0]["completeness_scope"] = "local_snapshot"
    fixing = build_fixing(c, m, s)
    assert fixing["sources"][0]["upstream_feed_complete"] is None
    assert "upstream_feed_completeness_unverified:tape" in fixing["release_blockers"]
    c["sources"][0]["upstream_feed_complete"] = True
    with pytest.raises(FixingError, match="upstream feed completeness"):
        build_fixing(c, m, s)


def test_implementation_change_cannot_silently_enter_same_series(inputs, tmp_path):
    c, m, s = inputs
    first = build_fixing(c, m, s); persist_fixing(first, tmp_path)
    m["implementation_sha256"] = "b" * 64
    c["published_at"] = "2026-11-28T12:10:00Z"
    with pytest.raises(FixingError, match="new series_id"):
        persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)


def test_private_source_archive_retains_exact_bytes_and_rejects_changes(inputs, tmp_path):
    from harness.research_fixing import archive_sources
    c, m, s = inputs
    artifact = build_fixing(c, m, s)
    store = tmp_path / "private_archive"
    archive_sources(artifact, s, store, candidate=c, method=m)
    assert (store / artifact["candidate_sha256"]).read_bytes() == canonical_bytes(c)
    assert (store / artifact["method"]["sha256"]).read_bytes() == canonical_bytes(m)
    archive_sources(artifact, s, store, candidate=c, method=m)
    stored = store / artifact["sources"][0]["sha256"]
    assert stored.read_bytes() == s["tape"].read_bytes()
    s["tape"].write_text("changed after build")
    with pytest.raises(FixingError, match="changed before archival"):
        archive_sources(artifact, s, store)
    s["tape"].write_bytes(stored.read_bytes())
    stored.write_text("tampered archive")
    with pytest.raises(FixingError, match="archive is corrupt"):
        archive_sources(artifact, s, store)


def test_declared_default_cannot_disappear_on_next_day(inputs, tmp_path):
    c, m, s = inputs
    c["constituents"][0]["status"] = "default_rule_pending"
    first = build_fixing(c, m, s); persist_fixing(first, tmp_path)
    c["observation_date"] = "2026-11-28"
    c["published_at"] = "2026-11-28T12:10:00Z"
    c["constituents"][0]["status"] = "accepted"
    with pytest.raises(FixingError, match="Unresolved default must persist"):
        persist_fixing(build_fixing(c, m, s), tmp_path)
    c["constituents"][0]["status"] = "default_rule_pending"
    assert load_fixing(persist_fixing(build_fixing(c, m, s), tmp_path))["value"] is None


def test_declared_default_cannot_be_erased_by_same_date_correction(inputs, tmp_path):
    c, m, s = inputs
    c["constituents"][0]["status"] = "default_rule_pending"
    first = build_fixing(c, m, s); persist_fixing(first, tmp_path)
    c["published_at"] = "2026-11-28T12:10:00Z"
    c["constituents"][0]["status"] = "accepted"
    with pytest.raises(FixingError, match="Unresolved default must persist"):
        persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)


def test_retry_original_before_later_default_is_idempotent(inputs, tmp_path):
    c, m, s = inputs
    first = build_fixing(c, m, s); path = persist_fixing(first, tmp_path)
    c["constituents"][0]["status"] = "default_rule_pending"
    c["published_at"] = "2026-11-28T12:10:00Z"
    persist_fixing(build_fixing(c, m, s, supersedes=first["artifact_sha256"]), tmp_path)
    assert persist_fixing(first, tmp_path) == path
