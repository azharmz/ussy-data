"""Bounded R2 publication pilot for corporate-action split facts v1.

Strict scope: two reviewed securities, exactly one Tiingo request each, isolated
pilot namespace, immutable artifacts, read-back validation, pointer written LAST.
Does not write canonical OHLCV/READY/EMA or production corporate-action pointer.
"""
from __future__ import annotations
import hashlib, io, json, os, urllib.parse, urllib.request
from datetime import UTC, datetime
import boto3, pandas as pd

CASES = (
    {"security_id":"US67066G1040","provider_ticker":"NVDA","start":"2024-06-09","end":"2024-06-11","expected_date":"2024-06-10","expected_factor":10.0},
    {"security_id":"US11135F1012","provider_ticker":"AVGO","start":"2024-07-11","end":"2024-07-16","expected_date":"2024-07-15","expected_factor":10.0},
)
REQUEST_BUDGET = 2
ROOT = "corporate_actions/splits/pilots"
FORBIDDEN_PREFIXES = ("history/ohlcv/","production/ready/","production/ema/","corporate_actions/splits/current.json")

def env(n):
    v=os.getenv(n)
    if not v: raise RuntimeError(f"missing {n}")
    return v

def tiingo(case, token):
    q=urllib.parse.urlencode({"startDate":case["start"],"endDate":case["end"],"token":token})
    req=urllib.request.Request(f"https://api.tiingo.com/tiingo/daily/{case['provider_ticker']}/prices?{q}",headers={"User-Agent":"ussy-data-corporate-action-pilot/1"})
    with urllib.request.urlopen(req,timeout=30) as r: data=json.loads(r.read())
    if not isinstance(data,list): raise ValueError("Tiingo response is not a list")
    events=[]
    for row in data:
        factor=float(row.get("splitFactor",1.0))
        if factor != 1.0:
            events.append((str(row["date"])[:10],factor))
    if events != [(case["expected_date"],case["expected_factor"])]:
        raise ValueError(f"unexpected split evidence for {case['provider_ticker']}: {events}")
    return events[0]

def s3client():
    return boto3.client("s3",endpoint_url=env("R2_ENDPOINT"),aws_access_key_id=env("R2_ACCESS_KEY_ID"),aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),region_name="auto")

def put_immutable(s3,bucket,key,body):
    if key.startswith(FORBIDDEN_PREFIXES): raise ValueError(f"forbidden pilot write: {key}")
    try:
        old=s3.get_object(Bucket=bucket,Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        old=None
    except Exception as e:
        if getattr(e,"response",{}).get("Error",{}).get("Code") in ("NoSuchKey","404"): old=None
        else: raise
    if old is not None:
        if old != body: raise ValueError(f"immutable conflict: {key}")
        return "REUSED"
    s3.put_object(Bucket=bucket,Key=key,Body=body)
    return "CREATED"

def main():
    if len(CASES)>REQUEST_BUDGET: raise ValueError("request budget exceeded before network")
    token=env("TIINGO_API_KEY"); bucket=env("R2_BUCKET_NAME"); s3=s3client()
    now=datetime.now(UTC); retrieved=now.isoformat()
    rows=[]; requests=0
    for c in CASES:
        if requests>=REQUEST_BUDGET: raise ValueError("Tiingo request budget exhausted")
        day,factor=tiingo(c,token); requests+=1
        rows.append({"security_id":c["security_id"],"effective_date":day,"split_factor":factor,"provider_ticker":c["provider_ticker"],"source_provider":"tiingo_eod","source_field":"splitFactor","retrieved_at":retrieved,"source_as_of_date":now.date().isoformat(),"schema_version":"corporate-action-split-v1"})
    df=pd.DataFrame(rows).sort_values(["security_id","effective_date"]).reset_index(drop=True)
    bio=io.BytesIO(); df.to_parquet(bio,index=False,compression="zstd"); events=bio.getvalue()
    sha=hashlib.sha256(events).hexdigest(); run_id=f"pilot-{now.strftime('%Y%m%dT%H%M%SZ')}-{os.getenv('GITHUB_RUN_ID','local')}"
    prefix=f"{ROOT}/runs/{run_id}"
    manifest={"schema_version":"corporate-action-split-v1","mode":"BOUNDED_PILOT","created_at":retrieved,"producer_commit":os.getenv("GITHUB_SHA"),"actions_run_id":os.getenv("GITHUB_RUN_ID"),"source_provider":"tiingo_eod","tiingo_request_budget":REQUEST_BUDGET,"tiingo_request_count":requests,"event_rows":len(df),"distinct_securities":int(df.security_id.nunique()),"min_effective_date":df.effective_date.min(),"max_effective_date":df.effective_date.max(),"events_sha256":sha,"events_bytes":len(events),"canonical_namespace_writes":0}
    mbody=json.dumps(manifest,sort_keys=True,indent=2).encode()
    ekey=f"{prefix}/events.parquet"; mkey=f"{prefix}/manifest.json"; pkey=f"{ROOT}/current.json"
    put_immutable(s3,bucket,ekey,events); put_immutable(s3,bucket,mkey,mbody)
    # Read-back before pointer.
    back=s3.get_object(Bucket=bucket,Key=ekey)["Body"].read()
    if hashlib.sha256(back).hexdigest()!=sha: raise ValueError("events read-back hash mismatch")
    backm=json.loads(s3.get_object(Bucket=bucket,Key=mkey)["Body"].read())
    if backm["events_sha256"]!=sha or backm["tiingo_request_count"]!=requests: raise ValueError("manifest read-back mismatch")
    pointer=json.dumps({"schema_version":"corporate-action-split-v1","mode":"BOUNDED_PILOT","run_prefix":prefix,"events_key":ekey,"manifest_key":mkey,"events_sha256":sha,"updated_at":retrieved},sort_keys=True,indent=2).encode()
    if pkey.startswith(FORBIDDEN_PREFIXES): raise ValueError("production pointer forbidden")
    s3.put_object(Bucket=bucket,Key=pkey,Body=pointer)
    # Pointer read-back + target read-back.
    p=json.loads(s3.get_object(Bucket=bucket,Key=pkey)["Body"].read())
    if p["events_sha256"]!=sha or p["run_prefix"]!=prefix: raise ValueError("pointer read-back mismatch")
    print(json.dumps({"status":"PASS","mode":"BOUNDED_PILOT","run_prefix":prefix,"events":len(df),"tiingo_requests":requests,"request_budget":REQUEST_BUDGET,"events_sha256":sha,"pointer":pkey,"canonical_namespace_writes":0},indent=2))

if __name__=="__main__": main()
