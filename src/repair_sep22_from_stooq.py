"""One-off Sep-22 Stooq incident repair. Audit by default; APPLY is explicit.

Population contract: canonical histories that bracket 2026-09-22, excluding the
security_ids present in the immutable production/daily/2026-09-22.parquet output.
The incident contract requires exactly 1,066 targets. Source rows come only from
the verified Stooq recovery staging prefix.
"""
from __future__ import annotations
import argparse, hashlib, io, json, os
from datetime import UTC, datetime
import pandas as pd
from botocore.exceptions import ClientError
from bootstrap_ohlcv import make_s3_client, OHLCV_COLUMNS
from ohlcv_qc import issues

TARGET=pd.Timestamp("2026-09-22")
DAILY_KEY="production/daily/2026-09-22.parquet"
HISTORY_PREFIX="history/ohlcv/"
STOOQ_PREFIX="recovery/stooq-2026-09-25/history/ohlcv/"
BACKUP_PREFIX="recovery/stooq-2026-09-25/pre-sep22-repair/history/ohlcv/"
REPORT_PREFIX="recovery/stooq-2026-09-25/sep22-repair/"
EXPECTED=1066
EXPECTED_READY=1061
EXPECTED_UNRESOLVED={"KYG096751022","KYG837611170","US5784731003","VGG646271137","US68840D1028"}

def get_parquet(s3,bucket,key):
    return pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket,Key=key)["Body"].read()))

def put_parquet(s3,bucket,key,frame):
    buf=io.BytesIO(); frame.to_parquet(buf,index=False,compression="zstd")
    data=buf.getvalue(); s3.put_object(Bucket=bucket,Key=key,Body=data,ContentType="application/vnd.apache.parquet")
    return hashlib.sha256(data).hexdigest(),len(data)

def exists(s3,bucket,key):
    try: s3.head_object(Bucket=bucket,Key=key); return True
    except ClientError as e:
        if e.response.get("Error",{}).get("Code") in {"404","NoSuchKey","NotFound"}: return False
        raise

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
    # Reproduce the original 1,066 incident population exactly: it was derived
    # from the READY snapshot current at the time, not from all canonical histories.
    ready_meta=json.loads(s3.get_object(Bucket=bucket,Key="production/ready/current.json")["Body"].read())
    ready=get_parquet(s3,bucket,ready_meta["parquet_key"])
    ready["date"]=pd.to_datetime(ready["date"],errors="coerce").dt.normalize()
    ids=sorted((set(ready.loc[ready.date<TARGET,"security_id"].astype(str)) &
                set(ready.loc[ready.date>TARGET,"security_id"].astype(str))) -
               set(ready.loc[ready.date==TARGET,"security_id"].astype(str)))
    targets=ids
    print(f"[population] ready_key={ready_meta['parquet_key']} targets={len(targets)}",flush=True)

    source_missing=[]; source_bad=[]; source_rows={}
    for n,sid in enumerate(targets,1):
        skey=STOOQ_PREFIX+sid+".parquet"
        if not exists(s3,bucket,skey):
            source_missing.append(sid)
        else:
            src=get_parquet(s3,bucket,skey)
            sd=pd.to_datetime(src["date"],errors="coerce").dt.normalize()
            row=src.loc[sd==TARGET]
            if len(row)!=1:
                source_bad.append({"security_id":sid,"reason":f"target_rows={len(row)}"})
            else:
                bad=issues(row.iloc[0])
                if bad: source_bad.append({"security_id":sid,"reason":";".join(bad)})
                else: source_rows[sid]=row[OHLCV_COLUMNS].copy()
        if n%50==0 or n==len(targets):
            print(f"[stooq] checked={n}/{len(targets)} ready={len(source_rows)} missing={len(source_missing)} bad={len(source_bad)}",flush=True)

    summary={"created_at":datetime.now(UTC).isoformat(),"mode":"APPLY" if a.apply else "AUDIT",
      "target_date":"2026-09-22","population_source":ready_meta["parquet_key"],"incident_target_count":len(targets),
      "expected_target_count":EXPECTED,"stooq_ready":len(source_rows),"stooq_missing":len(source_missing),
      "stooq_bad":len(source_bad)}
    print("SEP22_STOOQ_AUDIT="+json.dumps(summary,sort_keys=True),flush=True)
    manifest={"created_at":summary["created_at"],"target_date":"2026-09-22",
      "population_source":ready_meta["parquet_key"],"targets":targets,
      "stooq_ready":sorted(source_rows),"stooq_missing":source_missing,"stooq_bad":source_bad}
    manifest_key=REPORT_PREFIX+"manifest.json"
    s3.put_object(Bucket=bucket,Key=manifest_key,Body=json.dumps(manifest,indent=2,sort_keys=True).encode(),ContentType="application/json")
    print("SEP22_STOOQ_UNRESOLVED="+json.dumps({"missing":source_missing,"bad":source_bad},sort_keys=True),flush=True)
    print(f"[manifest] r2://{bucket}/{manifest_key} targets={len(targets)} ready={len(source_rows)} unresolved={len(source_missing)+len(source_bad)}",flush=True)
    if len(targets)!=EXPECTED:
        raise RuntimeError(f"Fail closed: incident population expected {EXPECTED}, got {len(targets)}")
    unresolved=set(source_missing) | {x["security_id"] for x in source_bad}
    if len(source_rows)!=EXPECTED_READY or unresolved!=EXPECTED_UNRESOLVED:
        raise RuntimeError(f"Fail closed: expected ready={EXPECTED_READY} and frozen unresolved IDs; got ready={len(source_rows)} unresolved={sorted(unresolved)}")
    if not a.apply:
        print(f"AUDIT PASS: ready={len(source_rows)} frozen_unresolved={len(unresolved)} zero canonical writes",flush=True); return

    changed=0
    apply_targets=sorted(source_rows)
    for n,sid in enumerate(apply_targets,1):
        key=HISTORY_PREFIX+sid+".parquet"; backup=BACKUP_PREFIX+sid+".parquet"
        raw=s3.get_object(Bucket=bucket,Key=key)["Body"].read()
        if not exists(s3,bucket,backup):
            s3.put_object(Bucket=bucket,Key=backup,Body=raw,ContentType="application/vnd.apache.parquet")
        hist=pd.read_parquet(io.BytesIO(raw)); hist["date"]=pd.to_datetime(hist["date"]).dt.normalize()
        merged=pd.concat([hist.loc[hist["date"]!=TARGET,OHLCV_COLUMNS],source_rows[sid]],ignore_index=True)
        merged=merged.drop_duplicates("date",keep="last").sort_values("date").reset_index(drop=True)
        put_parquet(s3,bucket,key,merged); changed+=1
        if n%100==0 or n==len(apply_targets): print(f"[apply] {n}/{len(apply_targets)}",flush=True)

    summary["changed"]=changed
    summary["skipped_unresolved"]=sorted(unresolved)
    report_key=REPORT_PREFIX+"apply.json"
    s3.put_object(Bucket=bucket,Key=report_key,Body=json.dumps(summary,indent=2).encode(),ContentType="application/json")
    print("SEP22_STOOQ_APPLY="+json.dumps(summary,sort_keys=True),flush=True)
    if changed!=EXPECTED_READY: raise RuntimeError(f"Apply incomplete: {changed}/{EXPECTED_READY}")

if __name__=="__main__": main()
