from __future__ import annotations
from bootstrap_ohlcv import load_membership

import argparse
import json
import logging
import os
import random
import time

from botocore.exceptions import ClientError

from bootstrap_ohlcv import download_history, make_s3_client, normalize_history, upload_parquet, yahoo_symbol


LOG = logging.getLogger("bootstrap_added")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap newly added confirmed-compliant securities")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--request-delay", type=float, default=3.0)
    return parser.parse_args()


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def main() -> None:
    args = parse_args()
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    change_key = f"universe/changes/{args.snapshot_date}.json"
    change = json.loads(s3.get_object(Bucket=bucket, Key=change_key)["Body"].read())
    results = []
    eligible = {str(row['security_id']): row for row in load_membership(s3, bucket, args.snapshot_date)}
    for position, record in enumerate(change.get("added", [])):
        if str(record['security_id']) not in eligible:
            continue
        record = eligible[str(record['security_id'])]
        security_id = str(record["security_id"])
        ticker = str(record["ticker"])
        key = f"backtest/ohlcv/{security_id}.parquet"
        if object_exists(s3, bucket, key):
            results.append({"security_id": security_id, "ticker": ticker, "status": "existing"})
            continue
        if position and args.request_delay:
            time.sleep(args.request_delay + random.uniform(0, 0.5))
        try:
            history = normalize_history(download_history(yahoo_symbol(ticker, security_id), 3), security_id, ticker)
            upload_parquet(s3, bucket, key, history)
            results.append({"security_id": security_id, "ticker": ticker, "status": "uploaded", "rows": len(history)})
            LOG.info("Bootstrapped ADDED security %s (%s rows)", ticker, len(history))
        except Exception as exc:
            results.append({"security_id": security_id, "ticker": ticker, "status": "failed", "error": str(exc)[:500]})
            print(f"::warning title=ADDED security unavailable::{ticker} ({security_id}): {str(exc)[:300]}")

    report = {
        "snapshot_date": args.snapshot_date,
        "summary": {
            "uploaded": sum(row["status"] == "uploaded" for row in results),
            "existing": sum(row["status"] == "existing" for row in results),
            "failed": sum(row["status"] == "failed" for row in results),
        },
        "results": results,
    }
    key = f"universe/changes/{args.snapshot_date}-bootstrap.json"
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(report, indent=2).encode(), ContentType="application/json")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()


