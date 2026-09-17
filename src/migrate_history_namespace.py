"""Guarded R2 namespace migration: backtest/* -> history/*.

Phase 1 copies exact objects and verifies size metadata. It never deletes the
legacy namespace. Consumer cutover and legacy deletion are separate phases.
Copy work is bounded-parallel and idempotent so interrupted runs can resume.
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from bootstrap_ohlcv import make_s3_client

MAPPINGS = (
    ("backtest/ohlcv/", "history/ohlcv/"),
    ("backtest/manifests/", "history/manifests/"),
)
DEFAULT_WORKERS = 16


def list_objects(s3, bucket: str, prefix: str) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out.extend(page.get("Contents", []))
    return out


def _not_found(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def copy_or_verify(s3, bucket: str, item: dict) -> str:
    source, target, expected = item["source"], item["target"], item["bytes"]
    try:
        existing = s3.head_object(Bucket=bucket, Key=target)
    except Exception as exc:
        if not _not_found(exc):
            raise
    else:
        if int(existing["ContentLength"]) != expected:
            raise RuntimeError(f"target exists with different size: {target}")
        return "reused"

    s3.copy_object(
        Bucket=bucket,
        Key=target,
        CopySource={"Bucket": bucket, "Key": source},
        MetadataDirective="COPY",
    )
    head = s3.head_object(Bucket=bucket, Key=target)
    if int(head["ContentLength"]) != expected:
        raise RuntimeError(f"copy verification failed: {source} -> {target}")
    return "copied"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 32:
        raise SystemExit("--workers must be between 1 and 32")

    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    plan = []
    for source_prefix, target_prefix in MAPPINGS:
        for item in list_objects(s3, bucket, source_prefix):
            source = item["Key"]
            target = target_prefix + source.removeprefix(source_prefix)
            plan.append({"source": source, "target": target, "bytes": int(item["Size"])})

    print(json.dumps({
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "objects": len(plan),
        "bytes": sum(x["bytes"] for x in plan),
        "workers": args.workers,
        "mappings": MAPPINGS,
    }, indent=2), flush=True)
    if not args.apply:
        return

    counts = {"copied": 0, "reused": 0}
    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(copy_or_verify, s3, bucket, item): item for item in plan}
        for future in as_completed(futures):
            item = futures[future]
            try:
                outcome = future.result()
            except Exception as exc:
                raise RuntimeError(f"migration failed for {item['source']} -> {item['target']}: {exc}") from exc
            counts[outcome] += 1
            completed += 1
            if completed % 100 == 0 or completed == len(plan):
                print(
                    f"copied/verified {completed}/{len(plan)} "
                    f"(copied={counts['copied']}, reused={counts['reused']})",
                    flush=True,
                )

    # Exhaustive post-copy key/size equivalence. Legacy source remains untouched.
    for source_prefix, target_prefix in MAPPINGS:
        source = {
            x["Key"].removeprefix(source_prefix): int(x["Size"])
            for x in list_objects(s3, bucket, source_prefix)
        }
        target = {
            x["Key"].removeprefix(target_prefix): int(x["Size"])
            for x in list_objects(s3, bucket, target_prefix)
        }
        if source != target:
            raise RuntimeError(f"namespace equivalence failed: {source_prefix} -> {target_prefix}")

    print(json.dumps({"result": "VERIFIED", **counts}, indent=2), flush=True)
    print("history namespace copy verified; legacy backtest namespace retained", flush=True)


if __name__ == "__main__":
    main()
