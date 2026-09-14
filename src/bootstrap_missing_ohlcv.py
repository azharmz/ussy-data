"""Bootstrap full OHLCV history for newly eligible universe members only.

This is the onboarding gate between a membership change and the normal production
update. A security becomes operational only after its immutable full-history
object exists under backtest/ohlcv/<security_id>.parquet and passes normal OHLCV
normalization/QC. Existing histories are never overwritten here.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from datetime import UTC, datetime
from typing import Any

from bootstrap_ohlcv import (
    download_history,
    load_membership,
    make_s3_client,
    normalize_history,
    upload_parquet,
    yahoo_symbol,
)

LOG = logging.getLogger("bootstrap_missing_ohlcv")
HISTORY_PREFIX = "backtest/ohlcv/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap missing full histories for current eligible universe members")
    parser.add_argument("--snapshot-date", default="current")
    parser.add_argument("--max-new-members", type=int, default=50)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--request-delay", type=float, default=2.0)
    return parser.parse_args()


def list_existing_security_ids(s3, bucket: str) -> set[str]:
    ids: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=HISTORY_PREFIX):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if key.endswith(".parquet"):
                ids.add(key.removeprefix(HISTORY_PREFIX).removesuffix(".parquet"))
    return ids


def missing_members(membership: list[dict[str, Any]], existing_ids: set[str]) -> list[dict[str, Any]]:
    return [row for row in membership if str(row["security_id"]) not in existing_ids]


def resolve_snapshot_date(s3, bucket: str, requested: str) -> str:
    if requested != "current":
        return requested
    pointer = json.loads(s3.get_object(Bucket=bucket, Key="universe/current.json")["Body"].read())
    return str(pointer["snapshot_date"])


def main() -> None:
    args = parse_args()
    if args.max_new_members < 1:
        raise ValueError("max-new-members must be >= 1")
    if not 1 <= args.max_retries <= 5:
        raise ValueError("max-retries must be between 1 and 5")
    if not 0 <= args.request_delay <= 10:
        raise ValueError("request-delay must be between 0 and 10 seconds")

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    snapshot_date = resolve_snapshot_date(s3, bucket, args.snapshot_date)
    membership = load_membership(s3, bucket, snapshot_date)
    existing_ids = list_existing_security_ids(s3, bucket)
    missing = missing_members(membership, existing_ids)

    if len(missing) > args.max_new_members:
        raise RuntimeError(
            f"Refusing automatic bootstrap of {len(missing)} missing histories; "
            f"guardrail max-new-members={args.max_new_members}. Review membership change first."
        )

    results: list[dict[str, Any]] = []
    for position, record in enumerate(missing):
        if position and args.request_delay:
            delay = args.request_delay + random.uniform(0, min(0.5, args.request_delay / 4))
            time.sleep(delay)
        security_id = str(record["security_id"])
        ticker = str(record["ticker"])
        symbol = yahoo_symbol(ticker, security_id)
        key = f"{HISTORY_PREFIX}{security_id}.parquet"
        try:
            history = normalize_history(download_history(symbol, args.max_retries), security_id, ticker)
            upload_parquet(s3, bucket, key, history)
            results.append({
                "security_id": security_id,
                "ticker": ticker,
                "provider_symbol": symbol,
                "status": "BOOTSTRAPPED",
                "rows": len(history),
                "first_date": history["date"].iloc[0].date().isoformat(),
                "last_date": history["date"].iloc[-1].date().isoformat(),
                "object_key": key,
            })
            LOG.info("Onboarded %s (%s): %s rows", ticker, security_id, len(history))
        except Exception as exc:
            results.append({
                "security_id": security_id,
                "ticker": ticker,
                "provider_symbol": symbol,
                "status": "UNAVAILABLE",
                "error": str(exc)[:500],
            })
            print(f"::warning title=New-member OHLCV onboarding unavailable::{ticker} ({security_id}): {str(exc)[:300]}")

    summary = {
        "eligible_members": len(membership),
        "existing_histories_before": len(existing_ids),
        "missing_detected": len(missing),
        "bootstrapped": sum(row["status"] == "BOOTSTRAPPED" for row in results),
        "unavailable": sum(row["status"] == "UNAVAILABLE" for row in results),
    }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": snapshot_date,
        "mode": "NEW_MEMBER_FULL_HISTORY_ONBOARDING",
        "max_new_members_guardrail": args.max_new_members,
        "summary": summary,
        "results": results,
    }
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    report_key = f"backtest/manifests/onboarding/{snapshot_date}/run-{run_id}-{attempt}.json"
    s3.put_object(
        Bucket=bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2).encode(),
        ContentType="application/json",
    )
    print(json.dumps({"report_key": report_key, **summary}, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
