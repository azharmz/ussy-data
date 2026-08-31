from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config


KNOWN_DELISTED = {
    "IE00BDGMC594": "Acquired by Alkermes; AVDL delisted in February 2026",
    "IL0010823123": "Magic Software merger; MGIC delisted in February 2026",
    "IL0011334468": "Acquired by Palo Alto Networks; CYBR delisted in February 2026",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit OHLCV bootstrap objects and build a repair queue")
    parser.add_argument("--snapshot-date", required=True)
    return parser.parse_args()


def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def read_json(s3, bucket: str, key: str) -> dict[str, Any]:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def main() -> None:
    args = parse_args()
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = make_s3_client()

    membership_key = f"universe/membership/{args.snapshot_date}.json"
    membership = read_json(s3, bucket, membership_key)["records"]
    confirmed = {
        str(row["security_id"]): row
        for row in membership
        if row.get("sharia_compliance") == "COMPLIANT"
        and row.get("musaffaHalalRating") == "COMPLIANT"
    }

    parquet_keys = list_keys(s3, bucket, "backtest/ohlcv/")
    available = {
        key.removeprefix("backtest/ohlcv/").removesuffix(".parquet")
        for key in parquet_keys
        if key.endswith(".parquet")
    }

    failed_by_id: dict[str, dict[str, Any]] = {}
    manifest_prefix = f"backtest/manifests/bootstrap/{args.snapshot_date}/"
    manifest_keys = [key for key in list_keys(s3, bucket, manifest_prefix) if key.endswith(".json")]
    for key in manifest_keys:
        manifest = read_json(s3, bucket, key)
        for result in manifest.get("results", []):
            if result.get("status") == "failed" and result.get("security_id"):
                failed_by_id[str(result["security_id"])] = {**result, "manifest_key": key}

    missing = sorted(set(confirmed) - available)
    repair_queue = []
    for security_id in missing:
        member = confirmed[security_id]
        failure = failed_by_id.get(security_id, {})
        known_reason = KNOWN_DELISTED.get(security_id)
        category = "delisted_or_merger" if known_reason else "retry_required"
        repair_queue.append(
            {
                "security_id": security_id,
                "ticker": member.get("ticker"),
                "yahoo_ticker": failure.get("yahoo_ticker"),
                "category": category,
                "error": failure.get("error", "Parquet missing; no failed manifest record found"),
                "known_reason": known_reason,
                "manifest_key": failure.get("manifest_key"),
            }
        )

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": args.snapshot_date,
        "confirmed_compliant": len(confirmed),
        "confirmed_with_parquet": len(set(confirmed) & available),
        "confirmed_missing_parquet": len(missing),
        "all_parquet_objects": len(available),
        "extra_historical_objects": len(available - set(confirmed)),
        "manifest_files_scanned": len(manifest_keys),
        "repair_categories": {
            category: sum(item["category"] == category for item in repair_queue)
            for category in ("delisted_or_merger", "retry_required")
        },
    }
    queue_document = {**summary, "records": repair_queue}

    queue_key = f"backtest/manifests/bootstrap/{args.snapshot_date}/repair_queue.json"
    summary_key = f"backtest/manifests/bootstrap/{args.snapshot_date}/audit_summary.json"
    s3.put_object(Bucket=bucket, Key=queue_key, Body=json.dumps(queue_document, indent=2).encode(), ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=summary_key, Body=json.dumps(summary, indent=2).encode(), ContentType="application/json")

    print(json.dumps(summary, indent=2))
    print(f"Repair queue: {queue_key}")
    if missing:
        print(f"::warning title=OHLCV repair queue::{len(missing)} confirmed-compliant securities need review")


if __name__ == "__main__":
    main()

