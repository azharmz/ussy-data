from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from urllib.request import Request, urlopen

import boto3
import pandas as pd
from botocore.config import Config

VERSION = "53-broad-market-membership-publisher-v1"
SOURCE_PROVIDER = "NASDAQ_TRADER_SYMBOL_DIRECTORY"
NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )


def fetch_text(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "USSY-Data/53 broad-market membership publisher"})
    with urlopen(req, timeout=45) as resp:
        data = resp.read()
    if not data or b"File Creation Time" not in data:
        raise ValueError(f"Invalid Nasdaq Trader symbol-directory payload: {url}")
    return data


def _parse_pipe_payload(raw: bytes, source_file: str) -> tuple[pd.DataFrame, str]:
    text = raw.decode("utf-8-sig", errors="strict")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"{source_file}: insufficient rows")
    creation_rows = [line for line in lines if line.startswith("File Creation Time")]
    if len(creation_rows) != 1:
        raise ValueError(f"{source_file}: expected exactly one File Creation Time row")
    creation_time = creation_rows[0].split("|", 1)[0].replace("File Creation Time:", "").strip()
    data_lines = [line for line in lines if not line.startswith("File Creation Time")]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter="|")
    rows = list(reader)
    if not rows:
        raise ValueError(f"{source_file}: no security rows")
    frame = pd.DataFrame(rows)
    frame["source_file"] = source_file
    return frame, creation_time


def normalize_membership(nasdaq_raw: bytes, other_raw: bytes, fetched_at: str) -> tuple[pd.DataFrame, dict]:
    nq, nq_created = _parse_pipe_payload(nasdaq_raw, "nasdaqlisted.txt")
    ot, ot_created = _parse_pipe_payload(other_raw, "otherlisted.txt")

    nq_out = pd.DataFrame({
        "symbol": nq["Symbol"].astype(str).str.strip(),
        "security_name": nq["Security Name"].astype(str).str.strip(),
        "listing_exchange": "NASDAQ",
        "market_category": nq.get("Market Category"),
        "financial_status": nq.get("Financial Status"),
        "is_etf": nq.get("ETF"),
        "test_issue": nq.get("Test Issue"),
        "source_file": "nasdaqlisted.txt",
    })
    symbol_col = "ACT Symbol" if "ACT Symbol" in ot.columns else ot.columns[0]
    ot_out = pd.DataFrame({
        "symbol": ot[symbol_col].astype(str).str.strip(),
        "security_name": ot["Security Name"].astype(str).str.strip(),
        "listing_exchange": ot["Exchange"].astype(str).str.strip(),
        "market_category": None,
        "financial_status": None,
        "is_etf": ot.get("ETF"),
        "test_issue": ot.get("Test Issue"),
        "source_file": "otherlisted.txt",
    })
    out = pd.concat([nq_out, ot_out], ignore_index=True)
    for col in ["market_category", "financial_status", "is_etf", "test_issue"]:
        out[col] = out[col].where(pd.notna(out[col]), None)
    out["source_provider"] = SOURCE_PROVIDER
    out["source_contract_version"] = VERSION
    out["fetched_at"] = fetched_at
    out = out.loc[out["symbol"].ne("")].copy()
    if out.empty:
        raise ValueError("Normalized membership is empty")
    if out.duplicated(subset=["source_file", "symbol"]).any():
        raise ValueError("Duplicate symbol within source file")
    out = out.sort_values(["source_file", "symbol"]).reset_index(drop=True)
    meta = {
        "nasdaqlisted_file_creation_time": nq_created,
        "otherlisted_file_creation_time": ot_created,
        "nasdaqlisted_rows": int(len(nq_out)),
        "otherlisted_rows": int(len(ot_out)),
        "combined_rows": int(len(out)),
    }
    return out, meta


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False, compression="zstd")
    return buf.getvalue()


def publish() -> dict:
    fetched_at = datetime.now(UTC).isoformat()
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = make_s3_client()

    nasdaq_raw = fetch_text(NASDAQ_URL)
    other_raw = fetch_text(OTHER_URL)
    frame, meta = normalize_membership(nasdaq_raw, other_raw, fetched_at)
    parquet = _parquet_bytes(frame)

    prefix = f"market/membership/runs/{run_id}"
    objects = {
        f"{prefix}/nasdaqlisted.txt": nasdaq_raw,
        f"{prefix}/otherlisted.txt": other_raw,
        f"{prefix}/membership.parquet": parquet,
    }
    object_meta = {}
    for key, body in objects.items():
        ctype = "text/plain" if key.endswith(".txt") else "application/vnd.apache.parquet"
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ctype)
        object_meta[key.rsplit("/", 1)[-1]] = {"key": key, "sha256": _sha(body), "bytes": len(body)}

    manifest = {
        "publisher_version": VERSION,
        "run_id": run_id,
        "source_provider": SOURCE_PROVIDER,
        "source_urls": [NASDAQ_URL, OTHER_URL],
        "fetched_at": fetched_at,
        **meta,
        "objects": object_meta,
        "pit_note": "This snapshot records only information fetched in this run. It does not reconstruct pre-publication membership and does not define O'Neil market leaders.",
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
    manifest_key = f"{prefix}/manifest.json"
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=manifest_bytes, ContentType="application/json")

    pointer = {
        "publisher_version": VERSION,
        "run_id": run_id,
        "manifest_key": manifest_key,
        "manifest_sha256": _sha(manifest_bytes),
        "fetched_at": fetched_at,
    }
    s3.put_object(
        Bucket=bucket,
        Key="market/membership/official.json",
        Body=json.dumps(pointer, indent=2, sort_keys=True).encode(),
        ContentType="application/json",
    )
    print(json.dumps({**pointer, **meta}, indent=2, sort_keys=True))
    return pointer


def main() -> None:
    publish()


if __name__ == "__main__":
    main()
