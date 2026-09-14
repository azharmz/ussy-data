"""Load and validate the shared persisted EMA state from private R2."""
from __future__ import annotations

import hashlib
import io
import json

from ema_state import PERIODS, PRICE_BASIS, validate_state_frame

EMA_POINTER_KEY = "production/indicators/ema/current.json"
EMA_RUN_PREFIX = "production/indicators/ema/runs/"


def load_ema_state(s3, bucket):
    import pandas as pd

    try:
        raw = s3.get_object(Bucket=bucket, Key=EMA_POINTER_KEY)["Body"].read()
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise FileNotFoundError(EMA_POINTER_KEY) from exc
        raise
    manifest = json.loads(raw)
    key = manifest.get("parquet_key", "")
    required = {
        "schema_version", "created_at", "price_basis", "periods", "securities",
        "parquet_key", "sha256", "source_ready_parquet_key", "source_ready_sha256",
        "update_method",
    }
    if not required.issubset(manifest):
        raise ValueError("EMA manifest lacks required fields")
    if manifest["schema_version"] != 1 or manifest["price_basis"] != PRICE_BASIS:
        raise ValueError("Invalid EMA manifest schema/price basis")
    if list(manifest["periods"]) != list(PERIODS) or not key.startswith(EMA_RUN_PREFIX):
        raise ValueError("Invalid EMA periods/parquet key")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != manifest["sha256"]:
        raise ValueError("EMA Parquet checksum mismatch")
    frame = validate_state_frame(pd.read_parquet(io.BytesIO(body)))
    if len(frame) != manifest["securities"]:
        raise ValueError("EMA manifest/data security count mismatch")
    security_ids = manifest.get("security_ids")
    if security_ids is not None and set(frame["security_id"]) != set(map(str, security_ids)):
        raise ValueError("EMA manifest/data security IDs mismatch")
    return frame, manifest
