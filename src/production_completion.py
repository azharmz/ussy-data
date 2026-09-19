"""Durable full-production completion evidence and cheap dedup checks."""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import UTC, datetime
from bootstrap_ohlcv import make_s3_client
from us_market_finalization import finalized_through

POINTER_KEY="production/completions/current.json"
PREFIX="production/completions/runs/"
SCHEMA_VERSION=1
DOWNSTREAM_VERSION="freshness-plus-web-status-v1"

def read_json(s3,bucket,key):
    return json.loads(s3.get_object(Bucket=bucket,Key=key)["Body"].read())

def optional_json(s3,bucket,key):
    try:return read_json(s3,bucket,key)
    except Exception as exc:
        code=str(getattr(exc,"response",{}).get("Error",{}).get("Code",""))
        if code in {"NoSuchKey","404","NotFound"} or isinstance(exc,KeyError):return None
        raise

def intended_finalized_identity():
    """Latest likely US trading date at/before the conservative finalization cutoff.

    Weekend dates are mapped to Friday. Exchange holidays remain safely non-complete:
    READY will not equal the holiday date, so production may run and reuse durable
    checkpoints rather than incorrectly suppressing recovery.
    """
    cutoff=finalized_through()
    while cutoff.weekday()>=5:
        from datetime import timedelta
        cutoff-=timedelta(days=1)
    return cutoff.isoformat()

def current_identity(s3,bucket):
    ready=read_json(s3,bucket,"production/ready/current.json")
    ema=read_json(s3,bucket,"production/indicators/ema/current.json")
    return {
      "finalized_through": intended_finalized_identity(),
      "ready_as_of_date": ready.get("as_of_date"),
      "ready_parquet_key": ready.get("parquet_key"),
      "ready_sha256": ready.get("sha256"),
      "ema_parquet_key": ema.get("parquet_key"),
      "ema_sha256": ema.get("sha256"),
      "ema_source_ready_parquet_key": ema.get("source_ready_parquet_key"),
      "ema_source_ready_sha256": ema.get("source_ready_sha256"),
    }

def lineage_valid(identity):
    return bool(identity["ready_as_of_date"] and identity["ready_parquet_key"] and identity["ready_sha256"]
      and identity["ema_parquet_key"] and identity["ema_sha256"]
      and identity["ema_source_ready_parquet_key"]==identity["ready_parquet_key"]
      and identity["ema_source_ready_sha256"]==identity["ready_sha256"])

def marker_matches(marker,identity):
    if not marker or marker.get("schema_version")!=SCHEMA_VERSION or marker.get("status")!="FULLY_COMPLETE":return False
    if marker.get("downstream_version")!=DOWNSTREAM_VERSION:return False
    return all(marker.get(k)==v for k,v in identity.items())

def is_complete(s3,bucket):
    try: identity=current_identity(s3,bucket)
    except Exception:return False,None
    if identity["ready_as_of_date"]!=identity["finalized_through"] or not lineage_valid(identity):return False,identity
    return marker_matches(optional_json(s3,bucket,POINTER_KEY),identity),identity

def write_completion(s3,bucket):
    identity=current_identity(s3,bucket)
    if identity["ready_as_of_date"]!=identity["finalized_through"]:
        raise RuntimeError("Cannot complete production: READY is not current finalized identity")
    if not lineage_valid(identity):raise RuntimeError("Cannot complete production: EMA lineage does not match READY")
    payload={"schema_version":SCHEMA_VERSION,"status":"FULLY_COMPLETE",**identity,
      "downstream_version":DOWNSTREAM_VERSION,"producer_commit_sha":os.getenv("GITHUB_SHA","local"),
      "github_run_id":os.getenv("GITHUB_RUN_ID","local"),"github_run_attempt":os.getenv("GITHUB_RUN_ATTEMPT","1"),
      "completed_at":datetime.now(UTC).isoformat()}
    raw=json.dumps(payload,sort_keys=True,indent=2).encode(); digest=hashlib.sha256(raw).hexdigest()
    key=f"{PREFIX}{identity['ready_as_of_date']}-{identity['ready_sha256'][:16]}.json"
    existing=optional_json(s3,bucket,key)
    if existing is not None and existing!=payload:raise RuntimeError(f"Completion key conflict: {key}")
    if existing is None:s3.put_object(Bucket=bucket,Key=key,Body=raw,ContentType="application/json")
    pointer={**payload,"completion_key":key,"completion_sha256":digest}
    s3.put_object(Bucket=bucket,Key=POINTER_KEY,Body=json.dumps(pointer,indent=2).encode(),ContentType="application/json")
    return pointer

def main():
    p=argparse.ArgumentParser();p.add_argument("--check",action="store_true");p.add_argument("--write",action="store_true");a=p.parse_args()
    if a.check==a.write:raise ValueError("Choose exactly one of --check or --write")
    s3,bucket=make_s3_client(),os.environ["R2_BUCKET_NAME"]
    if a.check:
        complete,identity=is_complete(s3,bucket);print(json.dumps({"complete":complete,"identity":identity},indent=2))
        if os.getenv("GITHUB_OUTPUT"):open(os.environ["GITHUB_OUTPUT"],"a").write(f"complete={'true' if complete else 'false'}\n")
    else:print(json.dumps(write_completion(s3,bucket),indent=2))
if __name__=="__main__":main()
