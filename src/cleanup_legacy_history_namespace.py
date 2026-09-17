from __future__ import annotations

import argparse
import os

from bootstrap_ohlcv import HISTORY_MANIFEST_PREFIX, HISTORY_PREFIX, make_s3_client

LEGACY_TO_CANONICAL = {
    "backtest/ohlcv/": HISTORY_PREFIX,
    "backtest/manifests/": HISTORY_MANIFEST_PREFIX,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Delete verified root legacy backtest history objects")
    parser.add_argument("--apply", action="store_true", help="Delete only after exhaustive key+size verification")
    return parser.parse_args()


def list_objects(s3, bucket: str, prefix: str) -> dict[str, int]:
    result: dict[str, int] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            result[str(item["Key"])] = int(item["Size"])
    return result


def verify_mirror(s3, bucket: str) -> tuple[dict[str, int], int]:
    legacy_all: dict[str, int] = {}
    total_bytes = 0
    mismatches: list[str] = []
    for legacy_prefix, canonical_prefix in LEGACY_TO_CANONICAL.items():
        legacy = list_objects(s3, bucket, legacy_prefix)
        canonical = list_objects(s3, bucket, canonical_prefix)
        for key, size in legacy.items():
            suffix = key.removeprefix(legacy_prefix)
            target = canonical_prefix + suffix
            if target not in canonical:
                mismatches.append(f"missing target: {key} -> {target}")
            elif canonical[target] != size:
                mismatches.append(f"size mismatch: {key}={size} {target}={canonical[target]}")
        legacy_all.update(legacy)
        total_bytes += sum(legacy.values())
    if mismatches:
        preview = "\n".join(mismatches[:20])
        raise RuntimeError(f"legacy history mirror verification failed ({len(mismatches)} mismatch(es)):\n{preview}")
    return legacy_all, total_bytes


def main() -> None:
    args = parse_args()
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = make_s3_client()
    legacy, total_bytes = verify_mirror(s3, bucket)
    mode = "APPLY" if args.apply else "DRY_RUN"
    print(f"mode: {mode}", flush=True)
    print(f"verified legacy objects: {len(legacy)}", flush=True)
    print(f"verified legacy bytes: {total_bytes}", flush=True)
    print("scope: root backtest/ohlcv/ and backtest/manifests/ only", flush=True)
    print("audit/.../before/backtest/... is outside deletion scope", flush=True)
    if not args.apply:
        return
    for index, key in enumerate(sorted(legacy), 1):
        if not any(key.startswith(prefix) for prefix in LEGACY_TO_CANONICAL):
            raise RuntimeError(f"refusing out-of-scope deletion: {key}")
        s3.delete_object(Bucket=bucket, Key=key)
        if index % 100 == 0 or index == len(legacy):
            print(f"delete progress {index}/{len(legacy)}", flush=True)
    remaining = {}
    for prefix in LEGACY_TO_CANONICAL:
        remaining.update(list_objects(s3, bucket, prefix))
    if remaining:
        raise RuntimeError(f"legacy root prefixes not empty after cleanup: {len(remaining)} object(s)")
    # Re-run canonical presence checks against the captured legacy set after deletion.
    for legacy_key, expected_size in legacy.items():
        for legacy_prefix, canonical_prefix in LEGACY_TO_CANONICAL.items():
            if legacy_key.startswith(legacy_prefix):
                target = canonical_prefix + legacy_key.removeprefix(legacy_prefix)
                actual = s3.head_object(Bucket=bucket, Key=target).get("ContentLength")
                if int(actual) != expected_size:
                    raise RuntimeError(f"canonical object changed during cleanup: {target}")
                break
    print("result: VERIFIED", flush=True)
    print("legacy root backtest history namespace deleted; canonical history retained", flush=True)


if __name__ == "__main__":
    main()
