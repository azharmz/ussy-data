"""One-shot cleanup for deprecated close-basis EMA R2 namespaces.

Deletes only the two explicitly deprecated prefixes and verifies they are empty.
The canonical adjusted EMA namespace production/indicators/ema/ is untouched.
"""
from __future__ import annotations

import json
import os

from bootstrap_ohlcv import make_s3_client

PREFIXES = (
    "production/indicators/ema-close/",
    "validation/indicators/ema-close/",
)


def list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        response = s3.list_objects_v2(**kwargs)
        keys.extend(obj["Key"] for obj in response.get("Contents", []))
        if not response.get("IsTruncated"):
            break
        token = response["NextContinuationToken"]
    return keys


def delete_keys(s3, bucket: str, keys: list[str]) -> int:
    deleted = 0
    for start in range(0, len(keys), 1000):
        batch = keys[start : start + 1000]
        if not batch:
            continue
        response = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": False},
        )
        errors = response.get("Errors", [])
        if errors:
            raise RuntimeError(f"R2 deletion errors: {errors}")
        deleted += len(response.get("Deleted", []))
    return deleted


def main() -> None:
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    report = {"bucket": bucket, "prefixes": {}, "canonical_ema_prefix_touched": False}

    for prefix in PREFIXES:
        before = list_keys(s3, bucket, prefix)
        deleted = delete_keys(s3, bucket, before)
        after = list_keys(s3, bucket, prefix)
        if after:
            raise RuntimeError(f"Deprecated EMA prefix not empty after cleanup: {prefix}: {after[:10]}")
        report["prefixes"][prefix] = {
            "objects_before": len(before),
            "objects_deleted": deleted,
            "objects_after": 0,
        }

    # Guardrail: this script never lists/deletes under production/indicators/ema/.
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
