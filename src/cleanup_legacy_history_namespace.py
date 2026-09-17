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


def canonical_inventory(s3, bucket: str) -> dict[str, int]:
    result = {}
    result.update(list_objects(s3, bucket, HISTORY_PREFIX))
    result.update(list_objects(s3, bucket, HISTORY_MANIFEST_PREFIX))
    return result


def canonical_target(legacy_key: str) -> str:
    for legacy_prefix, canonical_prefix in LEGACY_TO_CANONICAL.items():
        if legacy_key.startswith(legacy_prefix):
            return canonical_prefix + legacy_key.removeprefix(legacy_prefix)
    raise RuntimeError(f"refusing out-of-scope key: {legacy_key}")


def verify_coverage(s3, bucket: str) -> tuple[dict[str, int], int, int]:
    legacy_all: dict[str, int] = {}
    for prefix in LEGACY_TO_CANONICAL:
        legacy_all.update(list_objects(s3, bucket, prefix))
    canonical = canonical_inventory(s3, bucket)
    failures = []
    advanced = 0
    for key, size in legacy_all.items():
        target = canonical_target(key)
        target_size = canonical.get(target)
        if target_size is None:
            failures.append(f"missing target: {key} -> {target}")
        elif target_size <= 0:
            failures.append(f"empty target: {target}")
        elif target_size != size:
            advanced += 1
    if failures:
        raise RuntimeError(
            f"legacy history coverage verification failed ({len(failures)} failure(s)):\n"
            + "\n".join(failures[:20])
        )
    return legacy_all, sum(legacy_all.values()), advanced


def main() -> None:
    args = parse_args()
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = make_s3_client()
    legacy, total_bytes, advanced = verify_coverage(s3, bucket)
    print(f"mode: {'APPLY' if args.apply else 'DRY_RUN'}", flush=True)
    print(f"verified legacy objects covered by canonical keys: {len(legacy)}", flush=True)
    print(f"legacy bytes eligible for cleanup: {total_bytes}", flush=True)
    print(f"canonical objects whose size advanced since migration: {advanced}", flush=True)
    print("scope: root backtest/ohlcv/ and backtest/manifests/ only", flush=True)
    print("audit/.../before/backtest/... is outside deletion scope", flush=True)
    if not args.apply:
        return

    keys = sorted(legacy)
    for start in range(0, len(keys), 1000):
        chunk = keys[start:start + 1000]
        for key in chunk:
            canonical_target(key)
        response = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
        )
        errors = response.get("Errors", [])
        if errors:
            raise RuntimeError(f"R2 batch deletion reported {len(errors)} error(s): {errors[:5]}")
        print(f"delete progress {min(start + len(chunk), len(keys))}/{len(keys)}", flush=True)

    remaining = {}
    for prefix in LEGACY_TO_CANONICAL:
        remaining.update(list_objects(s3, bucket, prefix))
    if remaining:
        raise RuntimeError(f"legacy root prefixes not empty after cleanup: {len(remaining)} object(s)")

    canonical = canonical_inventory(s3, bucket)
    missing = [canonical_target(key) for key in legacy if canonical.get(canonical_target(key), 0) <= 0]
    if missing:
        raise RuntimeError(f"canonical coverage lost during cleanup: {missing[:20]}")
    print("result: VERIFIED", flush=True)
    print("legacy root backtest history namespace deleted; canonical history retained", flush=True)


if __name__ == "__main__":
    main()
