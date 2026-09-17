"""Publish a ready-only rolling dataset in the private R2 bucket.

Membership and original OHLCV remain untouched. No Yahoo downloads.
READY publication fails closed when a tiny provider-leading edge is newer than
the modal terminal date across ready securities.
"""
from __future__ import annotations
from compliance import is_eligible
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from uuid import uuid4


def select_ready_ids(readiness, membership, snapshot):
    if readiness.get("snapshot_date") != snapshot or membership.get("snapshot_date") != snapshot:
        raise ValueError("Source snapshot mismatch")
    records = membership.get("records")
    if not isinstance(records, list) or membership.get("count") != len(records):
        raise ValueError("Invalid membership records/count")
    compliant = {str(row["security_id"]) for row in records if is_eligible(row)}
    ids = readiness.get("ready_security_ids")
    if not isinstance(ids, list) or not all(isinstance(s, str) and s for s in ids):
        raise ValueError("Invalid ready_security_ids")
    selected = set(ids)
    if readiness.get('confirmed_compliant') != len(compliant):
        raise ValueError('Readiness eligibility count is stale; rebuild rolling first')
    if not selected or len(selected) != len(ids) or len(selected) != readiness.get("ready"):
        raise ValueError("Empty, duplicate, or inconsistent ready IDs")
    if not selected.issubset(compliant):
        raise ValueError("Ready IDs include non-compliant membership")
    excluded = set(readiness.get("insufficient_history_security_ids", [])) | set(readiness.get("data_unavailable_security_ids", []))
    if selected & excluded:
        raise ValueError("Ready and excluded lists overlap")
    return selected


def terminal_date_summary(frame):
    """Return terminal-date lineage and reject a partial newer provider edge.

    Older terminal dates can be legitimate (halts/delistings). A newer date than
    the modal terminal date cannot be authoritative because most READY securities
    have not reached it yet; publishing it would make global max(date) misleading.
    """
    terminal = frame.groupby("security_id")["date"].max()
    histogram = terminal.dt.date.astype(str).value_counts().sort_index()
    if histogram.empty:
        raise ValueError("READY terminal-date histogram is empty")
    mode_count = int(histogram.max())
    mode_dates = sorted(str(d) for d, n in histogram.items() if int(n) == mode_count)
    as_of = mode_dates[-1]
    max_date = str(terminal.max().date())
    if max_date > as_of:
        leading = int((terminal.dt.date.astype(str) == max_date).sum())
        raise RuntimeError(
            f"READY partial leading-edge date rejected: max={max_date} securities={leading}; "
            f"modal_as_of={as_of} securities={mode_count}"
        )
    return {
        "as_of_date": as_of,
        "as_of_security_count": mode_count,
        "terminal_date_min": str(terminal.min().date()),
        "terminal_date_max": max_date,
        "terminal_date_histogram": {str(k): int(v) for k, v in histogram.items()},
    }


def filter_rolling(frame, readiness, selected):
    import pandas as pd
    required = ["date", "security_id", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
    if not set(required).issubset(frame.columns):
        raise ValueError("Rolling data lacks required columns")
    result = frame.loc[frame["security_id"].isin(selected), required].copy()
    if set(result["security_id"]) != selected:
        raise ValueError("Some ready IDs have no rolling data")
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    if result["date"].isna().any() or result.duplicated(["security_id", "date"]).any():
        raise ValueError("Null dates or duplicate security/date rows")
    counts = result.groupby("security_id").size()
    minimum, maximum = readiness["minimum_ready_bars"], readiness["rolling_bars_target"]
    if not 1 <= minimum <= maximum or (counts < minimum).any() or (counts > maximum).any():
        raise ValueError("Ready row counts violate configured bar thresholds")
    details = {row["security_id"]: row for row in readiness["securities"]}
    dates = result.groupby("security_id")["date"].max()
    for sid in selected:
        if sid not in details or counts[sid] != details[sid]["rolling_bars"] or dates[sid].date().isoformat() != details[sid]["last_date"]:
            raise ValueError("Rolling data and readiness details disagree")
    from ohlcv_qc import validate_frame
    validate_frame(result)
    return result.sort_values(["security_id", "date"]).reset_index(drop=True)


def main():
    import pandas as pd
    from bootstrap_ohlcv import make_s3_client
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    def read(key):
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read(), obj["ETag"]
    current_raw, current_etag = read("universe/current.json")
    current = json.loads(current_raw); snapshot = current["snapshot_date"]
    ready_raw, ready_etag = read("production/rolling/readiness.json"); readiness = json.loads(ready_raw)
    member_key = f"universe/membership/{snapshot}.json"
    member_raw, member_etag = read(member_key)
    ids = select_ready_ids(readiness, json.loads(member_raw), snapshot)
    rolling_raw, rolling_etag = read("production/rolling/latest.parquet")
    frame = filter_rolling(pd.read_parquet(io.BytesIO(rolling_raw)), readiness, ids)
    terminal = terminal_date_summary(frame)
    print("READY terminal-date summary:", json.dumps(terminal, sort_keys=True))
    for key, etag in [("universe/current.json", current_etag), (member_key, member_etag),
                      ("production/rolling/readiness.json", ready_etag), ("production/rolling/latest.parquet", rolling_etag)]:
        if s3.head_object(Bucket=bucket, Key=key)["ETag"] != etag:
            raise RuntimeError("Source changed during export; retry after update finishes")
    buf = io.BytesIO(); frame.to_parquet(buf, engine="pyarrow", index=False, compression="zstd"); body = buf.getvalue()
    key = f"production/ready/runs/{uuid4().hex}.parquet"
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/vnd.apache.parquet")
    if s3.head_object(Bucket=bucket, Key=key)["ContentLength"] != len(body):
        raise RuntimeError("Ready export size verification failed")
    manifest = {
        "schema_version": 2, "created_at": datetime.now(UTC).isoformat(),
        "snapshot_date": snapshot, "readiness_created_at": readiness["created_at"],
        "securities": len(ids), "rows": len(frame), "security_ids": sorted(ids),
        "minimum_ready_bars": readiness["minimum_ready_bars"], "rolling_bars_target": readiness["rolling_bars_target"],
        "parquet_key": key, "sha256": hashlib.sha256(body).hexdigest(),
        "policy": "active_compliant_and_ready; modal terminal date must equal global max date",
        **terminal,
    }
    s3.put_object(Bucket=bucket, Key="production/ready/current.json", Body=json.dumps(manifest, indent=2).encode(), ContentType="application/json")
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("security_ids", "terminal_date_histogram")}, indent=2))
    print("Ready dataset: production/ready/current.json (private bucket; originals unchanged)")


if __name__ == "__main__":
    main()
