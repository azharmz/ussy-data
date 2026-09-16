"""One-shot guarded cleanup for R2 objects proven orphaned by repository trace.

Scope is intentionally an exact allowlist. Dry-run by default; --apply is required
for deletion. Institutional sponsorship and all prefix-based deletion are out of scope.
"""
from __future__ import annotations

import argparse
import json
import os

from bootstrap_ohlcv import make_s3_client

ORPHAN_KEYS = (
    "connection-tests/github-actions-33362338844-1.txt",
    "web/r2-storage-status.json",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    present = []
    for key in ORPHAN_KEYS:
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                continue
            raise
        present.append({"key": key, "bytes": int(head.get("ContentLength", 0))})

    print(json.dumps({
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "exact_allowlist": list(ORPHAN_KEYS),
        "present": present,
        "delete_objects": len(present),
        "delete_bytes": sum(item["bytes"] for item in present),
    }, indent=2), flush=True)

    if not args.apply:
        return

    for item in present:
        key = item["key"]
        if key not in ORPHAN_KEYS or key.startswith("institutional_sponsorship/"):
            raise RuntimeError(f"refusing non-allowlisted key: {key}")
        s3.delete_object(Bucket=bucket, Key=key)
        print(f"deleted {key}", flush=True)

    # Verify exact targets are absent after deletion.
    remaining = []
    for key in ORPHAN_KEYS:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            remaining.append(key)
        except s3.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise
    if remaining:
        raise RuntimeError(f"post-delete verification failed: {remaining}")
    print("orphan cleanup verified", flush=True)


if __name__ == "__main__":
    main()
