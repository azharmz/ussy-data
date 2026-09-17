"""Read-only forensic diff for a READY same-day SHA conflict.

Compares the canonical READY parquet referenced by current.json with the
candidate that export_ready.py would build from current rolling/readiness
inputs. Never writes R2.
"""
from __future__ import annotations

import hashlib
import io
import json
import os

import pandas as pd

from bootstrap_ohlcv import make_s3_client
from export_ready import filter_rolling, finalized_ready_frame, select_ready_ids, terminal_date_summary
from us_market_finalization import finalized_through

KEY_COLS = ["security_id", "date"]
VALUE_COLS = ["ticker", "open", "high", "low", "close", "adj_close", "volume"]


def main() -> None:
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]

    def read(key: str) -> bytes:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    current_ready = json.loads(read("production/ready/current.json"))
    current = json.loads(read("universe/current.json"))
    snapshot = current["snapshot_date"]
    readiness = json.loads(read("production/rolling/readiness.json"))
    membership = json.loads(read(f"universe/membership/{snapshot}.json"))
    selected = select_ready_ids(readiness, membership, snapshot)

    rolling = pd.read_parquet(io.BytesIO(read("production/rolling/latest.parquet")))
    candidate = filter_rolling(rolling, readiness, selected)
    candidate = finalized_ready_frame(candidate, cutoff=finalized_through())
    terminal = terminal_date_summary(candidate)

    canonical_raw = read(current_ready["parquet_key"])
    canonical = pd.read_parquet(io.BytesIO(canonical_raw))
    canonical["date"] = pd.to_datetime(canonical["date"], errors="raise")

    buf = io.BytesIO()
    candidate.to_parquet(buf, engine="pyarrow", index=False, compression="zstd")
    candidate_raw = buf.getvalue()

    old = canonical.set_index(KEY_COLS).sort_index()
    new = candidate.set_index(KEY_COLS).sort_index()
    old_keys, new_keys = set(old.index), set(new.index)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)

    changed = []
    changed_securities = set()
    changed_columns = {}
    for key in common:
        a, b = old.loc[key], new.loc[key]
        cols = []
        for col in VALUE_COLS:
            av, bv = a[col], b[col]
            if pd.isna(av) and pd.isna(bv):
                continue
            if av != bv:
                cols.append({"column": col, "canonical": None if pd.isna(av) else av.item() if hasattr(av, "item") else av,
                             "candidate": None if pd.isna(bv) else bv.item() if hasattr(bv, "item") else bv})
                changed_columns[col] = changed_columns.get(col, 0) + 1
        if cols:
            sid, date = key
            changed_securities.add(str(sid))
            changed.append({"security_id": str(sid), "date": pd.Timestamp(date).date().isoformat(), "changes": cols})

    affected = {str(k[0]) for k in added + removed} | changed_securities
    report = {
        "status": "DIFF_COMPLETE",
        "mode": "READ_ONLY",
        "canonical": {
            "key": current_ready["parquet_key"],
            "manifest_sha256": current_ready.get("sha256"),
            "actual_sha256": hashlib.sha256(canonical_raw).hexdigest(),
            "rows": len(canonical),
            "securities": int(canonical["security_id"].nunique()),
            "as_of_date": current_ready.get("as_of_date"),
        },
        "candidate": {
            "sha256": hashlib.sha256(candidate_raw).hexdigest(),
            "rows": len(candidate),
            "securities": int(candidate["security_id"].nunique()),
            **terminal,
        },
        "diff": {
            "added_rows": len(added),
            "removed_rows": len(removed),
            "changed_rows": len(changed),
            "affected_security_count": len(affected),
            "affected_security_ids": sorted(affected),
            "changed_columns": changed_columns,
            "added_examples": [{"security_id": str(k[0]), "date": pd.Timestamp(k[1]).date().isoformat()} for k in added[:50]],
            "removed_examples": [{"security_id": str(k[0]), "date": pd.Timestamp(k[1]).date().isoformat()} for k in removed[:50]],
            "changed_examples": changed[:100],
        },
    }
    print(json.dumps(report, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
