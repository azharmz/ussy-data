from __future__ import annotations

import json
import os
import sys

import boto3

GIB = 1024 ** 3
DEFAULT_WARN_GIB = 7.0
DEFAULT_HARD_STOP_GIB = 9.0


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def client():
    return boto3.client(
        "s3",
        endpoint_url=env("R2_ENDPOINT"),
        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def bucket_usage_bytes(s3, bucket: str) -> tuple[int, int]:
    total_bytes = 0
    total_objects = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            total_objects += 1
            total_bytes += int(obj.get("Size", 0))
    return total_bytes, total_objects


def classify(total_bytes: int, warn_gib: float, hard_stop_gib: float) -> str:
    if warn_gib >= hard_stop_gib:
        raise ValueError("warn threshold must be below hard-stop threshold")
    if total_bytes >= hard_stop_gib * GIB:
        return "HARD_STOP"
    if total_bytes >= warn_gib * GIB:
        return "WARNING"
    return "SAFE"


def main() -> None:
    warn_gib = float(os.getenv("R2_STORAGE_WARN_GIB", str(DEFAULT_WARN_GIB)))
    hard_stop_gib = float(os.getenv("R2_STORAGE_HARD_STOP_GIB", str(DEFAULT_HARD_STOP_GIB)))
    bucket = env("R2_BUCKET_NAME")
    total_bytes, total_objects = bucket_usage_bytes(client(), bucket)
    status = classify(total_bytes, warn_gib, hard_stop_gib)
    result = {
        "contract": "ussy-r2-storage-guard-v1",
        "scope": "ENTIRE_BUCKET",
        "bucket": bucket,
        "total_objects": total_objects,
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / GIB, 4),
        "warning_gib": warn_gib,
        "hard_stop_gib": hard_stop_gib,
        "status": status,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if status == "WARNING":
        print(f"::warning::R2 bucket usage is {result['total_gib']} GiB (warning at {warn_gib} GiB)")
    if status == "HARD_STOP":
        print(f"::error::R2 bucket usage is {result['total_gib']} GiB; write workflow blocked at {hard_stop_gib} GiB")
        sys.exit(2)


if __name__ == "__main__":
    main()
