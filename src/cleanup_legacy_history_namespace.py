from __future__ import annotations

import argparse
import os

from bootstrap_ohlcv import HISTORY_MANIFEST_PREFIX, HISTORY_PREFIX, make_s3_client

LEGACY_TO_CANONICAL = {
    "backtest/ohlcv/": HISTORY_PREFIX,
    "backtest/manifests/": HISTORY_MANIFEST_PREFIX,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Delete covered root legacy backtest history objects")
    parser.add_argument("--apply", action="store_true", help="Delete only after exhaustive canonical-key coverage verification")
    return parser.parse_args()


def list_objects(s3, bucket: str, prefix: str) -> dict[str, int]:
    result: dict[str, int] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            result[str(item["Key"])] = int(item["Size"])
    return result


def verify_coverage(s3, bucket: str) -> tuple[dict[str, int], int, int]:
    legacy_all: dict[str, int] = {}
    total_bytes = 0
    advanced = 0
    failures: list[str] = []
    for legacy_prefix, canonical_prefix in LEGACY_TO_CANONICAL.items():
        legacy = list_objects(s3, bucket, legacy_prefix)
        canonical = list_objects(s3, bucket, canonical_prefix)
        for key, size in legacy.items():
            target = canonical_prefix + key.removeprefix(legacy_prefix)
            target_size = canonical.get(target)
            if target_size is None:
                failures.append(f"missing target: {key} -> {target}")
            elif target_size <= 0:
                failures.append(f"empty target: {target}")
            elif target_size != size:
                advanced += 1
        legacy_all.update(legacy)
        total_bytes += sum(legacy.values())
    if failures:
        preview = "\n".join(failures[:20])
        raise RuntimeError(f"legacy history coverage verification failed ({len(failures)} failure(s)):\n{preview}")
    return legacy_all, total_bytes, advanced


def canonical_target(legacy_key: str) -> str:
    for legacy_prefix, canonical_prefix in LEGACY_TO_CANONICAL.items():
        if legacy_key.startswith(legacy_prefix):
            return canonical_prefix + legacy_key.removeprefix(legacy_prefix)
    raise RuntimeError(f"refusing out-of-scope key: {legacy_key}")


def main() -> None:
    args = parse_args()
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = make_s3_client()
    legacy, total_bytes, advanced = verify_coverage(s3, bucket)
    mode = "APPLY" if args.apply else "DRY_RUN"
    print(f"mode: {mode}", flush=True)
    print(f"verified legacy objects covered by canonical keys: {len(legacy)}", flush=True)
    print(f"legacy bytes eligible for cleanup: {total_bytes}", flush=True)
    print(f"canonical objects whose size advanced since migration: {advanced}", flush=True)
    print("scope: root backtest/ohlcv/ and backtest/manifests/ only", flush=True)
    print("audit/.../before/backtest/... is outside deletion scope", flush=True)
    if not args.apply:
        return
    for index, key in enumerate(sorted(legacy), 1):
        canonical_target(key)  # scope assertion before every destructive call
        s3.delete_object(Bucket=bucket, Key=key)
        if index % 100 == 0 or index == len(legacy):
            print(f"delete progress {index}/{len(legacy)}", flush=True)
    remaining = {}
    for prefix in LEGACY_TO_CANONICAL:
        remaining.update(list_objects(s3, bucket, prefix))
    if remaining:
        raise RuntimeError(f"legacy root prefixes not empty after cleanup: {len(remaining)} object(s)")
    for legacy_key in legacy:
        target = canonical_target(legacy_key)
        actual = int(s3.head_object(Bucket=bucket, Key=target).get("ContentLength", 0))
        if actual <= 0:
            raise RuntimeError(f"canonical object missing/empty after cleanup: {target}")
    print("result: VERIFIED", flush=True)
    print("legacy root backtest history namespace deleted; canonical history retained", flush=True)


if __name__ == "__main__":
    main()
