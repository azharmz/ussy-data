from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from datetime import UTC, datetime, timedelta

import pandas as pd
import yfinance as yf

from bootstrap_ohlcv import make_s3_client

VERSION = "49-market-index-publisher-v1"
SOURCE_CONTRACT = "47-market-input-data-contract-v1"
INDEXES = {
    "NASDAQ_COMPOSITE": "^IXIC",
    "SP500": "^GSPC",
    "DJIA": "^DJI",
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--period", default="2y")
    return p.parse_args()


def normalize(index_id: str, symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise RuntimeError(f"empty source frame: {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    frame = raw.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    required = ["date", "open", "high", "low", "close", "volume"]
    frame = frame[required].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    for c in ["open", "high", "low", "close", "volume"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "volume"])
    if frame.empty or (frame["volume"] < 0).any():
        raise RuntimeError(f"invalid normalized data: {symbol}")
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise RuntimeError(f"invalid date chronology: {symbol}")
    bad = (frame["high"] < frame[["open", "low", "close"]].max(axis=1)) | (frame["low"] > frame[["open", "high", "close"]].min(axis=1))
    if bad.any():
        raise RuntimeError(f"invalid OHLC envelope: {symbol}")
    frame.insert(0, "index_id", index_id)
    frame["source_provider"] = "YAHOO_YFINANCE"
    frame["source_symbol"] = symbol
    frame["source_contract_version"] = SOURCE_CONTRACT
    return frame


def parquet_bytes(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False, engine="pyarrow", compression="zstd")
    return buf.getvalue()


def put_json(s3, bucket, key, obj):
    payload = json.dumps(obj, sort_keys=True, indent=2).encode()
    s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/json")
    return hashlib.sha256(payload).hexdigest()


def main():
    args = parse_args()
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    fetched_at = datetime.now(UTC).isoformat()
    run_id = os.environ.get("GITHUB_RUN_ID", datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    run_prefix = f"market/indexes/runs/{run_id}"
    manifest = {"publisher_version": VERSION, "source_contract_version": SOURCE_CONTRACT, "fetched_at": fetched_at, "run_id": run_id, "indexes": {}}

    for index_id, symbol in INDEXES.items():
        raw = yf.download(symbol, period=args.period, interval="1d", auto_adjust=False, actions=False, progress=False, threads=False, timeout=30)
        frame = normalize(index_id, symbol, raw)
        frame["fetched_at"] = fetched_at
        payload = parquet_bytes(frame)
        digest = hashlib.sha256(payload).hexdigest()
        key = f"{run_prefix}/{index_id}.parquet"
        s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/vnd.apache.parquet", Metadata={"sha256": digest, "publisher-version": VERSION})
        manifest["indexes"][index_id] = {"source_symbol": symbol, "rows": len(frame), "first_date": frame.date.iloc[0], "last_date": frame.date.iloc[-1], "object_key": key, "sha256": digest}

    manifest_key = f"{run_prefix}/manifest.json"
    manifest_digest = put_json(s3, bucket, manifest_key, manifest)
    pointer = {"publisher_version": VERSION, "run_id": run_id, "manifest_key": manifest_key, "manifest_sha256": manifest_digest, "published_at": fetched_at}
    put_json(s3, bucket, "market/indexes/official.json", pointer)
    print(json.dumps(pointer, indent=2))


if __name__ == "__main__":
    main()
