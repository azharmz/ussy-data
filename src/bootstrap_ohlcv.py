from __future__ import annotations
from compliance import is_eligible
from provider_symbols import yahoo_symbol

import argparse
import io
import json
import logging
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
import pandas as pd
import yfinance as yf
from botocore.config import Config
from botocore.exceptions import ClientError

LOG = logging.getLogger("bootstrap_ohlcv")
OHLCV_COLUMNS = ["date", "security_id", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
HISTORY_PREFIX = "history/ohlcv/"
HISTORY_MANIFEST_PREFIX = "history/manifests/"

@dataclass
class Result:
    security_id: str
    ticker: str
    yahoo_ticker: str
    status: str
    rows: int = 0
    first_date: str | None = None
    last_date: str | None = None
    object_key: str | None = None
    error: str | None = None

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap full OHLCV history from Yahoo Finance to R2")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--request-delay", type=float, default=2.0)
    return parser.parse_args()

def validate_args(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.snapshot_date): raise ValueError("snapshot-date must use YYYY-MM-DD")
    if args.start_index < 0: raise ValueError("start-index must be >= 0")
    if not 1 <= args.batch_size <= 100: raise ValueError("batch-size must be between 1 and 100")
    if not 1 <= args.max_retries <= 5: raise ValueError("max-retries must be between 1 and 5")
    if not 0 <= args.request_delay <= 10: raise ValueError("request-delay must be between 0 and 10 seconds")

def make_s3_client():
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))

def load_membership(s3, bucket: str, snapshot_date: str) -> list[dict[str, Any]]:
    key = f"universe/membership/{snapshot_date}.json"
    payload = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    records = payload.get("records")
    if not isinstance(records, list) or payload.get("count") != len(records): raise ValueError(f"Invalid membership payload at {key}")
    required = {"security_id", "ticker"}
    for index, record in enumerate(records):
        if not required.issubset(record) or not all(record.get(field) for field in required): raise ValueError(f"Membership record {index} lacks security_id or ticker")
    source_count = len(records)
    records = [record for record in records if is_eligible(record)]
    LOG.info("Conservative compliance filter retained %s of %s membership records", len(records), source_count)
    records.sort(key=lambda row: (str(row["security_id"]), str(row["ticker"])))
    return records

def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key); return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}: return False
        raise

def download_history(symbol: str, max_retries: int) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            frame = yf.download(symbol, period="max", interval="1d", auto_adjust=False, actions=False, progress=False, threads=False, timeout=30)
            if frame.empty: raise ValueError("Yahoo Finance returned no rows")
            return frame
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                delay = 60 * attempt + random.uniform(0, 10) if exc.__class__.__name__ == "YFRateLimitError" else (2 ** (attempt - 1)) + random.random()
                LOG.warning("Download failed for %s (attempt %s/%s); retrying in %.1fs", symbol, attempt, max_retries, delay); time.sleep(delay)
    raise RuntimeError(f"download failed after {max_retries} attempts: {last_error}")

def normalize_history(frame: pd.DataFrame, security_id: str, ticker: str) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.copy(); frame.columns = frame.columns.get_level_values(0)
    normalized = frame.reset_index().rename(columns={"Date":"date","Datetime":"date","Open":"open","High":"high","Low":"low","Close":"close","Adj Close":"adj_close","Volume":"volume"})
    missing = {"date","open","high","low","close","volume"} - set(normalized.columns)
    if missing: raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")
    if "adj_close" not in normalized: normalized["adj_close"] = normalized["close"]
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    for column in ["open","high","low","close","adj_close"]: normalized[column] = pd.to_numeric(normalized[column], errors="coerce").astype("float64")
    normalized["volume"] = pd.to_numeric(normalized["volume"], errors="coerce").fillna(0).astype("int64")
    normalized["security_id"] = security_id; normalized["ticker"] = ticker
    normalized = normalized[OHLCV_COLUMNS].dropna(subset=["date","open","high","low","close"]).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    if normalized.empty: raise ValueError("No valid OHLCV rows after normalization")
    if (normalized[["open","high","low","close"]] < 0).any().any(): raise ValueError("Negative OHLC price detected")
    if (normalized["high"] < normalized["low"]).any(): raise ValueError("High price below low price detected")
    from ohlcv_qc import validate_frame
    validate_frame(normalized)
    return normalized

def upload_parquet(s3, bucket: str, key: str, frame: pd.DataFrame) -> None:
    buffer = io.BytesIO(); frame.to_parquet(buffer, engine="pyarrow", index=False, compression="zstd")
    s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue(), ContentType="application/vnd.apache.parquet")
    if s3.head_object(Bucket=bucket, Key=key).get("ContentLength", 0) <= 0: raise RuntimeError(f"Uploaded object is empty: {key}")

def run(args: argparse.Namespace) -> int:
    validate_args(args); bucket = os.environ["R2_BUCKET_NAME"]; s3 = make_s3_client(); membership = load_membership(s3, bucket, args.snapshot_date)
    selected = membership[args.start_index:args.start_index + args.batch_size]
    if not selected: raise ValueError(f"Batch starts beyond membership size ({len(membership)})")
    LOG.info("Processing membership rows %s..%s of %s", args.start_index, args.start_index + len(selected) - 1, len(membership)); results: list[Result] = []
    for position, record in enumerate(selected):
        security_id = str(record["security_id"]); ticker = str(record["ticker"]); symbol = yahoo_symbol(ticker, security_id); key = f"{HISTORY_PREFIX}{security_id}.parquet"
        if not args.force and object_exists(s3, bucket, key):
            LOG.info("Skipping existing object %s", key); results.append(Result(security_id, ticker, symbol, "skipped", object_key=key)); continue
        try:
            if position > 0 and args.request_delay:
                delay = args.request_delay + random.uniform(0, min(0.5, args.request_delay / 4)); LOG.info("Rate-limit delay: %.2fs", delay); time.sleep(delay)
            history = normalize_history(download_history(symbol, args.max_retries), security_id, ticker); upload_parquet(s3, bucket, key, history)
            result = Result(security_id, ticker, symbol, "uploaded", rows=len(history), first_date=history["date"].iloc[0].date().isoformat(), last_date=history["date"].iloc[-1].date().isoformat(), object_key=key); LOG.info("Uploaded %s (%s rows)", key, len(history))
        except Exception as exc:
            LOG.error("Failed %s (%s): %s", ticker, security_id, exc); print(f"::warning title=OHLCV unavailable::{ticker} ({security_id}): {str(exc)[:300]}"); result = Result(security_id, ticker, symbol, "failed", error=str(exc)[:500])
        results.append(result)
    run_id = os.getenv("GITHUB_RUN_ID", "local"); attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    manifest_key = f"{HISTORY_MANIFEST_PREFIX}bootstrap/{args.snapshot_date}/run-{run_id}-{attempt}-index-{args.start_index}.json"
    report = {"created_at":datetime.now(UTC).isoformat(),"snapshot_date":args.snapshot_date,"membership_count":len(membership),"start_index":args.start_index,"batch_size":len(selected),"force":args.force,"request_delay":args.request_delay,"summary":{status:sum(item.status == status for item in results) for status in ("uploaded","skipped","failed")},"results":[asdict(item) for item in results]}
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(report, indent=2).encode(), ContentType="application/json"); LOG.info("Manifest: %s", manifest_key); LOG.info("Summary: %s", report["summary"])
    attempted = report["summary"]["uploaded"] + report["summary"]["failed"]; failure_rate = report["summary"]["failed"] / attempted if attempted else 0
    if failure_rate > 0.10: LOG.error("Failure rate %.1f%% exceeds the 10%% safety threshold", failure_rate * 100); return 1
    if report["summary"]["failed"]: LOG.warning("Batch completed with %s unavailable ticker(s); see manifest repair queue", report["summary"]["failed"])
    return 0

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"); raise SystemExit(run(parse_args()))

if __name__ == "__main__": main()
