from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from dataclasses import asdict
from datetime import UTC, datetime

from bootstrap_ohlcv import (
    Result,
    download_history,
    make_s3_client,
    normalize_history,
    upload_parquet,
    yahoo_symbol,
)


LOG = logging.getLogger("repair_ohlcv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry unresolved OHLCV bootstrap records")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--request-delay", type=float, default=5.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.max_records <= 100:
        raise ValueError("max-records must be between 1 and 100")
    if not 0 <= args.request_delay <= 30:
        raise ValueError("request-delay must be between 0 and 30 seconds")

    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = make_s3_client()
    queue_key = f"backtest/manifests/bootstrap/{args.snapshot_date}/repair_queue.json"
    queue = json.loads(s3.get_object(Bucket=bucket, Key=queue_key)["Body"].read())
    records = [row for row in queue.get("records", []) if row.get("category") == "retry_required"][: args.max_records]
    if not records:
        LOG.info("No retry_required records found")
        return

    results: list[Result] = []
    for position, record in enumerate(records):
        security_id = str(record["security_id"])
        ticker = str(record["ticker"])
        symbol = yahoo_symbol(ticker)
        key = f"backtest/ohlcv/{security_id}.parquet"
        if position and args.request_delay:
            delay = args.request_delay + random.uniform(0, 1)
            LOG.info("Rate-limit delay: %.2fs", delay)
            time.sleep(delay)
        try:
            history = normalize_history(download_history(symbol, 3), security_id, ticker)
            upload_parquet(s3, bucket, key, history)
            result = Result(
                security_id,
                ticker,
                symbol,
                "repaired",
                rows=len(history),
                first_date=history["date"].iloc[0].date().isoformat(),
                last_date=history["date"].iloc[-1].date().isoformat(),
                object_key=key,
            )
            LOG.info("Repaired %s (%s rows)", ticker, len(history))
        except Exception as exc:
            result = Result(security_id, ticker, symbol, "still_unavailable", error=str(exc)[:500])
            LOG.error("Still unavailable %s (%s): %s", ticker, security_id, exc)
            print(f"::warning title=OHLCV still unavailable::{ticker} ({security_id}): {str(exc)[:300]}")
        results.append(result)

    run_id = os.getenv("GITHUB_RUN_ID", "local")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": args.snapshot_date,
        "summary": {
            "repaired": sum(row.status == "repaired" for row in results),
            "still_unavailable": sum(row.status == "still_unavailable" for row in results),
        },
        "results": [asdict(row) for row in results],
    }
    report_key = f"backtest/manifests/bootstrap/{args.snapshot_date}/repair-run-{run_id}-{attempt}.json"
    s3.put_object(Bucket=bucket, Key=report_key, Body=json.dumps(report, indent=2).encode(), ContentType="application/json")
    LOG.info("Repair report: %s", report_key)
    LOG.info("Summary: %s", report["summary"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()


