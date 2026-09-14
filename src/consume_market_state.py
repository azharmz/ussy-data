from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import pandas as pd

from bootstrap_ohlcv import make_s3_client

CONSUMER_VERSION = "50-market-state-consumer-v1"
INDEX_POINTER = "market/indexes/official.json"
STATE_POINTER = "market/state/official.json"


def get_bytes(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def get_json(s3, bucket: str, key: str) -> dict:
    return json.loads(get_bytes(s3, bucket, key))


def verify_sha(payload: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise RuntimeError(f"sha256 mismatch for {label}: {actual} != {expected}")


def load_index_series(s3, bucket: str, manifest: dict):
    from canslim_research.market_state_v1 import IndexBar
    series = {}
    for index_id, meta in manifest["indexes"].items():
        payload = get_bytes(s3, bucket, meta["object_key"])
        verify_sha(payload, meta["sha256"], meta["object_key"])
        frame = pd.read_parquet(io.BytesIO(payload))
        if set(frame["index_id"]) != {index_id}:
            raise RuntimeError(f"identity mismatch: {index_id}")
        bars = [IndexBar(str(r.date), float(r.low), float(r.close), None if pd.isna(r.volume) else float(r.volume)) for r in frame.itertuples()]
        series[index_id] = bars
    return series


def replay(index_series):
    from canslim_research.market_state_v1 import MarketState, classify_market
    dates = sorted({b.date for bars in index_series.values() for b in bars})
    prior = MarketState.NOT_EVALUABLE.value
    history = []
    for d in dates:
        partial = {k: [b for b in bars if b.date <= d] for k, bars in index_series.items()}
        c = classify_market(index_series=partial, prior_state=prior,
                            leadership_confirming=None, weakening_confirmed=None,
                            correction_reset=False)
        prior = c.state
        history.append(c)
    if not history:
        raise RuntimeError("no market-state observations")
    return history


def main():
    checkout = os.environ.get("CANSLIM_RESEARCH_PATH", "canslim-research/src")
    sys.path.insert(0, checkout)
    s3 = make_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]

    pointer_raw = get_bytes(s3, bucket, INDEX_POINTER)
    index_pointer = json.loads(pointer_raw)
    manifest_raw = get_bytes(s3, bucket, index_pointer["manifest_key"])
    verify_sha(manifest_raw, index_pointer["manifest_sha256"], index_pointer["manifest_key"])
    manifest = json.loads(manifest_raw)
    if manifest.get("source_contract_version") != "47-market-input-data-contract-v1":
        raise RuntimeError("unexpected market input contract")

    series = load_index_series(s3, bucket, manifest)
    history = replay(series)
    latest = history[-1]
    published_at = datetime.now(UTC).isoformat()
    run_id = os.environ.get("GITHUB_RUN_ID", published_at.replace(":", ""))
    out = {
        "consumer_version": CONSUMER_VERSION,
        "classifier_version": latest.version,
        "asof_date": latest.asof_date,
        "state": latest.state,
        "reason": latest.reason,
        "leadership_confirming": latest.leadership_confirming,
        "weakening_confirmed": latest.weakening_confirmed,
        "index_evidence": [asdict(x) for x in latest.index_evidence],
        "source_index_run_id": index_pointer["run_id"],
        "source_manifest_key": index_pointer["manifest_key"],
        "source_manifest_sha256": index_pointer["manifest_sha256"],
        "published_at": published_at,
        "pit_note": "classification uses only bars dated on/before asof_date; leadership/weakening inputs intentionally unset",
    }
    payload = json.dumps(out, sort_keys=True, indent=2).encode()
    digest = hashlib.sha256(payload).hexdigest()
    run_key = f"market/state/runs/{run_id}.json"
    s3.put_object(Bucket=bucket, Key=run_key, Body=payload, ContentType="application/json", Metadata={"sha256": digest})
    official = {
        "consumer_version": CONSUMER_VERSION,
        "run_id": run_id,
        "state_key": run_key,
        "state_sha256": digest,
        "asof_date": latest.asof_date,
        "state": latest.state,
        "published_at": published_at,
    }
    s3.put_object(Bucket=bucket, Key=STATE_POINTER, Body=json.dumps(official, sort_keys=True, indent=2).encode(), ContentType="application/json")
    print(json.dumps(official, indent=2))


if __name__ == "__main__":
    main()
