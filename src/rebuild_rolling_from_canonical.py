"""Rebuild rolling/readiness from canonical histories only; no provider fetch."""
from __future__ import annotations
import io, json, os
from datetime import UTC, datetime
import pandas as pd
from bootstrap_ohlcv import HISTORY_PREFIX, OHLCV_COLUMNS, make_s3_client
from compliance import is_eligible
from update_production import list_keys, read_json, read_parquet, write_parquet, normalize_existing
from security_lifecycle import records as lifecycle_records

ROLLING_BARS=300
MIN_READY=250

def main():
    s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
    snapshot=str(read_json(s3,bucket,"universe/current.json")["snapshot_date"])
    membership=read_json(s3,bucket,f"universe/membership/{snapshot}.json")["records"]
    confirmed={str(r["security_id"]):r for r in membership if is_eligible(r)}
    parquet_ids={k.removeprefix(HISTORY_PREFIX).removesuffix(".parquet") for k in list_keys(s3,bucket,HISTORY_PREFIX) if k.endswith(".parquet")}
    operational=sorted(set(confirmed)&parquet_ids)
    missing=set(confirmed)-parquet_ids
    reviewed=sorted(s for s in missing if s in lifecycle_records()); unavailable=sorted(missing-set(reviewed))
    frames=[]; details=[]; failures=[]
    for i,sid in enumerate(operational,1):
        ticker=str(confirmed[sid]["ticker"])
        try:
            h=normalize_existing(read_parquet(s3,bucket,f"{HISTORY_PREFIX}{sid}.parquet"),sid,ticker)
            if h.empty: raise ValueError("Historical Parquet is empty")
            r=h.tail(ROLLING_BARS).copy(); frames.append(r)
            details.append({"security_id":sid,"ticker":ticker,"available_bars":len(h),"rolling_bars":len(r),"last_date":h["date"].iloc[-1].date().isoformat(),"update_status":"canonical_rebuild_no_fetch"})
        except Exception as e: failures.append({"security_id":sid,"ticker":ticker,"error":str(e)[:500]})
        if i==1 or i%100==0 or i==len(operational): print(f"ROLLING_REBUILD progress={i}/{len(operational)} failures={len(failures)}",flush=True)
    if failures: raise RuntimeError(f"Fail closed: canonical history failures={len(failures)} sample={failures[:10]}")
    rolling=pd.concat(frames,ignore_index=True)[OHLCV_COLUMNS].drop_duplicates(["security_id","date"],keep="last").sort_values(["security_id","date"]).reset_index(drop=True)
    from ohlcv_qc import validate_frame
    validate_frame(rolling)
    ready_ids=sorted(x["security_id"] for x in details if x["rolling_bars"]>=MIN_READY)
    insufficient=sorted(x["security_id"] for x in details if x["rolling_bars"]<MIN_READY)
    # Critical Sep-22 propagation guard: canonical repair must actually be present in rolling.
    t=pd.Timestamp("2026-09-22")
    sep22=int((pd.to_datetime(rolling["date"]).dt.normalize()==t).sum())
    if sep22 < 1284: raise RuntimeError(f"Sep22 propagation incomplete: rolling has {sep22}, expected at least 1284")
    created=datetime.now(UTC).isoformat()
    readiness={"created_at":created,"snapshot_date":snapshot,"rolling_bars_target":ROLLING_BARS,"minimum_ready_bars":MIN_READY,"confirmed_compliant":len(confirmed),"included_in_rolling":len(details),"ready":len(ready_ids),"insufficient_history":len(insufficient),"reviewed_no_retry":len(reviewed),"data_unavailable":len(unavailable),"update_failures":0,"rolling_rows":len(rolling),"ready_security_ids":ready_ids,"insufficient_history_security_ids":insufficient,"reviewed_no_retry_security_ids":reviewed,"data_unavailable_security_ids":unavailable,"securities":sorted(details,key=lambda x:x["security_id"])}
    size=write_parquet(s3,bucket,"production/rolling/latest.parquet",rolling)
    s3.put_object(Bucket=bucket,Key="production/rolling/readiness.json",Body=json.dumps(readiness,indent=2).encode(),ContentType="application/json")
    print("ROLLING_REBUILD_RESULT="+json.dumps({"snapshot_date":snapshot,"operational":len(operational),"rolling_rows":len(rolling),"ready":len(ready_ids),"insufficient":len(insufficient),"sep22_rows":sep22,"bytes":size},sort_keys=True),flush=True)

if __name__=="__main__": main()
