from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, UTC

import pandas as pd
import yfinance as yf

from bootstrap_ohlcv import HISTORY_PREFIX, OHLCV_COLUMNS, make_s3_client, normalize_history, yahoo_symbol
from update_production import list_keys, read_json, read_parquet, write_parquet
from compliance import is_eligible


def parse_args():
    p = argparse.ArgumentParser(description="Repair one finalized daily OHLCV bar in R2 histories")
    p.add_argument("--date", required=True)
    p.add_argument("--snapshot-date", default="current")
    p.add_argument("--security-id", action="append", default=[], help="Restrict audit/repair to one security_id; repeatable")
    p.add_argument("--apply", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    target = pd.Timestamp(args.date).normalize()
    if target.date() >= datetime.now(UTC).date():
        raise ValueError("repair target must be a past UTC date")
    s3 = make_s3_client(); bucket = os.environ["R2_BUCKET_NAME"]
    snap = args.snapshot_date
    if snap == "current": snap = str(read_json(s3, bucket, "universe/current.json")["snapshot_date"])
    records = read_json(s3, bucket, f"universe/membership/{snap}.json")["records"]
    eligible = {str(r["security_id"]): r for r in records if is_eligible(r)}
    existing_ids = {k.removeprefix(HISTORY_PREFIX).removesuffix(".parquet") for k in list_keys(s3,bucket,HISTORY_PREFIX) if k.endswith(".parquet")}
    ids = sorted(set(eligible) & existing_ids)
    if args.security_id:
        requested = set(args.security_id)
        unknown = sorted(requested - set(ids))
        if unknown:
            raise ValueError(f"requested security_id not eligible/present: {unknown}")
        ids = [sid for sid in ids if sid in requested]
    changed=[]; missing=[]; errors=[]
    start=target.date().isoformat(); end=(target.date()+timedelta(days=1)).isoformat()
    for i,sid in enumerate(ids,1):
        rec=eligible[sid]; ticker=str(rec["ticker"]); symbol=yahoo_symbol(ticker,sid)
        key=f"{HISTORY_PREFIX}{sid}.parquet"
        try:
            raw=yf.download(symbol,start=start,end=end,interval="1d",auto_adjust=False,actions=False,progress=False,threads=False,timeout=30)
            if raw.empty:
                missing.append({"security_id":sid,"ticker":ticker}); continue
            fresh=normalize_history(raw,sid,ticker)
            fresh=fresh[pd.to_datetime(fresh["date"]).dt.normalize()==target]
            if len(fresh)!=1:
                missing.append({"security_id":sid,"ticker":ticker}); continue
            hist=read_parquet(s3,bucket,key); hist["date"]=pd.to_datetime(hist["date"])
            old=hist[hist["date"].dt.normalize()==target]
            if len(old)!=1:
                missing.append({"security_id":sid,"ticker":ticker}); continue
            cols=["open","high","low","close","adj_close","volume"]
            before={c:float(old.iloc[0][c]) for c in cols}; after={c:float(fresh.iloc[0][c]) for c in cols}
            if before==after: continue
            changed.append({"security_id":sid,"ticker":ticker,"before":before,"after":after})
            if args.apply:
                merged=pd.concat([hist[hist["date"].dt.normalize()!=target],fresh],ignore_index=True)
                merged=merged[OHLCV_COLUMNS].drop_duplicates("date",keep="last").sort_values("date").reset_index(drop=True)
                write_parquet(s3,bucket,key,merged)
        except Exception as exc:
            errors.append({"security_id":sid,"ticker":ticker,"error":str(exc)[:300]})
        if i%100==0: print(f"progress {i}/{len(ids)} changed={len(changed)} missing={len(missing)} errors={len(errors)}",flush=True)
    report={"contract":"ussy-one-date-ohlcv-repair-v1","created_at":datetime.now(UTC).isoformat(),"target_date":args.date,"snapshot_date":snap,"apply":args.apply,"requested_security_ids":args.security_id,"eligible_histories":len(ids),"changed":len(changed),"missing":len(missing),"errors":len(errors),"changes":changed,"missing_rows":missing,"error_rows":errors}
    run=os.getenv("GITHUB_RUN_ID","local"); attempt=os.getenv("GITHUB_RUN_ATTEMPT","1")
    key=f"audit/ohlcv-repair/{args.date}/run-{run}-{attempt}.json"
    s3.put_object(Bucket=bucket,Key=key,Body=json.dumps(report,indent=2).encode(),ContentType="application/json")
    print(json.dumps({k:v for k,v in report.items() if k not in {"changes","missing_rows","error_rows"}},indent=2))
    if changed:
        print("Changed rows:", json.dumps(changed, indent=2))
    if missing:
        print("Missing rows:", json.dumps(missing, indent=2))
    print(f"Audit report: {key}")
    if errors:
        print("First processing errors:", json.dumps(errors[:10], indent=2))
        raise RuntimeError(f"repair completed with {len(errors)} download/processing errors")

if __name__=="__main__": main()
