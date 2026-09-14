"""Validate a close-basis EMA candidate against long history, then promote it atomically."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from bootstrap_ohlcv import make_s3_client
from ema_close_state import PERIODS, PRICE_BASIS, bootstrap_state, classify_trend, validate_state_frame
from load_ema_close_state import PROMOTION_POLICY

CANDIDATE_PREFIX = "validation/indicators/ema-close/"
PRODUCTION_PREFIX = "production/indicators/ema-close/runs/"
POINTER_KEY = "production/indicators/ema-close/current.json"
MAX_WORKERS = 16


def parse_args():
    parser = argparse.ArgumentParser(description="Validate and promote close-basis shared EMA state")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--all-if-bootstrap", action="store_true")
    parser.add_argument("--rtol", type=float, default=1e-10)
    parser.add_argument("--atol", type=float, default=1e-8)
    return parser.parse_args()


def run_id() -> str:
    return f"run-{os.getenv('GITHUB_RUN_ID', 'local')}-{os.getenv('GITHUB_RUN_ATTEMPT', '1')}"


def read_json(s3, bucket: str, key: str) -> dict:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def history(s3, bucket: str, security_id: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=f"backtest/ohlcv/{security_id}.parquet")["Body"].read()
    return pd.read_parquet(io.BytesIO(body), engine="pyarrow")


def equivalence_report(s3, bucket: str, state: pd.DataFrame, manifest: dict, args) -> dict:
    ids = sorted(state["security_id"].astype(str))
    verify_all = args.all_if_bootstrap and (manifest.get("bootstrap_count", 0) > 0 or manifest.get("rebuild_count", 0) > 0)
    selected = ids if verify_all else ids[: min(args.sample_size, len(ids))]
    state_by_id = state.set_index("security_id")

    def check(security_id: str):
        reference = bootstrap_state(history(s3, bucket, security_id), security_id)
        persisted = state_by_id.loc[security_id]
        failures = []
        mismatch = 0
        max_abs = 0.0
        max_rel = 0.0
        if pd.Timestamp(reference["as_of_date"]).normalize() != pd.Timestamp(persisted["as_of_date"]).normalize():
            failures.append(f"{security_id}: as_of_date mismatch")
            return max_abs, max_rel, mismatch, failures
        if classify_trend(reference) != classify_trend(persisted):
            mismatch = 1
        for field in ["last_price", *(f"ema{p}" for p in PERIODS)]:
            ref = float(reference[field]); got = float(persisted[field])
            diff = abs(got - ref); rel = diff / abs(ref) if ref else diff
            max_abs = max(max_abs, diff); max_rel = max(max_rel, rel)
            if not np.isclose(got, ref, rtol=args.rtol, atol=args.atol):
                failures.append(f"{security_id}: {field} persisted={got:.12g} reference={ref:.12g}")
        return max_abs, max_rel, mismatch, failures

    max_abs = 0.0; max_rel = 0.0; mismatches = 0; failures = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(selected))) as pool:
        for abs_err, rel_err, mismatch, local_failures in pool.map(check, selected):
            max_abs = max(max_abs, abs_err); max_rel = max(max_rel, rel_err)
            mismatches += mismatch; failures.extend(local_failures)

    report = {
        "verified": len(selected), "mode": "all" if verify_all else "sample",
        "rtol": args.rtol, "atol": args.atol, "max_abs_error": max_abs,
        "max_relative_error": max_rel, "classification_mismatches": mismatches,
        "numeric_failures": len(failures),
    }
    print(json.dumps(report, indent=2))
    if failures or mismatches:
        for failure in failures[:20]:
            print(f"::error title=EMA close equivalence failure::{failure}")
        raise RuntimeError("EMA close equivalence failed; production pointer unchanged")
    return report


def promoted_manifest(candidate: dict, production_key: str, digest: str, equivalence: dict) -> dict:
    if candidate.get("status") != "CANDIDATE_NOT_PRODUCTION_APPROVED":
        raise ValueError("Candidate status is not promotable")
    if candidate.get("price_basis") != PRICE_BASIS or candidate.get("periods") != list(PERIODS):
        raise ValueError("EMA close candidate contract mismatch")
    return {
        "schema_version": 1, "created_at": datetime.now(UTC).isoformat(), "price_basis": PRICE_BASIS,
        "periods": list(PERIODS), "securities": candidate["securities"], "security_ids": candidate["security_ids"],
        "parquet_key": production_key, "sha256": digest,
        "source_ready_parquet_key": candidate["source_ready_parquet_key"], "source_ready_sha256": candidate["source_ready_sha256"],
        "source_ready_created_at": candidate.get("source_ready_created_at"), "update_method": candidate["update_method"],
        "bootstrap_count": candidate.get("bootstrap_count", 0), "recursive_count": candidate.get("recursive_count", 0),
        "unchanged_count": candidate.get("unchanged_count", 0), "rebuild_count": candidate.get("rebuild_count", 0),
        "as_of_date_min": candidate["as_of_date_min"], "as_of_date_max": candidate["as_of_date_max"],
        "equivalence": equivalence, "promotion_policy": PROMOTION_POLICY,
        "candidate_manifest_key": candidate.get("candidate_manifest_key"),
    }


def main() -> None:
    args = parse_args()
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    rid = run_id()
    candidate_key = f"{CANDIDATE_PREFIX}{rid}.parquet"
    candidate_manifest_key = f"{CANDIDATE_PREFIX}{rid}.json"
    manifest = read_json(s3, bucket, candidate_manifest_key)
    manifest["candidate_manifest_key"] = candidate_manifest_key
    if manifest.get("candidate_parquet_key") != candidate_key:
        raise RuntimeError("EMA close candidate manifest/key mismatch")
    body = s3.get_object(Bucket=bucket, Key=candidate_key)["Body"].read()
    digest = hashlib.sha256(body).hexdigest()
    if digest != manifest.get("candidate_sha256"):
        raise RuntimeError("EMA close candidate checksum mismatch")
    state = validate_state_frame(pd.read_parquet(io.BytesIO(body), engine="pyarrow"))
    ready = read_json(s3, bucket, "production/ready/current.json")
    lineage = (manifest.get("source_ready_parquet_key"), manifest.get("source_ready_sha256"))
    if lineage != (ready.get("parquet_key"), ready.get("sha256")):
        raise RuntimeError("EMA close candidate is not aligned with current ready source")
    report = equivalence_report(s3, bucket, state, manifest, args)
    production_key = f"{PRODUCTION_PREFIX}{rid}.parquet"
    s3.put_object(Bucket=bucket, Key=production_key, Body=body, ContentType="application/vnd.apache.parquet")
    uploaded = s3.get_object(Bucket=bucket, Key=production_key)["Body"].read()
    if hashlib.sha256(uploaded).hexdigest() != digest:
        raise RuntimeError("EMA close immutable production upload verification failed")
    ready_after = read_json(s3, bucket, "production/ready/current.json")
    if lineage != (ready_after.get("parquet_key"), ready_after.get("sha256")):
        raise RuntimeError("Ready source changed before EMA close promotion; pointer unchanged")
    promoted = promoted_manifest(manifest, production_key, digest, report)
    s3.put_object(Bucket=bucket, Key=POINTER_KEY, Body=json.dumps(promoted, indent=2).encode(), ContentType="application/json")
    print(json.dumps({k: v for k, v in promoted.items() if k != "security_ids"}, indent=2))


if __name__ == "__main__":
    main()
