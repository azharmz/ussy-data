"""Read-only inventory audit for the shared R2 bucket.

Lists every object and summarizes storage by top-level namespace and two-level
prefix. This tool never calls a destructive S3 API. Institutional sponsorship
is explicitly marked protected/excluded from cleanup candidacy.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import UTC, datetime

from bootstrap_ohlcv import make_s3_client

PROTECTED_PREFIXES = ("institutional_sponsorship/",)


def list_all(s3, bucket: str) -> list[dict]:
    out: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        out.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return out
        token = page.get("NextContinuationToken")


def prefix_of(key: str, depth: int) -> str:
    parts = key.split("/")
    return "/".join(parts[: min(depth, len(parts))]) + ("/" if len(parts) > 1 else "")


def summarize(objects: list[dict], depth: int) -> list[dict]:
    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"objects": 0, "bytes": 0})
    for obj in objects:
        key = str(obj["Key"])
        p = prefix_of(key, depth)
        groups[p]["objects"] += 1
        groups[p]["bytes"] += int(obj.get("Size", 0))
    return [
        {"prefix": p, **stats}
        for p, stats in sorted(groups.items(), key=lambda item: (-item[1]["bytes"], item[0]))
    ]


def main() -> None:
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    objects = list_all(s3, bucket)
    protected = [o for o in objects if str(o["Key"]).startswith(PROTECTED_PREFIXES)]
    report = {
        "schema": "ussy-r2-inventory-audit-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "READ_ONLY",
        "bucket": bucket,
        "total_objects": len(objects),
        "total_bytes": sum(int(o.get("Size", 0)) for o in objects),
        "protected_prefixes": list(PROTECTED_PREFIXES),
        "protected_objects": len(protected),
        "protected_bytes": sum(int(o.get("Size", 0)) for o in protected),
        "by_top_level": summarize(objects, 1),
        "by_two_levels": summarize(objects, 2),
    }
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
