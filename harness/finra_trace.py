"""Collect auditable public FINRA portal snapshots for internal research.

The portal rows lack transaction/correction linkage IDs. They are NOT a
reconstructed TRACE event tape. Archive every distinct full response; never
identify a trade by price/time/size or claim cancellations have been applied.
See FINRA ADDS guide https://www.finra.org/sites/default/files/AppSupportDoc/p123822.pdf
for the separate event-feed correction model; its codes cannot be assumed to
have the same meaning as this portal's observed M/O/N status codes.
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
import pathlib
import re
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE_DATA = pathlib.Path(os.environ.get("ACSI_PRIVATE_DATA_DIR", ROOT / "private_data"))
SESSION_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/group/Firm/name/ActiveIndividual/dynamiclookup/examCode"
REF_DATA_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/FixedIncomeMarket/name/CorporateAndAgencySecurities"
TRADE_DATA_URL = "https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/FixedIncomeMarket/name/CorporateAndAgencyTradeHistory"
USER_AGENT = "AISTRESS-Research-Collector/2.0"
WINDOW_LIMIT = 500
RETRY_HTTP = {408, 425, 429, 500, 502, 503, 504}
OBSERVED_PORTAL_STATUSES = {"M", "O", "N"}  # Observed, NOT a lifecycle-code mapping.
# Reference and trade schema fields
REF_FIELDS = [
    "cusip",
    "issueSymbolIdentifier",
    "issuerName",
    "couponRate",
    "maturityDate",
    "productSubTypeCode",
    "is144A",
    "isCallable",
    "couponType",
    "industryGroup",
    "moodysRating",
    "standardAndPoorsRating",
    "traceGradeCode",
    "isConvertible",
    "nextCallDate",
    "lastSalePrice",
    "lastSaleYield",
    "lastTradeDate",
    "priceChangeNumber",
    "priceChangePercent",
    "isPerpetual",
]

TRADE_FIELDS = [
    "tradeExecutionDate",
    "tradeExecutionTime",
    "tradeStatus",
    "reportedTradeVolume",
    "lastSalePrice",
    "lastSaleYield",
    "tradeModifierCode",
    "tradeModifier2Code",
    "specialPriceFlag",
    "isAsof",
    "reportingPartySideCode",
    "reportingPartyTypeCode",
    "contraPartyTypeCode",
    "cusip",
    "issueSymbolIdentifier",
    "issuerName",
    "remunerationIndicator",
    "atsFlag",
]


class SourceError(RuntimeError):
    pass


class FinraTraceClient:
    def __init__(self, headless=True):
        self.playwright = None
        self.api_context = None
        self.xsrf_token = None

    async def _request(self, method, url, **kwargs):
        # One retry only on transient failures. HTTP403/401 are access failures,
        # not an invitation to evade access controls or change identities.
        for attempt in range(2):
            try:
                response = await getattr(self.api_context, method)(url, **kwargs)
            except Exception as exc:
                transient = isinstance(exc, (TimeoutError, OSError)) or "Timeout" in type(exc).__name__ or any(
                    code in str(exc) for code in ("EAI_AGAIN", "ECONNRESET", "ETIMEDOUT", "ECONNREFUSED"))
                if attempt == 0 and transient:
                    await asyncio.sleep(1)
                    continue
                raise SourceError("Network request failed") from exc
            if response.status in RETRY_HTTP and attempt == 0:
                await asyncio.sleep(1)
                continue
            if response.status != 200:
                raise SourceError(f"Source returned HTTP {response.status}")
            return response

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        proxy = None
        if proxy_url:
            parsed = urlsplit(proxy_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{port}"}
            if parsed.username: proxy["username"] = unquote(parsed.username)
            if parsed.password: proxy["password"] = unquote(parsed.password)
        self.api_context = await self.playwright.request.new_context(
            proxy=proxy, user_agent=USER_AGENT, timeout=30_000,
            extra_http_headers={"Accept": "application/json"})
        await self._request("get", SESSION_URL)
        storage = await self.api_context.storage_state()
        self.xsrf_token = next((c["value"] for c in storage.get("cookies", []) if c["name"] == "XSRF-TOKEN"), None)
        if not self.xsrf_token:
            raise SourceError("Source session token unavailable")
        # Never print token or cookie values, even in test mode.
        print("FINRA public session initialized")

    async def _records(self, url, identifier, fields, limit):
        payload = {
            "fields": fields,
            "orFilters": [{"compareFilters": [
                {"fieldName": field, "fieldValue": identifier, "compareType": "EQUAL"}
                for field in ("cusip", "issueSymbolIdentifier")]}],
            "offset": 0, "limit": limit,
        }
        if url == TRADE_DATA_URL:
            payload["sortFields"] = ["-tradeExecutionDate", "-tradeExecutionTime"]
        response = await self._request("post", url, headers={"X-XSRF-TOKEN": self.xsrf_token,
                                       "Content-Type": "application/json"}, data=json.dumps(payload))
        body = await response.json()
        raw = body.get("returnBody", {}).get("data")
        if raw is None:
            raise SourceError("Response missing records field")
        records = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise SourceError("Unexpected records schema")
        return records

    async def fetch_reference(self, identifier):
        records = await self._records(REF_DATA_URL, identifier, REF_FIELDS, 1)
        if len(records) != 1:
            raise SourceError("Reference record missing or ambiguous")
        return records[0]

    async def fetch_trades(self, identifier, limit=WINDOW_LIMIT):
        # Offset is not evidence of an unrestricted archive. The public portal
        # has a bounded history; record truncation instead of promising backfill.
        return await self._records(TRADE_DATA_URL, identifier, TRADE_FIELDS, limit)

    async def close(self):
        if self.api_context:
            await self.api_context.dispose()
        if self.playwright:
            await self.playwright.stop()


def atomic_write(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = pathlib.Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def write_json(path, obj):
    atomic_write(path, (json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def write_parquet(path, df):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as f:
        temp = pathlib.Path(f.name)
    try:
        df.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def parse_volume(raw):
    # Dissemination caps are lower bounds, not exact weights.
    value = str(raw).strip().upper().replace(",", "")
    capped = value.endswith("+")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(MM|M|K)?\+?", value)
    if not match:
        return float("nan"), capped
    multiplier = {None: 1, "MM": 1_000_000, "M": 1_000_000, "K": 1_000}[match[2]]
    return float(match[1]) * multiplier, capped


def clean_trades(df):
    """Normalize a snapshot without pretending to reconstruct lifecycle events.

    Identical rows can be separate trades. Preserve their multiplicity. Unknown
    status codes are flagged for review, never guessed into a cancel filter.
    """
    out = df.copy()
    if out.empty:
        return out
    for name in TRADE_FIELDS:
        if name not in out:
            out[name] = None
    parsed = out["reportedTradeVolume"].map(parse_volume)
    out["reportedTradeVolume"] = parsed.map(lambda x: x[0])
    out["isVolumeCapped"] = parsed.map(lambda x: x[1])
    for name in ("lastSalePrice", "lastSaleYield"):
        out[name] = pd.to_numeric(out[name], errors="coerce")
    out["portal_status_recognized"] = out["tradeStatus"].astype(str).str.upper().isin(OBSERVED_PORTAL_STATUSES)
    out["correction_status"] = "unverified_portal_snapshot"
    out["settlement_eligible"] = False
    return out


def archive_snapshot(out_dir, bond_id, rows, fetched_at, previous=None):
    if not rows:
        raise SourceError("Empty trade snapshot")
    required = {"cusip", "tradeExecutionDate", "tradeExecutionTime", "lastSalePrice", "lastSaleYield", "tradeStatus"}
    if any(not required.issubset(r) for r in rows):
        raise SourceError("Snapshot missing required columns")
    cusips = {r["cusip"] for r in rows}
    if len(cusips) != 1 or not all(cusips):
        raise SourceError("Mixed or missing security identifiers")
    dates = sorted({r["tradeExecutionDate"] for r in rows})
    for d in dates:
        datetime.strptime(d, "%Y-%m-%d")
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    relative = pathlib.Path("snapshots") / fetched_at[:10] / f"{bond_id}_{digest}.json.gz"
    target = pathlib.Path(out_dir) / relative
    if not target.exists():
        atomic_write(target, gzip.compress(raw, mtime=0))
    elif gzip.decompress(target.read_bytes()) != raw:
        raise SourceError("Snapshot hash collision")
    # Comparing fingerprints detects loss of overlap, never uniquely identifies trades.
    signatures = {hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest() for r in rows}
    overlap = None
    if previous:
        old = {hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest() for r in previous}
        overlap = len(signatures & old)
    truncated = len(rows) >= WINDOW_LIMIT
    frame = pd.DataFrame(rows)
    frame["source_snapshot_id"] = digest
    frame["source_fetched_at"] = fetched_at
    frame["source_row_ordinal"] = range(len(frame))
    frame["history_boundary_excluded"] = truncated & (frame["tradeExecutionDate"] == dates[0])
    return frame, {
        "id": bond_id, "status": "ok", "snapshot_file": relative.as_posix(),
        "snapshot_sha256": digest, "fetched_at": fetched_at, "records": len(rows),
        "earliest_execution_date": dates[0], "latest_execution_date": dates[-1],
        "truncated_window": truncated, "overlap_fingerprints": overlap,
        "history_quality": "no_overlap_possible_gap" if previous and truncated and overlap == 0 else "bounded_portal_snapshot",
        "correction_status": "unverified_portal_snapshot", "settlement_eligible": False,
    }


async def run_pipeline(universe_path, test_mode=False, output_dir=PRIVATE_DATA, client=None):
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    universe = json.loads(pathlib.Path(universe_path).read_text())
    if not isinstance(universe, list) or not universe:
        raise ValueError("A nonempty universe list is required")
    if test_mode:
        # A partial diagnostic run cannot overwrite full-run materialized inputs.
        out_dir = out_dir / "diagnostic"
        out_dir.mkdir(parents=True, exist_ok=True)
        universe = universe[:2]
    refs_path = out_dir / "bond_reference.json"
    old_refs = json.loads(refs_path.read_text()) if refs_path.exists() else []
    refs = {str(r.get("cusip")): r for r in old_refs}
    old_raw_path = out_dir / "trace_trades_raw.parquet"
    prior = pd.read_parquet(old_raw_path) if old_raw_path.exists() else pd.DataFrame()
    current = prior.copy()
    old_universe_path = out_dir / "bonds_universe.json"
    resolved_prior = json.loads(old_universe_path.read_text()) if old_universe_path.exists() else []
    resolved = {r.get("id") or r.get("trace_symbol") or r.get("cusip"): r for r in resolved_prior}
    fetched_at = datetime.now(timezone.utc).isoformat()
    manifest_path = out_dir / "trace_collection_manifest.json"
    previous_report = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    report = {"schema_version": 3, "attempted_at": fetched_at, "fetched_at": None, "status": "collecting",
              "last_successful_collection_at": previous_report.get("last_successful_collection_at"),
              "correction_status": "unverified_portal_snapshot", "settlement_eligible": False,
              "expected_bonds": len(universe), "bonds": []}
    client = client or FinraTraceClient()
    errors = []
    try:
        await client.start()
        for i, bond in enumerate(universe, 1):
            bond_id = bond.get("id") or f"ACSI-BOND-{i:02d}"
            if not re.fullmatch(r"[A-Za-z0-9_-]+", bond_id):
                raise ValueError("Unsafe public bond ID")
            identifier = bond.get("trace_symbol") or bond.get("issueSymbolIdentifier") or bond.get("cusip")
            if not identifier:
                raise ValueError("Missing query identifier")
            entry = {"id": bond_id, "status": "error"}
            ref = None
            try:
                ref = await client.fetch_reference(identifier)
                if not ref.get("cusip"):
                    raise SourceError("Reference response missing identifier")
                refs[ref["cusip"]] = ref
                private_bond = {**bond, "id": bond_id, "cusip": ref["cusip"]}
                resolved[bond.get("id") or bond.get("trace_symbol") or bond.get("cusip")] = private_bond
                rows = await client.fetch_trades(identifier)
                if any(r.get("cusip") != ref["cusip"] for r in rows):
                    raise SourceError("Reference and trades disagree on security")
                previous = None
                if not prior.empty:
                    old = prior[prior["cusip"] == ref["cusip"]]
                    if len(old):
                        previous = old[[c for c in TRADE_FIELDS if c in old]].where(pd.notna(old), None).to_dict("records")
                frame, entry = archive_snapshot(out_dir, bond_id, rows, fetched_at, previous)
                if not current.empty:
                    current = current[current["cusip"] != ref["cusip"]]
                current = pd.concat([current, frame], ignore_index=True)
                unknown = sorted({str(r.get("tradeStatus")) for r in rows} - OBSERVED_PORTAL_STATUSES)
                if unknown:
                    entry["status"] = "unknown_portal_status"
                    entry["unknown_statuses"] = unknown
                    errors.append(bond_id)
                # Save every independent successful source before moving to another bond.
                write_parquet(old_raw_path, current)
                write_parquet(out_dir / "trace_trades_clean.parquet", clean_trades(current))
            except Exception as exc:
                entry.update(status="error", error=type(exc).__name__)
                if isinstance(exc, SourceError):
                    entry["error_message"] = str(exc)
                errors.append(bond_id)
            write_json(refs_path, list(refs.values()))
            write_json(old_universe_path, list(resolved.values()))
            report["bonds"].append(entry)
            write_json(out_dir / "trace_collection_manifest.json", report)
            print(f"{bond_id}: {entry['status']} ({entry.get('records', 0)} records)")
            if i < len(universe):
                await asyncio.sleep(2)
    except Exception as exc:
        errors.append("session")
        report["session_error"] = type(exc).__name__
        if isinstance(exc, SourceError):
            report["session_error_message"] = str(exc)
    finally:
        try:
            await client.close()
        except Exception:
            errors.append("session_cleanup")
        report["status"] = "incomplete" if errors else "research_snapshot"
        report["failed_bonds"] = errors
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        if not errors:
            report["fetched_at"] = fetched_at
            report["last_successful_collection_at"] = report["completed_at"]
        write_json(out_dir / "trace_collection_manifest.json", report)
        # Immutable run diagnostics, even for failures; no private identifiers in logs.
        run_id = report["completed_at"].replace(":", "").replace("+", "_")
        write_json(out_dir / "runs" / f"{run_id}.json", report)
    if errors:
        raise SourceError(f"Collection incomplete: {len(errors)} source failures; prior inputs retained")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="config/bond_universe.json")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--out", default=str(PRIVATE_DATA))
    args = parser.parse_args()
    asyncio.run(run_pipeline(args.universe, args.test, args.out))


if __name__ == "__main__":
    main()
