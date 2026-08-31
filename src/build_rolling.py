from __future__ import annotations

import argparse
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from bootstrap_ohlcv import OHLCV_COLUMNS, make_s3_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build production rolling OHLCV from historical Parquet objects")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--rolling-bars", type=int, default=300)
    parser.add_argument("--minimum-ready-bars", type=int, default=250)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def read_json(s3, bucket: str, key: str) -> dict[str, Any]:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def load_tail(s3, bucket: str, security_id: str, ticker: str, bars: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    key = f"backtest/ohlcv/{security_id}.parquet"
    payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    frame = pd.read_parquet(io.BytesIO(payload), engine="pyarrow")
    missing = set(OHLCV_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"{key} lacks columns: {sorted(missing)}")
    frame = frame[OHLCV_COLUMNS].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last").sort_values("date")
    frame["security_id"] = security_id
    frame["ticker"] = ticker
    tail = frame.tail(bars).reset_index(drop=True)
    if tail.empty:
        raise ValueError(f"{key} contains no valid rows")
    detail = {
        "security_id": security_id,
        "ticker": ticker,
        "available_bars": len(frame),
        "rolling_bars": len(tail),
        "first_rolling_date": tail["date"].iloc[0].date().isoformat(),
        "last_date": tail["date"].iloc[-1].date().isoformat(),
    }
    return tail, detail


def main() -> None:
    args = parse_args()
    if args.rolling_bars < 1:
        raise ValueError("rolling-bars must be positive")
    if not 1 <= args.minimum_ready_bars <= args.rolling_bars:
        raise ValueError("minimum-ready-bars must be between 1 and rolling-bars")
    if not 1 <= args.workers <= 32:
        raise ValueError("workers must be between 1 and 32")

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    membership = read_json(s3, bucket, f"universe/membership/{args.snapshot_date}.json")["records"]
    confirmed = {
        str(row["security_id"]): row
        for row in membership
        if row.get("sharia_compliance") == "COMPLIANT"
        and row.get("musaffaHalalRating") == "COMPLIANT"
    }
    parquet_ids = {
        key.removeprefix("backtest/ohlcv/").removesuffix(".parquet")
        for key in list_keys(s3, bucket, "backtest/ohlcv/")
        if key.endswith(".parquet")
    }
    available_ids = sorted(set(confirmed) & parquet_ids)
    unavailable_ids = sorted(set(confirmed) - parquet_ids)

    frames: list[pd.DataFrame] = []
    details: list[dict[str, Any]] = []
    load_failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(load_tail, s3, bucket, security_id, str(confirmed[security_id]["ticker"]), args.rolling_bars): security_id
            for security_id in available_ids
        }
        for future in as_completed(futures):
            security_id = futures[future]
            try:
                frame, detail = future.result()
                frames.append(frame)
                details.append(detail)
            except Exception as exc:
                load_failures.append(
                    {"security_id": security_id, "ticker": str(confirmed[security_id].get("ticker")), "error": str(exc)[:500]}
                )

    if load_failures:
        raise RuntimeError(f"Failed to load {len(load_failures)} existing Parquet objects: {load_failures[:5]}")
    if not frames:
        raise RuntimeError("No rolling data was built")

    rolling = pd.concat(frames, ignore_index=True)
    rolling = rolling[OHLCV_COLUMNS].sort_values(["security_id", "date"]).reset_index(drop=True)
    duplicate_count = int(rolling.duplicated(subset=["security_id", "date"]).sum())
    if duplicate_count:
        raise ValueError(f"Rolling output contains {duplicate_count} duplicate security/date rows")

    ready = sorted(row["security_id"] for row in details if row["rolling_bars"] >= args.minimum_ready_bars)
    insufficient = sorted(row["security_id"] for row in details if row["rolling_bars"] < args.minimum_ready_bars)
    readiness = {
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": args.snapshot_date,
        "rolling_bars_target": args.rolling_bars,
        "minimum_ready_bars": args.minimum_ready_bars,
        "confirmed_compliant": len(confirmed),
        "included_in_rolling": len(details),
        "ready": len(ready),
        "insufficient_history": len(insufficient),
        "data_unavailable": len(unavailable_ids),
        "rolling_rows": len(rolling),
        "ready_security_ids": ready,
        "insufficient_history_security_ids": insufficient,
        "data_unavailable_security_ids": unavailable_ids,
        "securities": sorted(details, key=lambda row: row["security_id"]),
    }

    buffer = io.BytesIO()
    rolling.to_parquet(buffer, engine="pyarrow", index=False, compression="zstd")
    rolling_key = "production/rolling/latest.parquet"
    readiness_key = "production/rolling/readiness.json"
    s3.put_object(Bucket=bucket, Key=rolling_key, Body=buffer.getvalue(), ContentType="application/vnd.apache.parquet")
    s3.put_object(Bucket=bucket, Key=readiness_key, Body=json.dumps(readiness, indent=2).encode(), ContentType="application/json")

    rolling_head = s3.head_object(Bucket=bucket, Key=rolling_key)
    readiness_head = s3.head_object(Bucket=bucket, Key=readiness_key)
    if rolling_head.get("ContentLength", 0) <= 0 or readiness_head.get("ContentLength", 0) <= 0:
        raise RuntimeError("Rolling output verification failed")
    print(json.dumps({key: value for key, value in readiness.items() if not key.endswith("_ids") and key != "securities"}, indent=2))
    print(f"Verified R2 object: {rolling_key} ({rolling_head['ContentLength']} bytes)")
    print(f"Verified R2 object: {readiness_key} ({readiness_head['ContentLength']} bytes)")


if __name__ == "__main__":
    main()


