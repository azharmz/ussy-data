#!/usr/bin/env python3
"""
publish_web_status.py

Reads a small set of existing R2 objects produced by the USSY production
pipeline and publishes a single, safe, read-only summary object:

    web/status.json

ADDITIVE ONLY: never writes to, modifies, or deletes any universe/,
backtest/, or production/ object. Never touches Parquet. Uses only the
standard library plus boto3 (already required elsewhere in this project)
-- no new dependencies.

Required environment variables:
    R2_ENDPOINT             R2 S3 endpoint
    R2_ACCESS_KEY_ID        R2 access key
    R2_SECRET_ACCESS_KEY    R2 secret key
    R2_BUCKET_NAME          Bucket name

Usage:
    python src/publish_web_status.py [--local-only]

    --local-only    Write web/status.json to disk but skip the R2 upload.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from botocore.exceptions import ClientError

    from bootstrap_ohlcv import make_s3_client
except ImportError:  # pragma: no cover
    print("ERROR: boto3 is required but not installed. No new dependency "
          "is being introduced -- boto3 is expected to already be "
          "available in this project's environment.", file=sys.stderr)
    raise

SCHEMA_VERSION = 1
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATUS_PATH = WEB_DIR / "status.json"

REVIEW_CATEGORIES = {
    "POTENTIALLY_STALE",
    "UNKNOWN",
    "DATA_UNAVAILABLE",
    "INSUFFICIENT_HISTORY",
    "DAILY_UPDATE_FAILED",
}


class MissingObjectError(RuntimeError):
    """Raised when a required R2 object is missing or unreadable."""


class SchemaError(RuntimeError):
    """Raised when a required R2 object exists but is malformed."""


def _get_json(client, bucket: str, key: str) -> dict[str, Any]:
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            raise MissingObjectError(f"Required object not found: {key}") from exc
        raise RuntimeError(f"Could not read {key}") from exc
    try:
        payload = json.loads(obj["Body"].read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SchemaError(f"Object {key} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaError(f"Object {key} must contain a JSON object")
    return payload


def _get_json_optional(client, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return _get_json(client, bucket, key)
    except MissingObjectError:
        return None


# ---------------------------------------------------------------------------
# Snapshot / active date
# ---------------------------------------------------------------------------

def _extract_active_snapshot_date(current_doc: dict[str, Any]) -> str:
    for key in ("snapshot_date", "active_snapshot_date", "date"):
        if key in current_doc and current_doc[key]:
            return str(current_doc[key])
    raise SchemaError(
        "universe/current.json did not contain a recognizable snapshot "
        "date field (tried: snapshot_date, active_snapshot_date, date)"
    )


def _check_snapshot_consistency(snapshot_date: str,
                                 readiness_doc: dict[str, Any]) -> bool:
    return readiness_doc.get("snapshot_date") == snapshot_date


# ---------------------------------------------------------------------------
# Universe counts -- sourced from production/rolling/readiness.json
# ---------------------------------------------------------------------------

def _extract_universe_counts(readiness_doc: dict[str, Any]) -> dict[str, Any]:
    def num(key: str) -> int:
        val = readiness_doc.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise SchemaError(f"readiness.{key} must be a non-negative integer")
        return val

    return {
        "confirmed_compliant": num("confirmed_compliant"),
        "included_in_rolling": num("included_in_rolling"),
        "ready": num("ready"),
        "insufficient_history": num("insufficient_history"),
        "data_unavailable": num("data_unavailable"),
        "rolling_rows": num("rolling_rows"),
        "rolling_bars_target": num("rolling_bars_target"),
        "minimum_ready_bars": num("minimum_ready_bars"),
    }


# ---------------------------------------------------------------------------
# Universe changes
# ---------------------------------------------------------------------------

def _extract_changes(changes_doc: dict[str, Any],
                      previous_snapshot_date: str | None) -> dict[str, Any]:
    added = changes_doc.get("added")
    removed = changes_doc.get("removed")
    ticker_changes = changes_doc.get("ticker_changes")
    if not all(isinstance(value, list) for value in (added, removed, ticker_changes)):
        raise SchemaError("changes object must contain added, removed, and ticker_changes arrays")
    return {
        "previous_snapshot_date": changes_doc.get("previous_snapshot_date",
                                                    previous_snapshot_date),
        "added_count": len(added),
        "removed_count": len(removed),
        "ticker_changes_count": len(ticker_changes),
        "added": added,
        "removed": removed,
        "ticker_changes": ticker_changes,
    }


# ---------------------------------------------------------------------------
# Freshness -- use the file's own top-level summary, don't recompute
# ---------------------------------------------------------------------------

def _extract_freshness_summary(freshness_doc: dict[str, Any]) -> dict[str, int]:
    def num(key: str) -> int:
        value = freshness_doc.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SchemaError(f"freshness.{key} must be a non-negative integer")
        return value

    return {
        "post_earnings_refreshed": num("post_earnings_refreshed"),
        "awaiting_next_earnings": num("awaiting_next_earnings"),
        "potentially_stale": num("potentially_stale"),
        "unknown": num("unknown"),
    }


def _freshness_review_records(freshness_doc: dict[str, Any]) -> list[dict[str, Any]]:
    records = freshness_doc.get("records", [])
    if not isinstance(records, list):
        raise SchemaError("freshness.records must be an array")

    out = []
    for rec in records:
        status = str(rec.get("freshness_status", "")).upper()
        if status in ("POTENTIALLY_STALE", "UNKNOWN"):
            out.append({
                "security_id": rec.get("security_id"),
                "ticker": rec.get("ticker"),
                "category": status,
                "reason": rec.get("reason"),
                "screening_updated_at": (
                    rec.get("screening_updated_at")
                    or rec.get("screening_updated_date")
                ),
                "recorded_earnings_date": rec.get("recorded_earnings_date"),
                "last_market_date": None,
            })
    return out


# ---------------------------------------------------------------------------
# Readiness-based review entries (insufficient history / bootstrap failed)
# ---------------------------------------------------------------------------

def _readiness_review_records(readiness_doc: dict[str, Any],
                               ticker_by_id: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    securities = readiness_doc.get("securities")
    if not isinstance(securities, list):
        raise SchemaError("readiness.securities must be an array")
    sec_by_id = {str(s["security_id"]): s for s in securities if s.get("security_id")}

    insufficient_ids = readiness_doc.get("insufficient_history_security_ids")
    if not isinstance(insufficient_ids, list):
        raise SchemaError("readiness.insufficient_history_security_ids must be an array")
    for sec_id in insufficient_ids:
        sec_id = str(sec_id)
        sec = sec_by_id.get(sec_id, {})
        out.append({
            "security_id": sec_id,
            "ticker": sec.get("ticker") or ticker_by_id.get(sec_id),
            "category": "INSUFFICIENT_HISTORY",
            "reason": (
                f"{sec.get('rolling_bars', '?')} of "
                f"{readiness_doc['minimum_ready_bars']} minimum bars "
                f"({readiness_doc['rolling_bars_target']} target)"
            ),
            "screening_updated_at": None,
            "recorded_earnings_date": None,
            "last_market_date": sec.get("last_date"),
        })

    data_unavailable_ids = readiness_doc.get("data_unavailable_security_ids")
    if not isinstance(data_unavailable_ids, list):
        raise SchemaError("readiness.data_unavailable_security_ids must be an array")
    for sec_id in data_unavailable_ids:
        sec_id = str(sec_id)
        out.append({
            "security_id": sec_id,
            "ticker": ticker_by_id.get(sec_id),
            "category": "DATA_UNAVAILABLE",
            "reason": "No historical OHLCV data available",
            "screening_updated_at": None,
            "recorded_earnings_date": None,
            "last_market_date": None,
        })

    for sec in securities:
        if str(sec.get("update_status", "")) == "stale_after_failure":
            out.append({
                "security_id": sec.get("security_id"),
                "ticker": sec.get("ticker"),
                "category": "DAILY_UPDATE_FAILED",
                "reason": "Daily update failed; data held at last known state",
                "screening_updated_at": None,
                "recorded_earnings_date": None,
                "last_market_date": sec.get("last_date"),
            })

    return out


def _build_ticker_lookup(membership_doc: dict[str, Any]) -> dict[str, str]:
    entries = membership_doc.get("records")
    if not isinstance(entries, list):
        raise SchemaError("membership.records must be an array")
    lookup = {}
    for e in entries or []:
        sec_id = e.get("security_id")
        ticker = e.get("ticker")
        if sec_id and ticker:
            lookup[str(sec_id)] = str(ticker)
    return lookup


def _build_review_queue(freshness_doc: dict[str, Any],
                         readiness_doc: dict[str, Any],
                         membership_doc: dict[str, Any]) -> list[dict[str, Any]]:
    ticker_by_id = _build_ticker_lookup(membership_doc)
    queue = _freshness_review_records(freshness_doc)
    queue += _readiness_review_records(readiness_doc, ticker_by_id)
    queue.sort(key=lambda r: (r["category"], r.get("ticker") or "", r.get("security_id") or ""))
    return queue


def _empty_freshness_summary() -> dict[str, None | bool]:
    return {
        "available": False,
        "post_earnings_refreshed": None,
        "awaiting_next_earnings": None,
        "potentially_stale": None,
        "unknown": None,
    }


# ---------------------------------------------------------------------------
# Pipeline status
# ---------------------------------------------------------------------------

def _determine_pipeline_status(freshness: dict[str, Any], readiness_doc: dict[str, Any]) -> str:
    update_failures = readiness_doc.get("update_failures", 0) or 0
    if not isinstance(update_failures, int) or update_failures < 0:
        raise SchemaError("readiness.update_failures must be a non-negative integer when present")
    if update_failures > 0 or (freshness.get("potentially_stale") or 0) > 0:
        return "DEGRADED"
    return "OPERATIONAL"


# ---------------------------------------------------------------------------
# Build / validate / write
# ---------------------------------------------------------------------------

def build_status_document(client, bucket: str) -> dict[str, Any]:
    current_doc = _get_json(client, bucket, "universe/current.json")
    snapshot_date = _extract_active_snapshot_date(current_doc)

    changes_doc = _get_json(client, bucket, f"universe/changes/{snapshot_date}.json")
    freshness_doc = _get_json_optional(client, bucket, "universe/freshness/latest.json")
    readiness_doc = _get_json(client, bucket, "production/rolling/readiness.json")
    membership_doc = _get_json(client, bucket, f"universe/membership/{snapshot_date}.json")

    if not _check_snapshot_consistency(snapshot_date, readiness_doc):
        raise SchemaError(
            "Snapshot date mismatch: "
            f"current={snapshot_date}, readiness={readiness_doc.get('snapshot_date')}"
        )
    if freshness_doc is not None and freshness_doc.get("snapshot_date") != snapshot_date:
        freshness_doc = None

    universe = _extract_universe_counts(readiness_doc)
    changes = _extract_changes(changes_doc, current_doc.get("previous_snapshot_date"))
    if freshness_doc is None:
        freshness_counts = _empty_freshness_summary()
        review_queue = _readiness_review_records(
            readiness_doc, _build_ticker_lookup(membership_doc)
        )
        review_queue.sort(key=lambda r: (r["category"], r.get("ticker") or "", r.get("security_id") or ""))
    else:
        freshness_counts = {"available": True, **_extract_freshness_summary(freshness_doc)}
        if freshness_doc.get("method") == "recorded_earnings_date_review_only_v2":
            if freshness_doc.get("securities_audited") != universe["confirmed_compliant"]:
                raise SchemaError("Freshness scope differs from confirmed-compliant universe")
            if sum(freshness_counts[key] for key in ("post_earnings_refreshed", "awaiting_next_earnings", "potentially_stale", "unknown")) != universe["confirmed_compliant"]:
                raise SchemaError("Freshness counts do not cover the compliant universe")
        freshness_counts["created_at"] = freshness_doc.get("created_at")
        freshness_counts["live_earnings_verified"] = freshness_doc.get("live_earnings_verified", False)
        review_queue = _build_review_queue(freshness_doc, readiness_doc, membership_doc)
    pipeline_status = _determine_pipeline_status(freshness_counts, readiness_doc)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_date": snapshot_date,
        "pipeline_status": pipeline_status,
        "universe": universe,
        "changes": changes,
        "freshness": freshness_counts,
        "review_queue": review_queue,
    }
    if freshness_doc is None:
        doc["warning"] = "Screening freshness audit is not available for the active snapshot"
    elif freshness_doc.get("disclaimer"):
        doc["warning"] = freshness_doc["disclaimer"]
    return doc


def build_error_document(message: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_date": None,
        "pipeline_status": "ERROR",
        "error_message": message,
        "universe": {
            "confirmed_compliant": None, "included_in_rolling": None,
            "ready": None, "insufficient_history": None,
            "data_unavailable": None, "rolling_rows": None,
            "rolling_bars_target": None, "minimum_ready_bars": None,
        },
        "changes": {
            "previous_snapshot_date": None, "added_count": None,
            "removed_count": None, "ticker_changes_count": None,
            "added": [], "removed": [], "ticker_changes": [],
        },
        "freshness": {
            "available": False,
            "post_earnings_refreshed": None, "awaiting_next_earnings": None,
            "potentially_stale": None, "unknown": None,
        },
        "review_queue": [],
    }


def validate_document(doc: dict[str, Any]) -> None:
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError("Unsupported web status schema version")
    for key in ("universe", "changes", "freshness"):
        if not isinstance(doc.get(key), dict):
            raise SchemaError(f"web status {key} must be an object")
    if not isinstance(doc.get("review_queue"), list):
        raise SchemaError("web status review_queue must be an array")
    for record in doc["review_queue"]:
        if record.get("category") not in REVIEW_CATEGORIES:
            raise SchemaError(f"Unknown review category: {record.get('category')}")


def _print_summary(doc: dict[str, Any]) -> None:
    print("USSY Data Control status generated:")
    print(f"  pipeline_status: {doc['pipeline_status']}")
    print(f"  snapshot_date:   {doc.get('snapshot_date')}")
    print(f"  generated_at:    {doc['generated_at']}")
    u = doc["universe"]
    print(f"  confirmed_compliant={u.get('confirmed_compliant')} "
          f"ready={u.get('ready')} "
          f"data_unavailable={u.get('data_unavailable')}")
    print(f"  review_queue entries: {len(doc.get('review_queue', []))}")
    if doc.get("warning"):
        print(f"  WARNING: {doc['warning']}")


def _write_local(doc: dict[str, Any]) -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true",
                         help="Write web/status.json but skip R2 upload")
    args = parser.parse_args()

    bucket = os.environ["R2_BUCKET_NAME"]

    try:
        client = make_s3_client()
        doc = build_status_document(client, bucket)
    except (MissingObjectError, SchemaError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        doc = build_error_document(str(exc))
        validate_document(doc)
        _write_local(doc)
        _print_summary(doc)
        return 1  # CI surfaces failure; production data untouched

    validate_document(doc)
    _write_local(doc)

    if not args.local_only:
        try:
            client.put_object(
                Bucket="ussy-data-web",
                Key="status.json",
                Body=json.dumps(doc, indent=2).encode("utf-8"),
                ContentType="application/json; charset=utf-8",
                CacheControl="public, max-age=60",
            )
            print("Published: ussy-data-web/status.json")
        except ClientError:
            print(
                "ERROR: failed to publish status.json to ussy-data-web",
                file=sys.stderr,
            )
            return 1

    _print_summary(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
