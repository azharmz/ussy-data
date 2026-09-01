from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from bootstrap_ohlcv import make_s3_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit whether Musaffa screening was refreshed around the latest earnings event")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--universe-dir", default="universe")
    return parser.parse_args()


def parse_date(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed


def classify(record: dict[str, Any], snapshot_cutoff: pd.Timestamp) -> tuple[str, str]:
    earnings = parse_date(record.get("next_earnings_date"))
    updated = max(
        (date for date in (parse_date(record.get("updated_at")), parse_date(record.get("updated_date"))) if date is not None),
        default=None,
    )
    if earnings is None or updated is None:
        return "UNKNOWN", "Musaffa did not provide both an earnings date and a screening update timestamp"
    if earnings >= snapshot_cutoff:
        return "AWAITING_NEXT_EARNINGS", "The recorded earnings event is after the snapshot date"
    if updated >= earnings:
        return "POST_EARNINGS_REFRESHED", "Musaffa update timestamp is on or after the recorded earnings event"
    return "POTENTIALLY_STALE", "The recorded earnings event is newer than Musaffa's update timestamp"


def main() -> None:
    args = parse_args()
    path = Path(args.universe_dir) / f"musaffa_security_master_{args.snapshot_date}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    snapshot_date = parse_date(args.snapshot_date)
    if snapshot_date is None:
        raise ValueError(f"Invalid snapshot date: {args.snapshot_date}")
    snapshot_cutoff = snapshot_date.normalize() + pd.Timedelta(days=1)

    records = []
    for source in payload.get("records", []):
        status, reason = classify(source, snapshot_cutoff)
        records.append(
            {
                "security_id": source.get("security_id"),
                "ticker": source.get("ticker"),
                "sharia_compliance": source.get("sharia_compliance"),
                "musaffaHalalRating": source.get("musaffaHalalRating"),
                "screening_updated_at": source.get("updated_at"),
                "screening_updated_date": source.get("updated_date"),
                "recorded_earnings_date": source.get("next_earnings_date"),
                "freshness_status": status,
                "reason": reason,
            }
        )

    statuses = ("POST_EARNINGS_REFRESHED", "AWAITING_NEXT_EARNINGS", "POTENTIALLY_STALE", "UNKNOWN")
    summary = {status.lower(): sum(row["freshness_status"] == status for row in records) for status in statuses}
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": args.snapshot_date,
        "method": "timestamp_comparison_not_independent_financial_statement_verification",
        "disclaimer": "Musaffa does not expose the financial-report period in this dataset. This audit compares its update timestamps with the recorded earnings date and cannot prove which filing was used.",
        "securities_audited": len(records),
        **summary,
        "records": records,
    }

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    key = f"universe/freshness/{args.snapshot_date}.json"
    latest_key = "universe/freshness/latest.json"
    body = json.dumps(report, indent=2).encode()
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=latest_key, Body=body, ContentType="application/json")

    concise = {key: value for key, value in report.items() if key != "records"}
    print(json.dumps(concise, indent=2))
    print(f"Freshness report: {key}")
    if summary["potentially_stale"]:
        print(f"::warning title=Potentially stale Musaffa screenings::{summary['potentially_stale']} records have an earnings date newer than the Musaffa update timestamp")
    if summary["unknown"]:
        print(f"::warning title=Unknown screening freshness::{summary['unknown']} records lack comparable timestamps")


if __name__ == "__main__":
    main()

