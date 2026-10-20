"""Bounded, audited repair of specific missing daily bars in one canonical history."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from datetime import UTC, datetime, timedelta

import pandas as pd
import yfinance as yf

from bootstrap_ohlcv import HISTORY_PREFIX, OHLCV_COLUMNS, make_s3_client, yahoo_symbol
from compliance import is_eligible
from repair_yahoo_session_gap import normalize_target_session


def merge_history(history, additions, target_dates):
    """Append only the explicitly targeted dates; fail closed on ambiguous history."""
    required = list(OHLCV_COLUMNS)
    if not set(required).issubset(history.columns) or not set(required).issubset(additions.columns):
        raise ValueError("History/additions lack required OHLCV columns")
    old = history[required].copy()
    old["date"] = pd.to_datetime(old["date"], errors="raise").dt.normalize()
    add = additions[required].copy()
    add["date"] = pd.to_datetime(add["date"], errors="raise").dt.normalize()
    targets = {pd.Timestamp(d).normalize() for d in target_dates}
    if old["date"].isna().any() or old.duplicated(["date"]).any():
        raise ValueError("Existing history has null or duplicate dates")
    if add["date"].isna().any() or add.duplicated(["date"]).any():
        raise ValueError("Repair rows have null or duplicate dates")
    if set(add["date"]) != targets or len(add) != len(targets):
        raise ValueError("Repair rows do not exactly match the requested target dates")
    if set(old["date"]) & targets:
        raise ValueError("A target date already exists in history; re-audit before retry")
    merged = pd.concat([old, add], ignore_index=True)[required]
    merged = merged.sort_values("date").reset_index(drop=True)
    if merged.duplicated(["date"]).any():
        raise ValueError("Duplicate date after merge")
    preserved = merged.loc[merged["date"].isin(set(old["date"])), required].reset_index(drop=True)
    old_sorted = old.sort_values("date").reset_index(drop=True)
    pd.testing.assert_frame_equal(old_sorted, preserved, check_dtype=False, check_exact=True)
    return merged


def read_json(s3, bucket, key):
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--security-id", required=True)
    p.add_argument("--ticker", required=True)
    p.add_argument("--target-dates", required=True, help="Comma-separated YYYY-MM-DD sessions")
    p.add_argument("--expected-ready-as-of", required=True)
    p.add_argument("--expected-ready-sha256", required=True)
    a = p.parse_args()

    sid = str(a.security_id)
    ticker = str(a.ticker)
    targets = sorted({pd.Timestamp(x.strip()).normalize() for x in a.target_dates.split(",") if x.strip()})
    if not targets or len(targets) != len([x for x in a.target_dates.split(",") if x.strip()]):
        raise ValueError("Target dates must be non-empty and unique")
    if any(t > pd.Timestamp(datetime.now(UTC).date()) for t in targets):
        raise ValueError("Target date cannot be in the future")

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    ready = read_json(s3, bucket, "production/ready/current.json")
    if ready.get("as_of_date") != a.expected_ready_as_of or ready.get("sha256") != a.expected_ready_sha256:
        raise RuntimeError("Canonical READY identity changed since audit; aborting without writes")

    universe = read_json(s3, bucket, "universe/current.json")
    snapshot = str(universe["snapshot_date"])
    membership = read_json(s3, bucket, f"universe/membership/{snapshot}.json")
    matches = [row for row in membership.get("records", []) if str(row.get("security_id")) == sid]
    if len(matches) != 1 or not is_eligible(matches[0]) or str(matches[0].get("ticker")) != ticker:
        raise RuntimeError("Security identity/compliance does not match current membership")
    if membership.get("count") != len(membership.get("records", [])):
        raise RuntimeError("Membership count mismatch; aborting")

    key = f"{HISTORY_PREFIX}{sid}.parquet"
    original_obj = s3.get_object(Bucket=bucket, Key=key)
    original_raw = original_obj["Body"].read()
    original_etag = original_obj["ETag"]
    history = pd.read_parquet(io.BytesIO(original_raw), engine="pyarrow")
    history["date"] = pd.to_datetime(history["date"], errors="raise").dt.normalize()
    if set(history.columns) < set(OHLCV_COLUMNS):
        raise ValueError("Canonical history schema is incomplete")
    present = set(history["date"])
    if present & set(targets):
        raise RuntimeError("One or more target dates already exist; re-audit before repair")

    symbol = yahoo_symbol(ticker, sid)
    raw = yf.Ticker(symbol).history(
        start=(min(targets).date() - timedelta(days=7)).isoformat(),
        end=(max(targets).date() + timedelta(days=3)).isoformat(),
        interval="1d", auto_adjust=False, actions=False, repair=True,
    )
    additions = pd.concat(
        [normalize_target_session(raw, sid, ticker, target) for target in targets],
        ignore_index=True,
    )
    merged = merge_history(history, additions, targets)

    backup_prefix = f"backups/repairs/ready-gap/{sid}/"
    run_id = os.getenv("GITHUB_RUN_ID", "manual")
    backup_key = f"{backup_prefix}{run_id}-before.parquet"
    backup_manifest_key = f"{backup_prefix}{run_id}-manifest.json"
    s3.put_object(
        Bucket=bucket, Key=backup_key, Body=original_raw,
        ContentType="application/vnd.apache.parquet", IfNoneMatch="*",
    )
    backup_manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "reason": "Verified READY continuity gap repair",
        "security_id": sid,
        "ticker": ticker,
        "target_dates": [x.date().isoformat() for x in targets],
        "expected_ready_as_of": a.expected_ready_as_of,
        "expected_ready_sha256": a.expected_ready_sha256,
        "history_key": key,
        "history_etag_before": original_etag,
        "history_sha256_before": hashlib.sha256(original_raw).hexdigest(),
        "backup_key": backup_key,
        "source": "Yahoo Ticker.history(repair=True), full OHLCV row validated before merge",
        "repair_rows": additions.to_dict("records"),
    }
    s3.put_object(
        Bucket=bucket, Key=backup_manifest_key,
        Body=json.dumps(backup_manifest, default=str, indent=2).encode(),
        ContentType="application/json", IfNoneMatch="*",
    )

    latest_ready = read_json(s3, bucket, "production/ready/current.json")
    if latest_ready.get("sha256") != a.expected_ready_sha256:
        raise RuntimeError("READY changed before history write; backup preserved, repair aborted")
    latest_head = s3.head_object(Bucket=bucket, Key=key)
    if latest_head.get("ETag") != original_etag:
        raise RuntimeError("History object changed before write; backup preserved, repair aborted")

    out = io.BytesIO()
    merged.to_parquet(out, engine="pyarrow", index=False, compression="zstd")
    new_raw = out.getvalue()
    s3.put_object(
        Bucket=bucket, Key=key, Body=new_raw,
        ContentType="application/vnd.apache.parquet", IfMatch=original_etag,
    )

    verified_raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    verified = pd.read_parquet(io.BytesIO(verified_raw), engine="pyarrow")
    verified["date"] = pd.to_datetime(verified["date"], errors="raise").dt.normalize()
    if len(verified) != len(history) + len(targets):
        raise RuntimeError("Post-write row count mismatch; backup preserved")
    if verified.duplicated(["date"]).any() or not set(targets).issubset(set(verified["date"])):
        raise RuntimeError("Post-write target-date verification failed; backup preserved")
    preserved = verified.loc[verified["date"].isin(set(history["date"])), OHLCV_COLUMNS].reset_index(drop=True)
    old_sorted = history[OHLCV_COLUMNS].sort_values("date").reset_index(drop=True)
    pd.testing.assert_frame_equal(old_sorted, preserved, check_dtype=False, check_exact=True)

    print("TARGETED_HISTORY_REPAIR=" + json.dumps({
        "status": "VERIFIED",
        "security_id": sid,
        "ticker": ticker,
        "yahoo_symbol": symbol,
        "target_dates": [x.date().isoformat() for x in targets],
        "validated_rows": additions.to_dict("records"),
        "history_key": key,
        "rows_before": len(history),
        "rows_after": len(verified),
        "backup_key": backup_key,
        "backup_manifest_key": backup_manifest_key,
        "history_sha256_before": hashlib.sha256(original_raw).hexdigest(),
        "history_sha256_after": hashlib.sha256(verified_raw).hexdigest(),
        "ready_pointer_unchanged": True,
    }, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
