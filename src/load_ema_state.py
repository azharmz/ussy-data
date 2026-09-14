"""Load and validate the shared persisted EMA state from private R2."""
from __future__ import annotations

import hashlib
import io
import json

from ema_state import PERIODS, PRICE_BASIS, validate_state_frame

EMA_POINTER_KEY = "production/indicators/ema/current.json"
EMA_RUN_PREFIX = "production/indicators/ema/runs/"
PROMOTION_POLICY = "candidate_then_equivalence_then_immutable_run_then_current_pointer_last_v1"


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
        "update_method", "promotion_policy", "equivalence",
    }
    if not required.issubset(manifest):
        raise ValueError("EMA manifest lacks required promotion evidence")
    if manifest["schema_version"] != 1 or manifest["price_basis"] != PRICE_BASIS:
        raise ValueError("Invalid EMA manifest schema/price basis")
    if list(manifest["periods"]) != list(PERIODS) or not key.startswith(EMA_RUN_PREFIX):
        raise ValueError("Invalid EMA periods/parquet key")
    if manifest["promotion_policy"] != PROMOTION_POLICY:
        raise ValueError("EMA manifest promotion policy is not approved")
    equivalence = manifest.get("equivalence")
    if not isinstance(equivalence, dict):
        raise ValueError("EMA manifest equivalence evidence is invalid")
    if equivalence.get("numeric_failures") != 0 or equivalence.get("classification_mismatches") != 0:
        raise ValueError("EMA manifest equivalence gate did not pass")
    if not isinstance(equivalence.get("verified"), int) or equivalence["verified"] < 1:
        raise ValueError("EMA manifest equivalence coverage is invalid")
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
