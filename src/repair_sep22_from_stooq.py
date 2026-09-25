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
    daily=get_parquet(s3,bucket,DAILY_KEY)
    safe=set(daily["security_id"].astype(str))
    if len(safe)!=107:
        raise RuntimeError(f"Fail closed: expected 107 original Sep-22 daily rows, got {len(safe)}")

    # Scan only canonical history objects. Pagination is required above 1000.
    keys=[]; token=None
    while True:
        kw={"Bucket":bucket,"Prefix":HISTORY_PREFIX}
        if token: kw["ContinuationToken"]=token
        page=s3.list_objects_v2(**kw)
        keys += [x["Key"] for x in page.get("Contents",[]) if x["Key"].endswith(".parquet")]
        if not page.get("IsTruncated"): break
        token=page["NextContinuationToken"]

    targets=[]; source_missing=[]; source_bad=[]; source_rows={}
    for n,key in enumerate(keys,1):
        sid=key[len(HISTORY_PREFIX):-8]
        hist=get_parquet(s3,bucket,key)
        dates=pd.to_datetime(hist["date"],errors="coerce").dt.normalize()
        if not ((dates<TARGET).any() and (dates>TARGET).any()) or sid in safe:
            continue
        targets.append(sid)
        skey=STOOQ_PREFIX+sid+".parquet"
        if not exists(s3,bucket,skey):
            source_missing.append(sid); continue
        src=get_parquet(s3,bucket,skey)
        sd=pd.to_datetime(src["date"],errors="coerce").dt.normalize()
        row=src.loc[sd==TARGET]
        if len(row)!=1:
            source_bad.append({"security_id":sid,"reason":f"target_rows={len(row)}"}); continue
        r=row.iloc[0]
        bad=issues(r)
        if bad:
            source_bad.append({"security_id":sid,"reason":";".join(bad)}); continue
        source_rows[sid]=row[OHLCV_COLUMNS].copy()

    summary={"created_at":datetime.now(UTC).isoformat(),"mode":"APPLY" if a.apply else "AUDIT",
      "target_date":"2026-09-22","daily_safe_count":len(safe),"bracketed_target_count":len(targets),
      "expected_target_count":EXPECTED,"stooq_ready":len(source_rows),"stooq_missing":len(source_missing),
      "stooq_bad":len(source_bad),"canonical_history_objects":len(keys)}
    print("SEP22_STOOQ_AUDIT="+json.dumps(summary,sort_keys=True),flush=True)
    if len(targets)!=EXPECTED:
        raise RuntimeError(f"Fail closed: incident population expected {EXPECTED}, got {len(targets)}")
    if source_missing or source_bad or len(source_rows)!=EXPECTED:
        raise RuntimeError(f"Fail closed: Stooq coverage/QC incomplete missing={len(source_missing)} bad={len(source_bad)} ready={len(source_rows)}")
    if not a.apply:
        print("AUDIT PASS: zero canonical writes",flush=True); return

    changed=0
    for n,sid in enumerate(sorted(targets),1):
        key=HISTORY_PREFIX+sid+".parquet"; backup=BACKUP_PREFIX+sid+".parquet"
        raw=s3.get_object(Bucket=bucket,Key=key)["Body"].read()
        if not exists(s3,bucket,backup):
            s3.put_object(Bucket=bucket,Key=backup,Body=raw,ContentType="application/vnd.apache.parquet")
        hist=pd.read_parquet(io.BytesIO(raw)); hist["date"]=pd.to_datetime(hist["date"]).dt.normalize()
        merged=pd.concat([hist.loc[hist["date"]!=TARGET,OHLCV_COLUMNS],source_rows[sid]],ignore_index=True)
        merged=merged.drop_duplicates("date",keep="last").sort_values("date").reset_index(drop=True)
        put_parquet(s3,bucket,key,merged); changed+=1
        if n%100==0 or n==len(targets): print(f"[apply] {n}/{len(targets)}",flush=True)

    summary["changed"]=changed
    report_key=REPORT_PREFIX+"apply.json"
    s3.put_object(Bucket=bucket,Key=report_key,Body=json.dumps(summary,indent=2).encode(),ContentType="application/json")
    print("SEP22_STOOQ_APPLY="+json.dumps(summary,sort_keys=True),flush=True)
    if changed!=EXPECTED: raise RuntimeError(f"Apply incomplete: {changed}/{EXPECTED}")

if __name__=="__main__": main()
