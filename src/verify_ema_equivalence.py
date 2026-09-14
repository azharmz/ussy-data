"""Compare persisted EMA state with long-history reference calculations."""
from __future__ import annotations

import argparse
import io
import json
import os

import numpy as np
import pandas as pd

from bootstrap_ohlcv import make_s3_client
from ema_state import PERIODS, bootstrap_state, classify_trend
from load_ema_state import load_ema_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify shared EMA state against long-history reference")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--all-if-bootstrap", action="store_true")
    parser.add_argument("--rtol", type=float, default=1e-10)
    parser.add_argument("--atol", type=float, default=1e-8)
    return parser.parse_args()


def _history(s3, bucket: str, security_id: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=f"backtest/ohlcv/{security_id}.parquet")["Body"].read()
    return pd.read_parquet(io.BytesIO(body), engine="pyarrow")


def main() -> None:
    args = parse_args()
    if args.sample_size < 1:
        raise ValueError("sample-size must be >= 1")
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    state, manifest = load_ema_state(s3, bucket)
    ready_manifest = json.loads(s3.get_object(Bucket=bucket, Key="production/ready/current.json")["Body"].read())
    if (manifest["source_ready_parquet_key"], manifest["source_ready_sha256"]) != (ready_manifest["parquet_key"], ready_manifest["sha256"]):
        raise RuntimeError("EMA state is not aligned with current ready source")

    ids = sorted(state["security_id"].astype(str))
    verify_all = args.all_if_bootstrap and (manifest.get("bootstrap_count", 0) > 0 or manifest.get("rebuild_count", 0) > 0)
    selected = ids if verify_all else ids[: min(args.sample_size, len(ids))]
    state_by_id = state.set_index("security_id")
    max_abs = 0.0
    max_rel = 0.0
    classification_mismatches = 0
    failures: list[str] = []

    for security_id in selected:
        reference = bootstrap_state(_history(s3, bucket, security_id), security_id)
        persisted = state_by_id.loc[security_id]
        if pd.Timestamp(reference["as_of_date"]).normalize() != pd.Timestamp(persisted["as_of_date"]).normalize():
            failures.append(f"{security_id}: as_of_date mismatch")
            continue
        if classify_trend(reference) != classify_trend(persisted):
            classification_mismatches += 1
        for field in ["last_price", *(f"ema{period}" for period in PERIODS)]:
            ref = float(reference[field])
            got = float(persisted[field])
            diff = abs(got - ref)
            rel = diff / abs(ref) if ref else diff
            max_abs = max(max_abs, diff)
            max_rel = max(max_rel, rel)
            if not np.isclose(got, ref, rtol=args.rtol, atol=args.atol):
                failures.append(f"{security_id}: {field} persisted={got:.12g} reference={ref:.12g}")

    report = {
        "verified": len(selected),
        "mode": "all" if verify_all else "sample",
        "rtol": args.rtol,
        "atol": args.atol,
        "max_abs_error": max_abs,
        "max_relative_error": max_rel,
        "classification_mismatches": classification_mismatches,
        "numeric_failures": len(failures),
    }
    print(json.dumps(report, indent=2))
    if failures or classification_mismatches:
        for failure in failures[:20]:
            print(f"::error title=EMA equivalence failure::{failure}")
        raise RuntimeError("EMA equivalence verification failed")


if __name__ == "__main__":
    main()
