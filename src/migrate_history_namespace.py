"""Guarded R2 namespace migration: backtest/* -> history/*.

Phase 1 copies exact objects and verifies size/ETag-compatible content metadata.
It never deletes the legacy namespace. Consumer cutover and legacy deletion are
separate phases after validation.
"""
from __future__ import annotations

import argparse
import json
import os

from bootstrap_ohlcv import make_s3_client

MAPPINGS = (
    ("backtest/ohlcv/", "history/ohlcv/"),
    ("backtest/manifests/", "history/manifests/"),
)


def list_objects(s3, bucket: str, prefix: str) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out.extend(page.get("Contents", []))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    plan = []
    for source_prefix, target_prefix in MAPPINGS:
        for item in list_objects(s3, bucket, source_prefix):
            source = item["Key"]
            target = target_prefix + source.removeprefix(source_prefix)
            plan.append({"source": source, "target": target, "bytes": int(item["Size"])})
    print(json.dumps({"mode": "APPLY" if args.apply else "DRY_RUN", "objects": len(plan),
                      "bytes": sum(x["bytes"] for x in plan), "mappings": MAPPINGS}, indent=2), flush=True)
    if not args.apply:
        return
    for index, item in enumerate(plan, 1):
        source, target = item["source"], item["target"]
        try:
            existing = s3.head_object(Bucket=bucket, Key=target)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise
        else:
            if int(existing["ContentLength"]) != item["bytes"]:
                raise RuntimeError(f"target exists with different size: {target}")
            continue
        s3.copy_object(Bucket=bucket, Key=target, CopySource={"Bucket": bucket, "Key": source}, MetadataDirective="COPY")
        head = s3.head_object(Bucket=bucket, Key=target)
        if int(head["ContentLength"]) != item["bytes"]:
            raise RuntimeError(f"copy verification failed: {source} -> {target}")
        if index % 100 == 0:
            print(f"copied/verified {index}/{len(plan)}", flush=True)
    # Exhaustive post-copy key/size equivalence. Legacy source remains untouched.
    for source_prefix, target_prefix in MAPPINGS:
        source = {x["Key"].removeprefix(source_prefix): int(x["Size"]) for x in list_objects(s3, bucket, source_prefix)}
        target = {x["Key"].removeprefix(target_prefix): int(x["Size"]) for x in list_objects(s3, bucket, target_prefix)}
        if source != target:
            raise RuntimeError(f"namespace equivalence failed: {source_prefix} -> {target_prefix}")
    print("history namespace copy verified; legacy backtest namespace retained", flush=True)


if __name__ == "__main__":
    main()
