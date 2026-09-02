"""Review recorded earnings dates; never change compliance membership.

This is not a live earnings feed. Generic update timestamps are not evidence
that Musaffa screened the most recent financial report.
"""
from __future__ import annotations
from compliance import is_eligible
import argparse
import json
import os
from datetime import UTC, date, datetime
from typing import Any

STATUSES = ("POST_EARNINGS_REFRESHED", "AWAITING_NEXT_EARNINGS", "POTENTIALLY_STALE", "UNKNOWN")
DISCLAIMER = (
    "Based only on earnings dates recorded in the Musaffa snapshot, not a live earnings feed. "
    "A scheduled date passing does not confirm release of a report. Generic update timestamps "
    "do not prove post-earnings screening. Compliance membership is unchanged."
)


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def classify(record: dict[str, Any], snapshot_day: date, as_of: date) -> tuple[str, str]:
    earnings = parse_date(record.get("next_earnings_date"))
    if earnings is None:
        return "UNKNOWN", "No usable earnings date in snapshot; latest earnings are not verified"
    if earnings > as_of:
        return "AWAITING_NEXT_EARNINGS", "Recorded earnings date is in the future; schedule is not independently verified"
    if earnings == as_of:
        return "UNKNOWN", "Recorded earnings date is today; release time and actual release are not verified"
    if earnings >= snapshot_day:
        return "POTENTIALLY_STALE", "Recorded earnings date has passed without evidence of a later screening; review a new Musaffa snapshot"
    updates = [parse_date(record.get(key)) for key in ("updated_at", "updated_date")]
    known = [value for value in updates if value is not None]
    if known and max(known) < earnings:
        return "POTENTIALLY_STALE", "Recorded earnings date is later than available source update timestamps; review required"
    return "UNKNOWN", "Generic source update timestamps do not establish which financial report was screened"


def records_from(payload: dict[str, Any], snapshot: str, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("snapshot_date") != snapshot:
        raise ValueError(f"{label}: snapshot mismatch")
    records = payload.get("records")
    if not isinstance(records, list) or type(payload.get("count")) is not int or payload["count"] != len(records):
        raise ValueError(f"{label}: invalid records/count")
    if any(not isinstance(row, dict) or not row.get("security_id") for row in records):
        raise ValueError(f"{label}: missing security_id")
    ids = [str(row["security_id"]) for row in records]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{label}: duplicate security_id")
    return records


def build_report(membership: dict[str, Any], master: dict[str, Any], snapshot: str,
                 now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    snapshot_day = parse_date(snapshot)
    if snapshot_day is None or snapshot_day > now.date():
        raise ValueError("Invalid or future snapshot date")
    members = records_from(membership, snapshot, "membership")
    sources = records_from(master, snapshot, "security_master")
    source_by_id = {str(row["security_id"]): row for row in sources}
    confirmed = [row for row in members if is_eligible(row)]
    records = []
    for member in sorted(confirmed, key=lambda row: str(row["security_id"])):
        security_id = str(member["security_id"])
        source = source_by_id.get(security_id, {})
        status, reason = classify(source, snapshot_day, now.date())
        if not source:
            reason = "Security absent from matching master; earnings freshness is unknown"
        records.append({
            "security_id": security_id, "ticker": member.get("ticker"),
            "sharia_compliance": member.get("sharia_compliance"),
            "musaffaHalalRating": member.get("musaffaHalalRating"),
            "screening_updated_at": None, "screening_updated_date": None,
            "source_updated_at": source.get("updated_at"),
            "source_updated_date": source.get("updated_date"),
            "recorded_earnings_date": source.get("next_earnings_date"),
            "earnings_release_confirmed": False,
            "freshness_status": status, "reason": reason,
        })
    summary = {status.lower(): sum(row["freshness_status"] == status for row in records) for status in STATUSES}
    return {
        "created_at": now.isoformat(), "as_of_date": now.date().isoformat(),
        "snapshot_date": snapshot, "method": "recorded_earnings_date_review_only_v2",
        "live_earnings_verified": False, "disclaimer": DISCLAIMER,
        "securities_audited": len(records), "confirmed_compliant": len(confirmed),
        **summary, "records": records,
    }


def read_json(s3, bucket: str, key: str) -> dict[str, Any]:
    payload = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {key}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-date", default="current")
    args = parser.parse_args()
    from bootstrap_ohlcv import make_s3_client
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    current = read_json(s3, bucket, "universe/current.json")
    snapshot = current.get("snapshot_date") if args.snapshot_date == "current" else args.snapshot_date
    if not isinstance(snapshot, str) or parse_date(snapshot) is None:
        raise ValueError("Invalid active snapshot_date")
    membership = read_json(s3, bucket, f"universe/membership/{snapshot}.json")
    master = read_json(s3, bucket, f"universe/security_master/{snapshot}.json")
    report = build_report(membership, master, snapshot)
    if current.get("snapshot_date") == snapshot and current.get("confirmed_compliant") != report["confirmed_compliant"]:
        raise ValueError("Active pointer and membership counts differ")
    if read_json(s3, bucket, "universe/current.json").get("snapshot_date") != snapshot:
        raise ValueError("Active snapshot changed; retry after universe update completes")
    body = json.dumps(report, indent=2).encode()
    for key in (f"universe/freshness/{snapshot}.json", "universe/freshness/latest.json"):
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))
    print("Freshness report: universe/freshness/latest.json")
    print("Membership unchanged. Latest earnings releases have NOT been fetched or confirmed.")


if __name__ == "__main__":
    main()
