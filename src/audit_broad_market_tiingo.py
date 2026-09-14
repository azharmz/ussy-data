"""Read-only #54 audit of Tiingo broad-market adjusted-close coverage.

Consumes the current #53 PIT membership snapshot from R2, selects a deterministic
sample, and checks whether Tiingo can supply at least 252 completed daily
adjusted-close observations through the audit date. This script never writes to
R2 and does not emit leader labels.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from audit_tiingo import fetch as fetch_tiingo
from bootstrap_ohlcv import make_s3_client

VERSION = "54-cycle1-broad-market-tiingo-audit-v1"
MEMBERSHIP_POINTER = "market/membership/official.json"
REQUIRED_BARS = 252


def _flag(value: Any) -> bool:
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


def load_membership(s3, bucket: str) -> tuple[dict, dict, pd.DataFrame]:
    pointer = json.loads(s3.get_object(Bucket=bucket, Key=MEMBERSHIP_POINTER)["Body"].read())
    manifest_raw = s3.get_object(Bucket=bucket, Key=pointer["manifest_key"])["Body"].read()
    actual = hashlib.sha256(manifest_raw).hexdigest()
    if actual != pointer["manifest_sha256"]:
        raise RuntimeError("#53 membership manifest SHA mismatch")
    manifest = json.loads(manifest_raw)
    if manifest.get("publisher_version") != "53-broad-market-membership-publisher-v1":
        raise RuntimeError("unexpected #53 membership contract")
    member_meta = manifest["objects"]["membership.parquet"]
    payload = s3.get_object(Bucket=bucket, Key=member_meta["key"])["Body"].read()
    if hashlib.sha256(payload).hexdigest() != member_meta["sha256"]:
        raise RuntimeError("#53 membership parquet SHA mismatch")
    frame = pd.read_parquet(io.BytesIO(payload))
    return pointer, manifest, frame


def eligible_symbols(frame: pd.DataFrame) -> list[str]:
    required = {"symbol", "is_etf", "test_issue"}
    if not required.issubset(frame.columns):
        raise ValueError(f"membership missing columns: {sorted(required - set(frame.columns))}")
    work = frame.copy()
    work["symbol"] = work["symbol"].astype(str).str.strip().str.upper()
    work = work.loc[work["symbol"].ne("")]
    work = work.loc[~work["is_etf"].map(_flag) & ~work["test_issue"].map(_flag)]
    return sorted(set(work["symbol"]))


def deterministic_sample(symbols: list[str], sample_size: int, seed: str) -> list[str]:
    if sample_size < 1:
        raise ValueError("sample_size must be >= 1")
    ranked = sorted(symbols, key=lambda s: hashlib.sha256(f"{seed}|{s}".encode()).hexdigest())
    return ranked[: min(sample_size, len(ranked))]


def evaluate_rows(rows: list[dict]) -> dict:
    adj = [row for row in rows if row.get("adj_close") is not None and float(row["adj_close"]) > 0]
    dates = [row["date"] for row in adj]
    return {
        "rows": len(rows),
        "adjusted_close_rows": len(adj),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "has_252_adjusted_close_bars": len(adj) >= REQUIRED_BARS,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample-size", type=int, default=100)
    p.add_argument("--lookback-days", type=int, default=550)
    p.add_argument("--request-delay", type=float, default=0.25)
    args = p.parse_args()

    token = os.environ.get("TIINGO_API_KEY")
    if not token:
        raise ValueError("TIINGO_API_KEY is required")
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    pointer, manifest, membership = load_membership(s3, bucket)
    symbols = eligible_symbols(membership)
    seed = str(pointer["run_id"])
    sample = deterministic_sample(symbols, args.sample_size, seed)

    start = (date.today() - timedelta(days=args.lookback_days)).isoformat()
    end = (date.today() + timedelta(days=1)).isoformat()
    report = {
        "version": VERSION,
        "started_at": datetime.now(UTC).isoformat(),
        "mode": "READ_ONLY_SOURCE_AUDIT",
        "membership_run_id": pointer["run_id"],
        "membership_manifest_sha256": pointer["manifest_sha256"],
        "membership_fetched_at": pointer.get("fetched_at"),
        "eligible_non_etf_non_test_symbols": len(symbols),
        "sample_size_requested": args.sample_size,
        "sample_size_actual": len(sample),
        "sample_method": "SHA256(membership_run_id|symbol) ascending",
        "required_adjusted_close_bars": REQUIRED_BARS,
        "lookback_days": args.lookback_days,
        "results": [],
    }

    for i, symbol in enumerate(sample):
        if i and args.request_delay:
            time.sleep(args.request_delay)
        row = {"symbol": symbol}
        try:
            rows = fetch_tiingo(symbol, start, end, token)
            row.update(status="OK", **evaluate_rows(rows))
        except Exception as exc:
            row.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:500])
        report["results"].append(row)

    ok = [r for r in report["results"] if r["status"] == "OK"]
    ready = [r for r in ok if r.get("has_252_adjusted_close_bars")]
    report["summary"] = {
        "ok_symbols": len(ok),
        "error_symbols": len(report["results"]) - len(ok),
        "symbols_with_252_adjusted_close_bars": len(ready),
        "coverage_fraction_252": None if not sample else len(ready) / len(sample),
    }
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["interpretation"] = (
        "Descriptive audit only. No numeric production-acceptance threshold is implied. "
        "A later governance decision must review coverage, symbol failures, entitlement, "
        "and PIT provenance before Tiingo can be approved for the broad-market OHLCV panel."
    )

    out = Path("diagnostics/54-broad-market-tiingo")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
