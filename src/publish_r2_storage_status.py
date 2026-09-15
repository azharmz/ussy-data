#!/usr/bin/env python3
"""Publish read-only whole-bucket R2 capacity status for the USSY web monitor."""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from r2_storage_guard import GIB, DEFAULT_HARD_STOP_GIB, DEFAULT_WARN_GIB, classify, client, env

OUTPUT_KEY = "web/r2-storage-status.json"
DEFAULT_LOCAL_OUTPUT = "web/r2/status.json"
CONTRACT = "ussy-r2-storage-status-v1"


def scan_bucket(s3, bucket: str):
    total_bytes = 0
    total_objects = 0
    prefixes = defaultdict(lambda: {"bytes": 0, "objects": 0})
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = str(obj.get("Key", ""))
            size = int(obj.get("Size", 0))
            prefix = key.split("/", 1)[0] if "/" in key else "(root)"
            total_bytes += size
            total_objects += 1
            prefixes[prefix]["bytes"] += size
            prefixes[prefix]["objects"] += 1
    return total_bytes, total_objects, prefixes


def build_document(s3, bucket: str, warn_gib: float, hard_stop_gib: float):
    total_bytes, total_objects, raw_prefixes = scan_bucket(s3, bucket)
    status = classify(total_bytes, warn_gib, hard_stop_gib)
    hard_bytes = hard_stop_gib * GIB
    prefixes = [{
        "prefix": name,
        "objects": values["objects"],
        "bytes": values["bytes"],
        "gib": round(values["bytes"] / GIB, 4),
        "percent_of_bucket": round((values["bytes"] / total_bytes * 100) if total_bytes else 0, 2),
    } for name, values in raw_prefixes.items()]
    prefixes.sort(key=lambda row: (-row["bytes"], row["prefix"]))
    return {
        "contract": CONTRACT, "scope": "ENTIRE_BUCKET",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket": bucket, "status": status, "write_allowed": status != "HARD_STOP",
        "total_objects": total_objects, "total_bytes": total_bytes,
        "total_gib": round(total_bytes / GIB, 4), "warning_gib": warn_gib,
        "hard_stop_gib": hard_stop_gib,
        "headroom_to_hard_stop_gib": round(max(0, hard_bytes - total_bytes) / GIB, 4),
        "percent_of_hard_stop": round(total_bytes / hard_bytes * 100, 2) if hard_bytes else None,
        "prefixes": prefixes,
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-output", default=DEFAULT_LOCAL_OUTPUT)
    parser.add_argument("--no-r2-publish", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    warn_gib = float(os.getenv("R2_STORAGE_WARN_GIB", str(DEFAULT_WARN_GIB)))
    hard_stop_gib = float(os.getenv("R2_STORAGE_HARD_STOP_GIB", str(DEFAULT_HARD_STOP_GIB)))
    bucket = env("R2_BUCKET_NAME")
    s3 = client()
    doc = build_document(s3, bucket, warn_gib, hard_stop_gib)
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    local_path = Path(args.local_output)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(text, encoding="utf-8")
    if not args.no_r2_publish:
        s3.put_object(Bucket=bucket, Key=OUTPUT_KEY, Body=text.encode("utf-8"), ContentType="application/json", CacheControl="no-cache")
        print(f"Published s3://{bucket}/{OUTPUT_KEY}")
    print(text, end="")
    print(f"Wrote {local_path}")


if __name__ == "__main__":
    main()
