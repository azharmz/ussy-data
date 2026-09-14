"""Compute and persist a non-production close-basis EMA candidate."""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from bootstrap_ohlcv import make_s3_client
from ema_close_state import PERIODS, PRICE_BASIS, StateNeedsRebuild, advance_state, bootstrap_state, validate_state_frame
from load_ema_close_state import load_ema_close_state
from load_ready import load_ready

UPDATE_METHOD = "long_history_bootstrap_then_recursive_persisted_state_v1"
CANDIDATE_PREFIX = "validation/indicators/ema-close/"


def candidate_id() -> str:
    return f"run-{os.getenv('GITHUB_RUN_ID', 'local')}-{os.getenv('GITHUB_RUN_ATTEMPT', '1')}"


def candidate_keys() -> tuple[str, str]:
    base = f"{CANDIDATE_PREFIX}{candidate_id()}"
    return f"{base}.parquet", f"{base}.json"


def _read_full_history(s3, bucket: str, security_id: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=f"backtest/ohlcv/{security_id}.parquet")["Body"].read()
    return pd.read_parquet(io.BytesIO(body), engine="pyarrow")


def _ready_groups(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    data = frame.copy()
    data["security_id"] = data["security_id"].astype(str)
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    return {sid: group.sort_values("date").reset_index(drop=True) for sid, group in data.groupby("security_id", sort=True)}


def _bootstrap_checked(s3, bucket: str, security_id: str, ready_rows: pd.DataFrame) -> dict[str, object]:
    history = _read_full_history(s3, bucket, security_id)
    state = bootstrap_state(history, security_id)
    ready_latest = ready_rows.sort_values("date").iloc[-1]
    state_date = pd.Timestamp(state["as_of_date"]).normalize()
    ready_date = pd.Timestamp(ready_latest["date"]).normalize()
    if state_date != ready_date:
        raise RuntimeError(f"Long history/ready date mismatch for {security_id}: {state_date.date()} != {ready_date.date()}")
    if not np.isclose(float(state["last_price"]), float(ready_latest[PRICE_BASIS]), rtol=1e-10, atol=1e-10):
        raise RuntimeError(f"Long history/ready close mismatch for {security_id}")
    return state


def build_state(s3, bucket: str, ready: pd.DataFrame, previous: pd.DataFrame | None):
    groups = _ready_groups(ready)
    previous_rows = {}
    if previous is not None:
        previous = validate_state_frame(previous)
        previous_rows = {str(row.security_id): row for row in previous.itertuples(index=False)}
    rows = []
    counters = {"bootstrap": 0, "recursive": 0, "unchanged": 0, "rebuild": 0}
    for security_id, ready_rows in groups.items():
        prior = previous_rows.get(security_id)
        if prior is None:
            rows.append(_bootstrap_checked(s3, bucket, security_id, ready_rows))
            counters["bootstrap"] += 1
            continue
        try:
            row, advanced = advance_state(prior._asdict(), ready_rows)
            rows.append(row)
            counters["recursive" if advanced else "unchanged"] += 1
        except StateNeedsRebuild:
            rows.append(_bootstrap_checked(s3, bucket, security_id, ready_rows))
            counters["rebuild"] += 1
    state = validate_state_frame(pd.DataFrame(rows))
    if set(state["security_id"]) != set(groups):
        raise RuntimeError("EMA close output security set does not match ready security set")
    return state, counters


def main() -> None:
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    ready, ready_manifest = load_ready(s3, bucket)
    previous = None
    try:
        previous, _ = load_ema_close_state(s3, bucket)
    except (FileNotFoundError, ValueError):
        previous = None
    state, counters = build_state(s3, bucket, ready, previous)
    buf = io.BytesIO()
    state.to_parquet(buf, engine="pyarrow", index=False, compression="zstd")
    body = buf.getvalue()
    digest = hashlib.sha256(body).hexdigest()
    parquet_key, manifest_key = candidate_keys()
    s3.put_object(Bucket=bucket, Key=parquet_key, Body=body, ContentType="application/vnd.apache.parquet")
    uploaded = s3.get_object(Bucket=bucket, Key=parquet_key)["Body"].read()
    if hashlib.sha256(uploaded).hexdigest() != digest:
        raise RuntimeError("EMA close candidate verification failed")
    as_of = pd.to_datetime(state["as_of_date"])
    manifest = {
        "schema_version": 1,
        "candidate_created_at": datetime.now(UTC).isoformat(),
        "status": "CANDIDATE_NOT_PRODUCTION_APPROVED",
        "price_basis": PRICE_BASIS,
        "periods": list(PERIODS),
        "securities": len(state),
        "security_ids": state["security_id"].tolist(),
        "candidate_parquet_key": parquet_key,
        "candidate_sha256": digest,
        "source_ready_parquet_key": ready_manifest["parquet_key"],
        "source_ready_sha256": ready_manifest["sha256"],
        "source_ready_created_at": ready_manifest.get("created_at"),
        "update_method": UPDATE_METHOD,
        "bootstrap_count": counters["bootstrap"],
        "recursive_count": counters["recursive"],
        "unchanged_count": counters["unchanged"],
        "rebuild_count": counters["rebuild"],
        "as_of_date_min": as_of.min().date().isoformat(),
        "as_of_date_max": as_of.max().date().isoformat(),
    }
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest, indent=2).encode(), ContentType="application/json")
    print(json.dumps({k: v for k, v in manifest.items() if k != "security_ids"}, indent=2))


if __name__ == "__main__":
    main()
