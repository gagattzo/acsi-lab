"""Append-only, deterministic research fixings. This is not a settlement oracle.

The input manifest records collection claims; hashing cannot prove those claims or
establish data rights. Outputs remain private research, even when calculated.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

SCHEMA = "aistress-research-fixing-v1"
SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
HASH = re.compile(r"^[a-f0-9]{64}$")
SERIES_KINDS = {"cash_bond_candidate", "cds_research"}
STATUSES = {"accepted", "carried", "missing", "stale", "ambiguous", "solver_domain", "source_incomplete", "default_rule_pending"}
# These are deliberately not caller-set booleans. A research artifact cannot
# certify the wider process merely because a caller supplies approval=true.
RELEASE_BLOCKERS = [
    "research_only_method",
    "commercial_and_derivative_data_rights_not_verified",
    "independent_model_validation_not_approved",
    "manipulation_and_liquidity_policy_not_approved",
    "credit_event_and_recovery_policy_not_approved",
    "publication_and_revision_governance_not_approved",
    "oracle_operations_and_dispute_policy_not_approved",
]


class FixingError(ValueError):
    """An input or existing artifact violates the fixing contract."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                           allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FixingError("Input is not finite JSON") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _keys(value: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - optional:
        raise FixingError(f"Expected fields {sorted(required)}; optional {sorted(optional)}")


def _id(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise FixingError("Identifiers must be short public aliases, not paths or raw security identifiers")
    return value


def _day(value: Any) -> date:
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise FixingError("Expected ISO date YYYY-MM-DD") from exc
    if result.isoformat() != value:
        raise FixingError("Expected ISO date YYYY-MM-DD")
    return result


def _time(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise FixingError("Expected timestamp with timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise FixingError("Timestamp must include timezone")
    return result.astimezone(timezone.utc)


def _utc(value: Any) -> str:
    return _time(value).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FixingError("Expected finite number")
    return float(value)


def _age(observed: date, target: date, freshness: dict) -> int:
    if observed > target:
        raise FixingError("A constituent observation is after the fixing date")
    if freshness["basis"] == "calendar_days":
        return (target - observed).days
    sessions = freshness["session_dates"]
    if observed.isoformat() not in sessions or target.isoformat() not in sessions:
        raise FixingError("Observation outside the method's verified session calendar")
    return sessions.index(target.isoformat()) - sessions.index(observed.isoformat())


def _method(method: dict) -> tuple[dict[str, float], dict]:
    _keys(method, {"id", "version", "series_kind", "unit", "aggregation", "constituents", "freshness", "implementation_sha256"})
    _id(method["id"]); _id(method["version"]); _id(method["unit"])
    if not isinstance(method["implementation_sha256"], str) or not HASH.fullmatch(method["implementation_sha256"]):
        raise FixingError("Method must identify its implementation/profile SHA-256")
    if method["series_kind"] not in SERIES_KINDS or method["aggregation"] != "weighted_mean":
        raise FixingError("Unsupported series or aggregation; do not splice the headline gap into this level series")
    expected = {}
    if not isinstance(method["constituents"], list) or not method["constituents"]:
        raise FixingError("Method must fix a nonempty universe")
    for item in method["constituents"]:
        _keys(item, {"id", "weight"})
        key = _id(item["id"])
        weight = _number(item["weight"])
        if key in expected or weight <= 0:
            raise FixingError("Duplicate constituent or nonpositive weight")
        expected[key] = weight
    if not math.isclose(math.fsum(expected.values()), 1.0, abs_tol=1e-12, rel_tol=0):
        raise FixingError("Method weights must sum to one; missing weights are never redistributed")
    freshness = method["freshness"]
    _keys(freshness, {"basis", "maximum_age"}, {"session_dates"})
    if isinstance(freshness["maximum_age"], bool) or not isinstance(freshness["maximum_age"], int) or freshness["maximum_age"] < 0:
        raise FixingError("Maximum age must be a nonnegative integer")
    if freshness["basis"] == "sessions":
        dates = freshness.get("session_dates")
        if not isinstance(dates, list) or not dates or dates != sorted(set(dates)):
            raise FixingError("Session calendar must contain sorted unique dates")
        for value in dates:
            _day(value)
    elif freshness["basis"] != "calendar_days" or "session_dates" in freshness:
        raise FixingError("Unknown freshness basis")
    return expected, freshness


def build_fixing(candidate: dict, method: dict, source_files: Mapping[str, str | Path], *,
                 supersedes: str | None = None) -> dict:
    """Build a sanitized artifact; never read the system clock or fetch inputs.

    source_files must contain exactly the source IDs declared in candidate.
    File paths and source bytes are hashed locally, never emitted in the result.
    Source timestamps/PIT attestation remain collector claims, not verified facts.
    """
    _keys(candidate, {"series_id", "observation_date", "cutoff_at", "published_at", "constituents", "sources"})
    expected, freshness = _method(method)
    series_id = _id(candidate["series_id"])
    target = _day(candidate["observation_date"])
    cutoff = _time(candidate["cutoff_at"])
    published = _time(candidate["published_at"])
    if cutoff > published or target > cutoff.date():
        raise FixingError("Fixing date must not follow cutoff; publication must not precede cutoff")
    if supersedes is not None and not HASH.fullmatch(supersedes):
        raise FixingError("supersedes must be an artifact SHA-256")
    if not isinstance(candidate["sources"], list) or not candidate["sources"]:
        raise FixingError("Source manifest is required")
    sources = {}
    release_reasons = list(RELEASE_BLOCKERS)
    for source in candidate["sources"]:
        _keys(source, {"id", "available_at", "complete", "point_in_time_verified"}, {"availability_basis", "completeness_scope", "upstream_feed_complete"})
        key = _id(source["id"])
        if key in sources or key not in source_files:
            raise FixingError("Duplicate or missing source snapshot")
        if type(source["complete"]) is not bool or type(source["point_in_time_verified"]) is not bool:
            raise FixingError("Source completeness and PIT flags must be booleans")
        scope = source.get("completeness_scope", "source_feed")
        upstream = source.get("upstream_feed_complete", source["complete"] if scope == "source_feed" else None)
        if scope not in {"source_feed", "local_snapshot"}:
            raise FixingError("Unknown completeness scope")
        if scope == "local_snapshot" and upstream is not None:
            raise FixingError("A local snapshot does not establish upstream feed completeness")
        if scope == "source_feed" and (type(upstream) is not bool or upstream != source["complete"]):
            raise FixingError("Source feed completeness claims must agree")
        if scope == "local_snapshot":
            release_reasons.append(f"upstream_feed_completeness_unverified:{key}")
        basis = source.get("availability_basis", "collector_claim")
        if basis not in {"collector_claim", "local_snapshot_observed"}:
            raise FixingError("Unknown availability basis")
        if basis == "local_snapshot_observed" and source["point_in_time_verified"]:
            raise FixingError("Local snapshot observation cannot establish historical point-in-time availability")
        if _time(source["available_at"]) > cutoff:
            raise FixingError("Source was unavailable at the declared cutoff")
        raw_path = Path(source_files[key])
        if not raw_path.is_file():
            raise FixingError("Source snapshot is not a readable regular file")
        content_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        sources[key] = {"id": key, "sha256": content_hash, "available_at": _utc(source["available_at"]),
                        "complete": source["complete"], "point_in_time_verified": source["point_in_time_verified"],
                        "availability_basis": basis, "completeness_scope": scope, "upstream_feed_complete": upstream}
        if not source["point_in_time_verified"]:
            release_reasons.append(f"source_point_in_time_unverified:{key}")
    if set(sources) != set(source_files):
        raise FixingError("Unexpected source snapshot")
    if not isinstance(candidate["constituents"], list):
        raise FixingError("Constituents must be a list")
    supplied = {}
    for item in candidate["constituents"]:
        _keys(item, {"id", "value", "observed_date", "available_at", "status", "source_ids"})
        key = _id(item["id"])
        if key not in expected or key in supplied:
            raise FixingError("Unknown or duplicate constituent")
        if item["status"] not in STATUSES:
            raise FixingError("Unknown constituent status")
        if not isinstance(item["source_ids"], list) or not item["source_ids"] or len(set(item["source_ids"])) != len(item["source_ids"]):
            raise FixingError("Each constituent must reference distinct source IDs")
        if any(key not in sources for key in item["source_ids"]):
            raise FixingError("Unknown source reference")
        supplied[key] = item
    parts, reasons = [], []
    for key, weight in sorted(expected.items()):
        item = supplied.get(key)
        if item is None:
            parts.append({"id": key, "weight": weight, "value": None, "observed_date": None,
                          "available_at": None, "age": None, "status": "missing", "source_ids": []})
            reasons.append(f"missing:{key}")
            continue
        observed = _day(item["observed_date"])
        available = _time(item["available_at"])
        if available > cutoff or observed > available.date():
            raise FixingError("Constituent was unavailable at cutoff or predates its observation")
        if any(available < _time(sources[s]["available_at"]) for s in item["source_ids"]):
            raise FixingError("Constituent cannot be available before its source snapshot")
        age = _age(observed, target, freshness)
        value = None if item["value"] is None else _number(item["value"])
        status = item["status"]
        if status == "accepted" and age > 0:
            # A held price must never masquerade as a same-day observation.
            status = "carried"
        if status in {"accepted", "carried"} and age > freshness["maximum_age"]:
            status = "stale"
        if status in {"accepted", "carried"} and value is None:
            status = "missing"
        if status in {"accepted", "carried"} and any(not sources[s]["complete"] for s in item["source_ids"]):
            status = "source_incomplete"
        if status not in {"accepted", "carried"}:
            reasons.append(f"{status}:{key}")
            value = None
        parts.append({"id": key, "weight": weight, "value": value,
                      "observed_date": observed.isoformat(), "available_at": _utc(item["available_at"]),
                      "age": age, "status": status, "source_ids": sorted(item["source_ids"])})
    # An undeclared extra incomplete source cannot disappear from the quality gate.
    reasons.extend(f"source_incomplete:{s}" for s in sorted(sources) if not sources[s]["complete"])
    reasons = sorted(set(reasons))
    value = None if reasons else math.fsum(part["weight"] * part["value"] for part in parts)
    if value is not None and not math.isfinite(value):
        raise FixingError("Aggregate overflow")
    payload = {
        "schema_version": SCHEMA,
        "series_id": series_id,
        "series_kind": method["series_kind"],
        "observation_date": target.isoformat(),
        "cutoff_at": _utc(candidate["cutoff_at"]),
        "published_at": _utc(candidate["published_at"]),
        "method": {"id": method["id"], "version": method["version"], "sha256": digest(method),
                   "unit": method["unit"], "aggregation": method["aggregation"], "freshness_basis": freshness["basis"],
                   "implementation_sha256": method["implementation_sha256"]},
        "candidate_sha256": digest(candidate),
        "sources": [sources[key] for key in sorted(sources)],
        "constituents": parts,
        "value": value,
        "calculation_status": "blocked" if reasons else "calculated",
        "calculation_blockers": reasons,
        "fresh_weight": math.fsum(p["weight"] for p in parts if p["status"] == "accepted"),
        "carried_weight": math.fsum(p["weight"] for p in parts if p["status"] == "carried"),
        "publication_authorized": False,
        "settlement_eligible": False,
        "release_blockers": sorted(set(release_reasons)),
        "supersedes": supersedes,
    }
    return {**payload, "artifact_sha256": digest(payload)}


def verify_fixing(artifact: dict) -> None:
    if not isinstance(artifact, dict) or artifact.get("schema_version") != SCHEMA:
        raise FixingError("Unsupported fixing schema")
    if artifact.get("publication_authorized") is not False or artifact.get("settlement_eligible") is not False:
        raise FixingError("Research fixings cannot claim release or settlement authorization")
    payload = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    if artifact.get("artifact_sha256") != digest(payload):
        raise FixingError("Artifact content hash mismatch")


def load_fixing(path: str | Path) -> dict:
    try:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixingError("Unreadable fixing artifact") from exc
    verify_fixing(artifact)
    return artifact


def persist_fixing(artifact: dict, output_dir: str | Path) -> Path:
    """Append a vintage under an exclusive process lock; never replace a file.

    Corrections must point to the latest artifact and use a later publication
    time. Method changes require a different series_id, preventing spliced history.
    """
    verify_fixing(artifact)
    series = Path(output_dir) / _id(artifact["series_id"])
    directory = series / _day(artifact["observation_date"]).isoformat()
    series.mkdir(parents=True, exist_ok=True)
    # Lock all dates of a series, since the method is fixed across dates.
    with (series / ".write.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        unresolved_defaults = set()
        for previous_file in sorted(series.glob("????-??-??/*.json")):
            previous = load_fixing(previous_file)
            if previous["series_id"] != artifact["series_id"] or previous["method"]["sha256"] != artifact["method"]["sha256"]:
                raise FixingError("Method changes require a new series_id")
            if previous["observation_date"] <= artifact["observation_date"]:
                unresolved_defaults.update(part["id"] for part in previous["constituents"]
                                           if part["status"] == "default_rule_pending")
        directory.mkdir(exist_ok=True)
        prior_files = sorted(directory.glob("*.json"))
        prior = []
        for sequence, path in enumerate(prior_files, 1):
            previous = load_fixing(path)
            expected_name = f"{sequence:06d}-{previous['artifact_sha256']}.json"
            expected_parent = prior[-1]["artifact_sha256"] if prior else None
            if path.name != expected_name or previous["supersedes"] != expected_parent:
                raise FixingError("Existing vintage chain is broken")
            if previous["observation_date"] != artifact["observation_date"]:
                raise FixingError("Existing vintage date mismatch")
            prior.append(previous)
        for path, previous in zip(prior_files, prior):
            if previous["artifact_sha256"] == artifact["artifact_sha256"]:
                return path  # Exact retry of any historical vintage is harmless.
        current_status = {part["id"]: part["status"] for part in artifact["constituents"]}
        if any(current_status.get(key) != "default_rule_pending" for key in unresolved_defaults):
            raise FixingError("Unresolved default must persist; a new approved event policy requires a new series")
        expected_parent = prior[-1]["artifact_sha256"] if prior else None
        if artifact["supersedes"] != expected_parent:
            raise FixingError("Correction must supersede the latest vintage; original must have no parent")
        if prior and (_time(artifact["published_at"]) <= _time(prior[-1]["published_at"]) or
                      _time(artifact["cutoff_at"]) < _time(prior[-1]["cutoff_at"])):
            raise FixingError("Correction must be published later and cannot roll its information cutoff backward")
        final = directory / f"{len(prior) + 1:06d}-{artifact['artifact_sha256']}.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(canonical_bytes(artifact)); handle.flush(); os.fsync(handle.fileno())
            os.link(temporary, final)  # Atomic create; unlike rename, cannot overwrite.
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()
        return final


def archive_sources(artifact: dict, source_files: Mapping[str, str | Path], archive_dir: str | Path, *,
                    candidate: dict | None = None, method: dict | None = None) -> None:
    """Retain exact inputs in a PRIVATE content-addressed store, never in artifact JSON.

    Refuses a snapshot that changed since build_fixing. Existing copies are
    rehashed; no raw bytes are printed. The store must not be served publicly.
    """
    verify_fixing(artifact)
    expected = {source["id"]: source["sha256"] for source in artifact["sources"]}
    if set(expected) != set(source_files):
        raise FixingError("Archive source IDs do not match fixing")
    directory = Path(archive_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshots = [(key, sha, Path(source_files[key]).read_bytes()) for key, sha in expected.items()]
    if candidate is not None:
        snapshots.append(("canonical_candidate", artifact["candidate_sha256"], canonical_bytes(candidate)))
    if method is not None:
        snapshots.append(("canonical_method", artifact["method"]["sha256"], canonical_bytes(method)))
    for key, sha, raw in snapshots:
        if hashlib.sha256(raw).hexdigest() != sha:
            raise FixingError(f"Source changed before archival: {key}")
        final = directory / sha
        if final.exists():
            if hashlib.sha256(final.read_bytes()).hexdigest() != sha:
                raise FixingError("Existing private source archive is corrupt")
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            try:
                os.link(temporary, final)
            except FileExistsError:
                if hashlib.sha256(final.read_bytes()).hexdigest() != sha:
                    raise FixingError("Concurrent private source archive is corrupt")
        finally:
            if temporary:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()

    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--method", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--supersedes")
    parser.add_argument("--source-archive-dir", type=Path, help="PRIVATE content-addressed input store; defaults under output-dir")
    args = parser.parse_args()
    sources = {}
    for argument in args.source:
        if "=" not in argument:
            parser.error("--source must be ID=PATH")
        key, path = argument.split("=", 1)
        if key in sources:
            parser.error("Duplicate source ID")
        sources[key] = Path(path)
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        method = json.loads(args.method.read_text(encoding="utf-8"))
        artifact = build_fixing(candidate, method, sources, supersedes=args.supersedes)
        archive_sources(artifact, sources, args.source_archive_dir or args.output_dir / "_private_sources", candidate=candidate, method=method)
        result = persist_fixing(artifact, args.output_dir)
    except (FixingError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Fixing blocked: {exc}\n")
    print(json.dumps({"artifact": str(result), "artifact_sha256": artifact["artifact_sha256"],
                      "calculation_status": artifact["calculation_status"], "settlement_eligible": False}))


if __name__ == "__main__":
    main()
