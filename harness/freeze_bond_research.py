"""Freeze a private bond replay as a dated, immutable research artifact.

--recorded-at is when these local snapshots were observed together. It is NOT
an assertion that the source was fetched then or was available on the market
date. Legacy replay remains retrospective, with point-in-time verification false.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

try:
    from .research_fixing import FixingError, build_fixing, persist_fixing, archive_sources, _time, digest
    from .bond_spreads import issuer_group
except ImportError:
    from research_fixing import FixingError, build_fixing, persist_fixing, archive_sources, _time, digest
    from bond_spreads import issuer_group

ROOT = Path(__file__).resolve().parent.parent


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prepare_bond_fixing(*, replay_dir, trades, universe, treasury, observation_date,
                        recorded_at, published_at, series_id="private-bond-research-v1",
                        calendar=None, default_events=None, supersedes=None, source_archive_dir=None):
    replay_dir = Path(replay_dir)
    calendar = Path(calendar or ROOT / "config/bond_calendar.json")
    default_events = Path(default_events or ROOT / "config/default_events.json")
    manifest_path = replay_dir / "acsi_bond_manifest.json"
    inputs_path = replay_dir / "acsi_bond_inputs_daily.json"
    series_path = replay_dir / "acsi_bond_spreads_daily.json"
    manifest = _read(manifest_path)
    if manifest.get("private_candidate") is not True or manifest.get("publication_eligible") is not False:
        raise FixingError("Adapter accepts explicit private research replay only")
    if manifest.get("method_version") != "bond-research-v1":
        raise FixingError("Unsupported bond method; version a new adapter")
    if _time(manifest["generated_at"]) > _time(recorded_at):
        raise FixingError("Local snapshot observation predates replay generation")
    if hashlib.sha256(Path(trades).read_bytes()).hexdigest() != manifest.get("input_sha256"):
        raise FixingError("Raw snapshot hash does not match replay manifest")
    series_rows = [row for row in _read(series_path) if row["date"] == observation_date]
    if len(series_rows) != 1:
        raise FixingError("Replay must have exactly one requested daily row")
    daily = series_rows[0]
    configured = _read(universe)
    if isinstance(configured, dict):
        configured = configured["bonds"]
    if not isinstance(configured, list) or not configured:
        raise FixingError("Universe must contain bonds")
    groups = [issuer_group(bond) for bond in configured]
    group_count = len(set(groups))
    parts = []
    for number, bond in enumerate(configured, 1):
        # Match native calculator IDs; never serialize the raw security ID/name.
        key = bond.get("id", f"ACSI-BOND-{number:02d}")
        if not key.startswith("ACSI-BOND-"):
            raise FixingError("Expected anonymized native bond IDs")
        parts.append({"id": key, "weight": 1 / group_count / groups.count(issuer_group(bond))})
    if len({part["id"] for part in parts}) != len(parts):
        raise FixingError("Duplicate configured bond alias")
    calendar_config = _read(calendar)
    start = date.fromisoformat(calendar_config["valid_from"])
    finish = date.fromisoformat(calendar_config["valid_through"])
    sessions = []
    day = start
    while day <= finish:
        key = day.isoformat()
        if key in calendar_config.get("open_overrides", []) or (day.weekday() < 5 and key not in calendar_config["closed_dates"]):
            sessions.append(key)
        day += timedelta(days=1)
    implementation_profile = {"method": manifest["method_version"],
                              "minimum_customer_trade_size": manifest["minimum_customer_trade_size"],
                              "minimum_eligible_prints": manifest["minimum_eligible_prints"],
                              "maximum_carry_sessions": manifest["max_carry_sessions"],
                              "code_sha256": {name: hashlib.sha256((ROOT / "harness" / name).read_bytes()).hexdigest()
                                              for name in ["bond_spreads.py", "bond_provenance.py", "finra_trace.py",
                                                           "research_fixing.py", "freeze_bond_research.py"]}}
    method = {"id": "bond-research-v1-fixed-issuer-level", "version": "fixing-v1",
              "series_kind": "cash_bond_candidate", "unit": "bp", "aggregation": "weighted_mean",
              "implementation_sha256": digest(implementation_profile),
              "constituents": parts,
              "freshness": {"basis": "sessions", "maximum_age": manifest["max_carry_sessions"], "session_dates": sessions}}
    source_files = {"raw_tape": Path(trades), "universe": Path(universe), "treasury": Path(treasury),
                    "calendar": calendar, "default_events": default_events,
                    "calculator": ROOT / "harness/bond_spreads.py",
                    "snapshot_adapter": ROOT / "harness/bond_provenance.py",
                    "snapshot_cleaner": ROOT / "harness/finra_trace.py",
                    "fixing_engine": ROOT / "harness/research_fixing.py",
                    "fixing_adapter": ROOT / "harness/freeze_bond_research.py",
                    "replay_inputs": inputs_path, "replay_series": series_path, "replay_manifest": manifest_path}
    replay_hashes = manifest.get("replay_input_hashes")
    required_replay_hashes = {"raw_tape", "universe", "treasury", "calendar", "default_events", "calculator", "snapshot_adapter", "snapshot_cleaner"}
    if not isinstance(replay_hashes, dict) or not required_replay_hashes.issubset(replay_hashes):
        raise FixingError("Replay lacks full input/code provenance; rerun the native private replay")
    for key in required_replay_hashes:
        if hashlib.sha256(source_files[key].read_bytes()).hexdigest() != replay_hashes[key]:
            raise FixingError(f"Replay input/code hash mismatch: {key}")
    source_ids = sorted(source_files)
    inputs = [row for row in _read(inputs_path) if row["date"] == observation_date]
    if len({row["id"] for row in inputs}) != len(inputs):
        raise FixingError("Duplicate replay constituent")
    expected_weights = {part["id"]: part["weight"] for part in parts}
    constituents = []
    status_map = {"active": "accepted", "carried": "carried", "stale": "stale", "missing": "missing"}
    declared_defaults = _read(default_events).get("events", [])
    defaulted = any(event.get("default_date", "9999-99-99") <= observation_date for event in declared_defaults)
    for row in inputs:
        if row["id"] not in expected_weights or not math.isclose(row["fixed_weight"], expected_weights[row["id"]], abs_tol=1e-12, rel_tol=0):
            raise FixingError("Replay changed the configured universe or weights")
        status = status_map.get(row["status"])
        if status is None:
            raise FixingError("Unknown bond replay status")
        if defaulted or daily["status"] == "default_rule_pending":
            status = "default_rule_pending"
        elif daily["status"] == "missing_treasury":
            status = "source_incomplete"
        constituents.append({"id": row["id"], "value": row["spread_bp"],
                             "observed_date": row["source_date"] or observation_date,
                             "available_at": recorded_at, "status": status, "source_ids": source_ids})
    candidate = {"series_id": series_id, "observation_date": observation_date,
                 "cutoff_at": recorded_at, "published_at": published_at,
                 "constituents": constituents,
                 "sources": [{"id": key, "available_at": recorded_at, "complete": True,
                              "point_in_time_verified": False, "availability_basis": "local_snapshot_observed",
                              "completeness_scope": "local_snapshot", "upstream_feed_complete": None}
                             for key in source_ids]}
    artifact = build_fixing(candidate, method, source_files, supersedes=supersedes)
    replay_value = daily["g_spread_issuer_capped_bp"]
    if (artifact["value"] is None) != (replay_value is None):
        raise FixingError("Replay aggregate availability disagrees with reconstructed fixing")
    if replay_value is not None and not math.isclose(artifact["value"], replay_value, abs_tol=.051, rel_tol=0):
        raise FixingError("Replay aggregate disagrees with independently reconstructed fixed weights")
    if source_archive_dir is not None:
        archive_sources(artifact, source_files, source_archive_dir, candidate=candidate, method=method)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", type=Path, required=True)
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--treasury", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--series-id", default="private-bond-research-v1")
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--default-events", type=Path)
    parser.add_argument("--supersedes")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-archive-dir", type=Path, help="Private input archive; defaults under output-dir")
    args = parser.parse_args()
    try:
        artifact = prepare_bond_fixing(replay_dir=args.replay_dir, trades=args.trades, universe=args.universe,
                                      treasury=args.treasury, observation_date=args.date, recorded_at=args.recorded_at,
                                      published_at=args.published_at, series_id=args.series_id, calendar=args.calendar,
                                      default_events=args.default_events, supersedes=args.supersedes,
                                      source_archive_dir=args.source_archive_dir or args.output_dir / "_private_sources")
        path = persist_fixing(artifact, args.output_dir)
    except (FixingError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        parser.exit(2, f"Fixing blocked: {exc}\n")
    print(json.dumps({"artifact": str(path), "artifact_sha256": artifact["artifact_sha256"],
                      "observation_date": artifact["observation_date"], "value": artifact["value"],
                      "calculation_status": artifact["calculation_status"], "settlement_eligible": False}))


if __name__ == "__main__":
    main()
