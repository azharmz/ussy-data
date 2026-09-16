"""One-shot guarded cleanup for superseded shared EMA artifacts in R2.

Default mode is dry-run. Destructive mode requires --apply and protects the
current production EMA parquet plus all artifacts belonging to the current
production run. It only considers the two EMA namespaces owned by ussy-data.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from bootstrap_ohlcv import make_s3_client

POINTER_KEY = "production/indicators/ema/current.json"
PRODUCTION_PREFIX = "production/indicators/ema/runs/"
CANDIDATE_PREFIX = "validation/indicators/ema/"
REBUILD_HINT_KEY = "validation/indicators/ema/rebuild-required/current.json"


@dataclass(frozen=True)
class Obj:
    key: str
    size: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clean superseded EMA artifacts from R2")
    p.add_argument("--apply", action="store_true", help="Actually delete selected objects")
    p.add_argument("--keep-production-runs", type=int, default=2,
                   help="Keep this many newest immutable production EMA runs, including current")
    p.add_argument("--keep-candidate-runs", type=int, default=2,
                   help="Keep candidate artifacts for this many newest run IDs")
    return p.parse_args()


def read_json(s3, bucket: str, key: str) -> dict:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def list_prefix(s3, bucket: str, prefix: str) -> list[Obj]:
    out: list[Obj] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        out.extend(Obj(str(x["Key"]), int(x.get("Size", 0))) for x in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return out
        token = page.get("NextContinuationToken")


def run_token(key: str) -> str | None:
    name = key.rsplit("/", 1)[-1]
    if not name.startswith("run-"):
        return None
    for suffix in (".parquet", ".json"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return None


def run_sort_key(token: str) -> tuple[int, int]:
    # run-<github_run_id>-<attempt>; unknown forms sort oldest/fail-safe.
    parts = token.split("-")
    try:
        return int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return (0, 0)


def main() -> None:
    args = parse_args()
    if args.keep_production_runs < 2:
        raise ValueError("keep-production-runs must be >= 2 (current + rollback generation)")
    if args.keep_candidate_runs < 1:
        raise ValueError("keep-candidate-runs must be >= 1")

    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    pointer = read_json(s3, bucket, POINTER_KEY)
    current_key = str(pointer.get("parquet_key") or pointer.get("production_parquet_key") or "")
    if not current_key.startswith(PRODUCTION_PREFIX):
        raise RuntimeError(f"Refusing cleanup: invalid EMA production pointer target {current_key!r}")
    current_run = run_token(current_key)
    if not current_run:
        raise RuntimeError("Refusing cleanup: cannot derive current EMA run token")

    prod = list_prefix(s3, bucket, PRODUCTION_PREFIX)
    cand = [x for x in list_prefix(s3, bucket, CANDIDATE_PREFIX) if x.key != REBUILD_HINT_KEY]

    prod_runs = sorted({t for x in prod if (t := run_token(x.key))}, key=run_sort_key, reverse=True)
    if current_run not in prod_runs:
        raise RuntimeError("Refusing cleanup: current EMA run not found in immutable production namespace")
    keep_prod = set(prod_runs[: args.keep_production_runs]) | {current_run}

    cand_runs = sorted({t for x in cand if (t := run_token(x.key))}, key=run_sort_key, reverse=True)
    # Always retain candidate evidence for every retained production run, plus newest candidates.
    keep_cand = set(cand_runs[: args.keep_candidate_runs]) | keep_prod

    delete_prod = [x for x in prod if (t := run_token(x.key)) and t not in keep_prod]
    delete_cand = [x for x in cand if (t := run_token(x.key)) and t not in keep_cand]
    delete = delete_prod + delete_cand

    # Unknown/non-run objects are deliberately untouched. current.json and rebuild hints
    # are outside deletion selection by construction.
    report = {
        "schema": "ussy-ema-r2-cleanup-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "current_pointer": POINTER_KEY,
        "current_production_key": current_key,
        "current_run": current_run,
        "keep_production_runs": sorted(keep_prod, key=run_sort_key, reverse=True),
        "keep_candidate_runs": sorted(keep_cand, key=run_sort_key, reverse=True),
        "production_objects_seen": len(prod),
        "candidate_objects_seen": len(cand),
        "delete_objects": len(delete),
        "delete_bytes": sum(x.size for x in delete),
        "delete_keys": [x.key for x in delete],
    }
    print(json.dumps(report, indent=2), flush=True)

    if not args.apply:
        return

    # Re-read immediately before destructive work. If promotion moved, abort.
    pointer_now = read_json(s3, bucket, POINTER_KEY)
    current_now = str(pointer_now.get("parquet_key") or pointer_now.get("production_parquet_key") or "")
    if current_now != current_key:
        raise RuntimeError("EMA production pointer changed during cleanup; nothing deleted")

    for obj in delete:
        if obj.key == current_key or run_token(obj.key) == current_run:
            raise RuntimeError(f"Internal safety violation: attempted current-run deletion {obj.key}")
        s3.delete_object(Bucket=bucket, Key=obj.key)
        print(f"deleted {obj.key} ({obj.size} bytes)", flush=True)

    remaining = {x.key for x in list_prefix(s3, bucket, PRODUCTION_PREFIX)}
    if current_key not in remaining:
        raise RuntimeError("Post-delete verification failed: current EMA production artifact missing")
    print(f"EMA cleanup complete: deleted {len(delete)} objects / {sum(x.size for x in delete)} bytes", flush=True)


if __name__ == "__main__":
    main()
