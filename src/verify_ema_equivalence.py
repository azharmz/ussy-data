"""Validate an EMA candidate against long history, then promote it atomically."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os

import numpy as np
import pandas as pd

from bootstrap_ohlcv import make_s3_client
from ema_promotion import build_promoted_manifest
from ema_state import PERIODS, bootstrap_state, classify_trend, validate_state_frame

CANDIDATE_PREFIX = "validation/indicators/ema/"
PRODUCTION_PREFIX = "production/indicators/ema/runs/"
POINTER_KEY = "production/indicators/ema/current.json"
PROGRESS_EVERY = 100


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and promote shared EMA state")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--all-if-bootstrap", action="store_true", help="Legacy-compatible flag; per-security v3 always verifies bootstrapped/rebuilt IDs plus a sample")
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


def select_equivalence_ids(state: pd.DataFrame, manifest: dict, sample_size: int) -> tuple[list[str], str]:
    ids = sorted(state["security_id"].astype(str))
    required = set(map(str, manifest.get("bootstrap_security_ids", []))) | set(map(str, manifest.get("rebuild_security_ids", [])))
    unknown = required - set(ids)
    if unknown:
        raise RuntimeError(f"EMA manifest required-equivalence IDs absent from candidate: {sorted(unknown)[:20]}")
    remaining = [sid for sid in ids if sid not in required]
    sampled = remaining[: min(sample_size, len(remaining))]
    selected = sorted(required | set(sampled))
    if len(required) == len(ids):
        mode = "all_required"
    elif required:
        mode = "required_plus_sample"
    else:
        mode = "sample"
    return selected, mode


def equivalence_report(s3, bucket: str, state: pd.DataFrame, manifest: dict, args: argparse.Namespace) -> dict:
    selected, mode = select_equivalence_ids(state, manifest, args.sample_size)
    required_count = len(set(manifest.get("bootstrap_security_ids", [])) | set(manifest.get("rebuild_security_ids", [])))
    print(f"EMA equivalence: mode={mode}, verifying {len(selected)} securities ({required_count} required)", flush=True)
    state_by_id = state.set_index("security_id")
    max_abs = 0.0
    max_rel = 0.0
    mismatches = 0
    failures: list[str] = []
    total = len(selected)
    for done, security_id in enumerate(selected, start=1):
        reference = bootstrap_state(history(s3, bucket, security_id), security_id)
        persisted = state_by_id.loc[security_id]
        if pd.Timestamp(reference["as_of_date"]).normalize() != pd.Timestamp(persisted["as_of_date"]).normalize():
            failures.append(f"{security_id}: as_of_date mismatch")
        else:
            if classify_trend(reference) != classify_trend(persisted):
                mismatches += 1
            for field in ["last_price", *(f"ema{period}" for period in PERIODS)]:
                ref = float(reference[field])
                got = float(persisted[field])
                diff = abs(got - ref)
                rel = diff / abs(ref) if ref else diff
                max_abs = max(max_abs, diff)
                max_rel = max(max_rel, rel)
                if not np.isclose(got, ref, rtol=args.rtol, atol=args.atol):
                    failures.append(f"{security_id}: {field} persisted={got:.12g} reference={ref:.12g}")
        if done == 1 or done == total or done % PROGRESS_EVERY == 0:
            print(f"EMA equivalence progress: {done}/{total}; numeric_failures={len(failures)}; classification_mismatches={mismatches}", flush=True)
    report = {
        "verified": len(selected), "required_verified": required_count, "mode": mode,
        "rtol": args.rtol, "atol": args.atol, "max_abs_error": max_abs,
        "max_relative_error": max_rel, "classification_mismatches": mismatches,
        "numeric_failures": len(failures),
    }
    print(json.dumps(report, indent=2), flush=True)
    if failures or mismatches:
        for failure in failures[:20]:
            print(f"::error title=EMA equivalence failure::{failure}")
        raise RuntimeError("EMA equivalence failed; production pointer unchanged")
    return report


def main() -> None:
    args = parse_args()
    if args.sample_size < 1:
        raise ValueError("sample-size must be >= 1")
    s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
    rid = run_id()
    candidate_key = f"{CANDIDATE_PREFIX}{rid}.parquet"
    candidate_manifest_key = f"{CANDIDATE_PREFIX}{rid}.json"
    print(f"EMA equivalence: loading candidate {rid}", flush=True)
    manifest = read_json(s3, bucket, candidate_manifest_key)
    manifest["candidate_manifest_key"] = candidate_manifest_key
    if manifest.get("candidate_parquet_key") != candidate_key:
        raise RuntimeError("EMA candidate manifest/key mismatch")
    body = s3.get_object(Bucket=bucket, Key=candidate_key)["Body"].read()
    digest = hashlib.sha256(body).hexdigest()
    if digest != manifest.get("candidate_sha256"):
        raise RuntimeError("EMA candidate checksum mismatch")
    state = validate_state_frame(pd.read_parquet(io.BytesIO(body), engine="pyarrow"))
    if len(state) != manifest.get("securities") or set(state["security_id"]) != set(manifest.get("security_ids", [])):
        raise RuntimeError("EMA candidate manifest/data mismatch")
    ready = read_json(s3, bucket, "production/ready/current.json")
    lineage = (manifest.get("source_ready_parquet_key"), manifest.get("source_ready_sha256"))
    if lineage != (ready.get("parquet_key"), ready.get("sha256")):
        raise RuntimeError("EMA candidate is not aligned with current ready source")

    report = equivalence_report(s3, bucket, state, manifest, args)
    production_key = f"{PRODUCTION_PREFIX}{rid}.parquet"
    print(f"EMA promotion: equivalence PASS; uploading immutable run {production_key}", flush=True)
    s3.put_object(Bucket=bucket, Key=production_key, Body=body, ContentType="application/vnd.apache.parquet")
    uploaded = s3.get_object(Bucket=bucket, Key=production_key)["Body"].read()
    if hashlib.sha256(uploaded).hexdigest() != digest:
        raise RuntimeError("EMA immutable production upload verification failed")
    print("EMA promotion: immutable upload checksum verified", flush=True)
    ready_after = read_json(s3, bucket, "production/ready/current.json")
    if lineage != (ready_after.get("parquet_key"), ready_after.get("sha256")):
        raise RuntimeError("Ready source changed before EMA promotion; pointer unchanged")
    promoted = build_promoted_manifest(manifest, production_key, digest, report)
    print("EMA promotion: lineage rechecked; writing production current pointer LAST", flush=True)
    s3.put_object(Bucket=bucket, Key=POINTER_KEY, Body=json.dumps(promoted, indent=2).encode(), ContentType="application/json")
    print("EMA promotion: production pointer updated", flush=True)
    print(json.dumps({k: v for k, v in promoted.items() if k != "security_ids"}, indent=2))


if __name__ == "__main__":
    main()
