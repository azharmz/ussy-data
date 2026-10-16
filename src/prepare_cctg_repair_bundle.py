"""Prepare a CCTG correction bundle locally; never write to R2.

Reads canonical history, frozen Stooq recovery history, READY and EMA state.
Writes only local backup/candidate files for a human-governed review.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
from datetime import UTC, datetime

import pandas as pd

from bootstrap_ohlcv import HISTORY_PREFIX, make_s3_client
from ema_state import PRICE_BASIS, bootstrap_state, validate_state_frame
from load_ema_state import EMA_POINTER_KEY, load_ema_state
from load_ready import load_ready

SECURITY_ID = "KYG1993R1184"
TICKER = "CCTG"
TARGET_DATE = pd.Timestamp("2026-09-22")
CANONICAL_ADJ_CLOSE = 0.5180000066757202
RECOVERY_ADJ_CLOSE = 0.5184
STOOQ_KEY = f"recovery/stooq-2026-09-25/history/ohlcv/{SECURITY_ID}.parquet"
HISTORY_KEY = f"{HISTORY_PREFIX}{SECURITY_ID}.parquet"
READY_POINTER_KEY = "production/ready/current.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def read_frame(s3, bucket: str, key: str) -> tuple[bytes, pd.DataFrame]:
    raw = read_bytes(s3, bucket, key)
    return raw, pd.read_parquet(io.BytesIO(raw), engine="pyarrow")


def correct_only_adjusted_close(history: pd.DataFrame, recovery: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Fail closed unless the exact diagnosed row/value pair is present."""
    required = {"date", "security_id", "ticker", "adj_close"}
    for label, frame in (("canonical", history), ("recovery", recovery)):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{label} history lacks columns: {sorted(missing)}")
    current = history.copy()
    source = recovery.copy()
    current["date"] = pd.to_datetime(current["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    source["date"] = pd.to_datetime(source["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    old_rows = current.index[current["date"] == TARGET_DATE].tolist()
    new_rows = source.loc[source["date"] == TARGET_DATE]
    if len(old_rows) != 1 or len(new_rows) != 1:
        raise ValueError(f"Expected one CCTG row on {TARGET_DATE.date()} in both histories")
    idx = old_rows[0]
    old_value = float(current.at[idx, PRICE_BASIS])
    new_value = float(new_rows.iloc[0][PRICE_BASIS])
    if old_value != CANONICAL_ADJ_CLOSE:
        raise ValueError(f"Canonical adj_close drifted from diagnosed value: {old_value!r}")
    if new_value != RECOVERY_ADJ_CLOSE:
        raise ValueError(f"Frozen recovery adj_close drifted from verified value: {new_value!r}")
    current.at[idx, PRICE_BASIS] = new_value
    current = current.sort_values("date").reset_index(drop=True)
    if current["date"].duplicated().any():
        raise ValueError("Canonical CCTG history contains duplicate dates")
    return current, {
        "security_id": SECURITY_ID,
        "ticker": TICKER,
        "date": TARGET_DATE.date().isoformat(),
        "field": PRICE_BASIS,
        "before": old_value,
        "after": new_value,
        "recovery_source_key": STOOQ_KEY,
        "other_fields_changed": False,
    }


def build_bundle(s3, bucket: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = output_dir / "backup"
    candidate_dir = output_dir / "candidate"
    backup_dir.mkdir(exist_ok=True)
    candidate_dir.mkdir(exist_ok=True)

    ready, ready_manifest = load_ready(s3, bucket)
    ready_pointer = json.loads(read_bytes(s3, bucket, READY_POINTER_KEY))
    if ready_pointer != ready_manifest:
        raise RuntimeError("READY pointer changed while source was loading")
    ready_cctg = ready.loc[ready["security_id"].astype(str) == SECURITY_ID].copy()
    if ready_cctg.empty:
        raise RuntimeError("CCTG absent from current READY snapshot")
    ready_cctg["date"] = pd.to_datetime(ready_cctg["date"], errors="raise").dt.tz_localize(None).dt.normalize()
    cutoff = ready_cctg["date"].max()

    canonical_raw, canonical = read_frame(s3, bucket, HISTORY_KEY)
    recovery_raw, recovery = read_frame(s3, bucket, STOOQ_KEY)
    current_state, ema_manifest = load_ema_state(s3, bucket)
    state_row = current_state.loc[current_state["security_id"].astype(str) == SECURITY_ID]
    if len(state_row) != 1:
        raise RuntimeError("Expected exactly one CCTG row in persisted EMA state")
    state_date = pd.Timestamp(state_row.iloc[0]["as_of_date"]).normalize()
    if state_date != cutoff:
        raise RuntimeError(f"CCTG READY cutoff {cutoff.date()} differs from EMA state date {state_date.date()}")

    corrected, row_change = correct_only_adjusted_close(canonical, recovery)
    corrected_cutoff = corrected.loc[corrected["date"] <= cutoff].copy()
    if corrected_cutoff.empty or pd.Timestamp(corrected_cutoff["date"].max()) != cutoff:
        raise RuntimeError("Corrected canonical history does not reach CCTG READY cutoff")
    latest_ready = ready_cctg.loc[ready_cctg["date"] == cutoff]
    if len(latest_ready) != 1:
        raise RuntimeError("Expected one latest CCTG READY row")
    if float(corrected_cutoff.iloc[-1][PRICE_BASIS]) != float(latest_ready.iloc[0][PRICE_BASIS]):
        raise RuntimeError("Corrected history last adj_close differs from READY at cutoff")

    rebuilt_row = bootstrap_state(corrected_cutoff, SECURITY_ID)
    for col in ("as_of_date", "last_price"):
        if col == "as_of_date":
            if pd.Timestamp(rebuilt_row[col]).normalize() != cutoff:
                raise RuntimeError("Rebuilt EMA date does not equal READY cutoff")
        elif float(rebuilt_row[col]) != float(state_row.iloc[0][col]):
            raise RuntimeError(f"Rebuilt EMA {col} differs from persisted state")
    candidate_state = current_state.copy()
    for col in ("ticker", "as_of_date", "last_price", "ema20", "ema50", "ema150", "ema200"):
        candidate_state.loc[candidate_state["security_id"].astype(str) == SECURITY_ID, col] = rebuilt_row[col]
    candidate_state = validate_state_frame(candidate_state)
    non_target_before = current_state.loc[current_state["security_id"].astype(str) != SECURITY_ID].reset_index(drop=True)
    non_target_after = candidate_state.loc[candidate_state["security_id"].astype(str) != SECURITY_ID].reset_index(drop=True)
    pd.testing.assert_frame_equal(non_target_before, non_target_after, check_dtype=True)

    corrected_buf = io.BytesIO()
    corrected.to_parquet(corrected_buf, engine="pyarrow", index=False, compression="zstd")
    candidate_buf = io.BytesIO()
    candidate_state.to_parquet(candidate_buf, engine="pyarrow", index=False, compression="zstd")
    (backup_dir / "CCTG-canonical-original.parquet").write_bytes(canonical_raw)
    (candidate_dir / "CCTG-corrected-history.parquet").write_bytes(corrected_buf.getvalue())
    (candidate_dir / "ema-state-candidate.parquet").write_bytes(candidate_buf.getvalue())
    (backup_dir / "CCTG-frozen-recovery-original.parquet").write_bytes(recovery_raw)

    manifest = {
        "schema_version": 1,
        "status": "LOCAL_CANDIDATE_NOT_PUBLISHED",
        "created_at": datetime.now(UTC).isoformat(),
        "security_id": SECURITY_ID,
        "ticker": TICKER,
        "target_date": TARGET_DATE.date().isoformat(),
        "correction": row_change,
        "ready_pointer_key": READY_POINTER_KEY,
        "ready_parquet_key": ready_manifest["parquet_key"],
        "ready_sha256": ready_manifest["sha256"],
        "ready_cutoff": cutoff.date().isoformat(),
        "ema_pointer_key": EMA_POINTER_KEY,
        "ema_parquet_key": ema_manifest["parquet_key"],
        "ema_manifest_sha256": sha256(json.dumps(ema_manifest, sort_keys=True).encode()),
        "canonical_history_key": HISTORY_KEY,
        "canonical_history_sha256_before": sha256(canonical_raw),
        "recovery_history_key": STOOQ_KEY,
        "recovery_history_sha256": sha256(recovery_raw),
        "backup_file": "backup/CCTG-canonical-original.parquet",
        "backup_sha256": sha256(canonical_raw),
        "corrected_history_file": "candidate/CCTG-corrected-history.parquet",
        "corrected_history_sha256": sha256(corrected_buf.getvalue()),
        "ema_candidate_file": "candidate/ema-state-candidate.parquet",
        "ema_candidate_sha256": sha256(candidate_buf.getvalue()),
        "persisted_cctg_ema_before": {k: (pd.Timestamp(v).date().isoformat() if k == "as_of_date" else float(v) if k != "ticker" else str(v)) for k, v in state_row.iloc[0].to_dict().items()},
        "rebuilt_cctg_ema_after": {k: (pd.Timestamp(v).date().isoformat() if k == "as_of_date" else float(v) if k != "ticker" else str(v)) for k, v in rebuilt_row.items()},
        "other_security_ema_rows_changed": 0,
        "r2_writes": 0,
        "production_pointer_writes": 0,
        "downstream_dispatch": "DISABLED",
        "requires_governed_review_before_any_apply": True,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="artifacts/cctg-repair-bundle")
    args = parser.parse_args()
    # This script intentionally has no upload/publish/apply mode.
    s3 = make_s3_client()
    manifest = build_bundle(s3, os.environ["R2_BUCKET_NAME"], Path(args.output_dir))
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
