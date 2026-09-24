from __future__ import annotations
from compliance import is_eligible

import argparse
import io
import json
import logging
import os
import random
import time
from datetime import UTC, datetime, timedelta
from security_lifecycle import acquisition_allowed, records as lifecycle_records
from itertools import batched
from typing import Any

import pandas as pd
import yfinance as yf

from bootstrap_ohlcv import HISTORY_PREFIX, OHLCV_COLUMNS, make_s3_client, normalize_history, yahoo_symbol

LOG = logging.getLogger("update_production")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update daily and rolling production OHLCV")
    parser.add_argument("--snapshot-date", default="current"); parser.add_argument("--rolling-bars", type=int, default=300); parser.add_argument("--minimum-ready-bars", type=int, default=250); parser.add_argument("--batch-size", type=int, default=10); parser.add_argument("--request-delay", type=float, default=2.0); parser.add_argument("--max-retries", type=int, default=3); return parser.parse_args()

def list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys=[]
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix): keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys

def read_json(s3,bucket,key): return json.loads(s3.get_object(Bucket=bucket,Key=key)["Body"].read())
def read_parquet(s3,bucket,key): return pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket,Key=key)["Body"].read()),engine="pyarrow")
def write_parquet(s3,bucket,key,frame):
    buffer=io.BytesIO(); frame.to_parquet(buffer,engine="pyarrow",index=False,compression="zstd"); payload=buffer.getvalue(); s3.put_object(Bucket=bucket,Key=key,Body=payload,ContentType="application/vnd.apache.parquet"); return len(payload)

def download_since(symbol,start_date,max_retries):
    last_error=None; start=(start_date.date()-timedelta(days=7)).isoformat(); end=(datetime.now(UTC).date()+timedelta(days=1)).isoformat()
    for attempt in range(1,max_retries+1):
        try: return yf.download(symbol,start=start,end=end,interval="1d",auto_adjust=False,actions=False,progress=False,threads=False,timeout=30)
        except Exception as exc:
            last_error=exc
            if attempt<max_retries:
                delay=60*attempt+random.uniform(0,10) if exc.__class__.__name__=="YFRateLimitError" else 2**(attempt-1)+random.random(); LOG.warning("Download failed for %s (attempt %s/%s); retrying in %.1fs",symbol,attempt,max_retries,delay); time.sleep(delay)
    raise RuntimeError(f"download failed after {max_retries} attempts: {last_error}")

def download_batch(symbols,start_date):
    start=(start_date.date()-timedelta(days=7)).isoformat(); end=(datetime.now(UTC).date()+timedelta(days=1)).isoformat(); return yf.download(symbols,start=start,end=end,interval="1d",auto_adjust=False,actions=False,progress=False,group_by="ticker",threads=False,timeout=30)

def extract_symbol(frame,symbol,symbol_count):
    if frame.empty:return pd.DataFrame()
    if not isinstance(frame.columns,pd.MultiIndex):return frame.copy() if symbol_count==1 else pd.DataFrame()
    if symbol in frame.columns.get_level_values(0):return frame[symbol].dropna(how="all")
    if symbol in frame.columns.get_level_values(1):return frame.xs(symbol,axis=1,level=1).dropna(how="all")
    return pd.DataFrame()

def normalize_with_individual_fallback(batch_raw,symbol,last_date,max_retries,security_id,ticker):
    if batch_raw.empty:
        LOG.warning("No batch rows for %s; retrying individually",symbol); raw=download_since(symbol,last_date,max_retries); return pd.DataFrame() if raw.empty else normalize_history(raw,security_id,ticker)
    try:return normalize_history(batch_raw,security_id,ticker)
    except Exception as batch_exc:
        LOG.warning("Batch rows for %s failed normalization/QC; retrying individually: %s",symbol,batch_exc); raw=download_since(symbol,last_date,max_retries)
        if raw.empty:raise RuntimeError(f"batch normalization/QC failed ({batch_exc}); individual retry returned no rows") from batch_exc
        try:return normalize_history(raw,security_id,ticker)
        except Exception as individual_exc:raise RuntimeError("batch and individual normalization/QC failed; "+f"batch={batch_exc}; individual={individual_exc}") from individual_exc

def merge_downloaded_history(historical, downloaded, last_date):
    """Persist recovered internal sessions as well as newer Yahoo bars."""
    if downloaded.empty:
        empty=historical.iloc[0:0].copy()
        return historical, empty, empty
    existing_dates=set(pd.to_datetime(historical["date"]))
    downloaded=downloaded[OHLCV_COLUMNS].copy()
    additions=downloaded[downloaded["date"]>last_date].copy()
    backfills=downloaded[(downloaded["date"]<=last_date) & (~downloaded["date"].isin(existing_dates))].copy()
    if additions.empty and backfills.empty:
        return historical, additions, backfills
    merged=pd.concat([historical,downloaded],ignore_index=True)
    merged=merged[OHLCV_COLUMNS].drop_duplicates(subset=["date"],keep="last").sort_values("date").reset_index(drop=True)
    return merged, additions, backfills

def normalize_existing(frame,security_id,ticker):
    missing=set(OHLCV_COLUMNS)-set(frame.columns)
    if missing:raise ValueError(f"Historical Parquet lacks columns: {sorted(missing)}")
    frame=frame[OHLCV_COLUMNS].copy(); frame["date"]=pd.to_datetime(frame["date"],errors="coerce"); frame["security_id"]=security_id; frame["ticker"]=ticker; return frame.dropna(subset=["date"]).drop_duplicates(subset=["date"],keep="last").sort_values("date").reset_index(drop=True)

def main():
    args=parse_args()
    if not 1<=args.minimum_ready_bars<=args.rolling_bars:raise ValueError("minimum-ready-bars must be between 1 and rolling-bars")
    if not 1<=args.batch_size<=25:raise ValueError("batch-size must be between 1 and 25")
    if not 0<=args.request_delay<=60:raise ValueError("request-delay must be between 0 and 60 seconds")
    s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
    if args.snapshot_date=="current":args.snapshot_date=str(read_json(s3,bucket,"universe/current.json")["snapshot_date"])
    membership=read_json(s3,bucket,f"universe/membership/{args.snapshot_date}.json")["records"]; confirmed={str(row["security_id"]):row for row in membership if is_eligible(row)}
    parquet_ids={key.removeprefix(HISTORY_PREFIX).removesuffix(".parquet") for key in list_keys(s3,bucket,HISTORY_PREFIX) if key.endswith(".parquet")}; operational_ids=sorted(set(confirmed)&parquet_ids); missing_ids=set(confirmed)-parquet_ids; reviewed_no_retry_ids=sorted(sid for sid in missing_ids if sid in lifecycle_records()); unavailable_ids=sorted(missing_ids-set(reviewed_no_retry_ids)); acquisition_date=datetime.now(UTC).date()
    rolling_frames=[]; new_rows=[]; details=[]; failures=[]; updated_histories=0; histories={}; candidates=[]
    for security_id in operational_ids:
        record=confirmed[security_id]; ticker=str(record["ticker"]); symbol=yahoo_symbol(ticker,security_id); key=f"{HISTORY_PREFIX}{security_id}.parquet"
        try:
            historical=normalize_existing(read_parquet(s3,bucket,key),security_id,ticker)
            if historical.empty:raise ValueError("Historical Parquet is empty")
            histories[security_id]=historical
            if acquisition_allowed(security_id,acquisition_date): candidates.append((security_id,ticker,symbol,historical["date"].iloc[-1]))
            else:
                rolling=historical.tail(args.rolling_bars).copy(); rolling_frames.append(rolling); details.append({"security_id":security_id,"ticker":ticker,"available_bars":len(historical),"rolling_bars":len(rolling),"last_date":historical["date"].iloc[-1].date().isoformat(),"update_status":"lifecycle_excluded"}); LOG.info("Lifecycle-excluded daily acquisition for %s (%s)",ticker,security_id)
        except Exception as exc:failures.append({"security_id":security_id,"ticker":ticker,"error":str(exc)[:500]}); LOG.error("Failed to load history for %s (%s): %s",ticker,security_id,exc)
    candidate_batches=list(batched(candidates,args.batch_size))
    for batch_index,batch in enumerate(candidate_batches):
        if batch_index and args.request_delay:
            delay=args.request_delay+random.uniform(0,min(1.0,args.request_delay/10)); LOG.info("Rate-limit delay between batches: %.2fs",delay); time.sleep(delay)
        symbols=[item[2] for item in batch]; common_start=min(item[3] for item in batch); LOG.info("Downloading batch %s/%s (%s tickers)",batch_index+1,len(candidate_batches),len(symbols))
        try:batch_frame=download_batch(symbols,common_start)
        except Exception as exc:LOG.warning("Batch download failed; retrying its tickers individually: %s",exc); batch_frame=pd.DataFrame()
        for security_id,ticker,symbol,last_date in batch:
            historical=histories[security_id]; key=f"{HISTORY_PREFIX}{security_id}.parquet"
            try:
                raw=extract_symbol(batch_frame,symbol,len(symbols)); normalized=normalize_with_individual_fallback(raw,symbol,last_date,args.max_retries,security_id,ticker); downloaded=historical.iloc[0:0].copy() if normalized.empty else normalized; merged,additions,backfills=merge_downloaded_history(historical,downloaded,last_date)
                if not additions.empty or not backfills.empty:
                    write_parquet(s3,bucket,key,merged); historical=merged; histories[security_id]=historical; new_rows.append(pd.concat([backfills,additions],ignore_index=True)[OHLCV_COLUMNS]); updated_histories+=1; LOG.info("Updated %s through %s (+%s new, +%s recovered internal bars)",ticker,historical["date"].iloc[-1].date(),len(additions),len(backfills))
                rolling=historical.tail(args.rolling_bars).copy(); rolling_frames.append(rolling); details.append({"security_id":security_id,"ticker":ticker,"available_bars":len(historical),"rolling_bars":len(rolling),"last_date":historical["date"].iloc[-1].date().isoformat()})
            except Exception as exc:
                failures.append({"security_id":security_id,"ticker":ticker,"error":str(exc)[:500]}); LOG.error("Failed production update for %s (%s): %s",ticker,security_id,exc); print(f"::warning title=Production OHLCV update failed::{ticker} ({security_id}): {str(exc)[:300]}"); rolling=historical.tail(args.rolling_bars).copy(); rolling_frames.append(rolling); details.append({"security_id":security_id,"ticker":ticker,"available_bars":len(historical),"rolling_bars":len(rolling),"last_date":historical["date"].iloc[-1].date().isoformat(),"update_status":"stale_after_failure"})
    failure_rate=len(failures)/len(operational_ids) if operational_ids else 1
    if failure_rate>0.10:raise RuntimeError(f"Failure rate {failure_rate:.1%} exceeds 10% safety threshold; production outputs not published")
    daily_outputs=[]
    if new_rows:
        additions=pd.concat(new_rows,ignore_index=True); additions["date"]=pd.to_datetime(additions["date"]); existing_daily_keys=set(list_keys(s3,bucket,"production/daily/"))
        for market_date,daily in additions.groupby(additions["date"].dt.date):
            daily=daily[OHLCV_COLUMNS].drop_duplicates(subset=["security_id"],keep="last").sort_values("security_id").reset_index(drop=True); daily_key=f"production/daily/{market_date.isoformat()}.parquet"
            if daily_key in existing_daily_keys:
                existing=read_parquet(s3,bucket,daily_key); daily=pd.concat([existing,daily],ignore_index=True); daily=daily[OHLCV_COLUMNS].drop_duplicates(subset=["security_id"],keep="last").sort_values("security_id").reset_index(drop=True)
            size=write_parquet(s3,bucket,daily_key,daily); daily_outputs.append({"date":market_date.isoformat(),"rows":len(daily),"key":daily_key,"bytes":size})
    else:print("::warning title=No new market bars::Yahoo returned no dates newer than the stored histories")
    rolling=pd.concat(rolling_frames,ignore_index=True); rolling=rolling[OHLCV_COLUMNS].drop_duplicates(subset=["security_id","date"],keep="last").sort_values(["security_id","date"]).reset_index(drop=True); ready_ids=sorted(row["security_id"] for row in details if row["rolling_bars"]>=args.minimum_ready_bars); insufficient_ids=sorted(row["security_id"] for row in details if row["rolling_bars"]<args.minimum_ready_bars); created_at=datetime.now(UTC).isoformat()
    readiness={"created_at":created_at,"snapshot_date":args.snapshot_date,"rolling_bars_target":args.rolling_bars,"minimum_ready_bars":args.minimum_ready_bars,"confirmed_compliant":len(confirmed),"included_in_rolling":len(details),"ready":len(ready_ids),"insufficient_history":len(insufficient_ids),"reviewed_no_retry":len(reviewed_no_retry_ids),"data_unavailable":len(unavailable_ids),"update_failures":len(failures),"rolling_rows":len(rolling),"ready_security_ids":ready_ids,"insufficient_history_security_ids":insufficient_ids,"reviewed_no_retry_security_ids":reviewed_no_retry_ids,"data_unavailable_security_ids":unavailable_ids,"securities":sorted(details,key=lambda row:row["security_id"])}
    rolling_size=write_parquet(s3,bucket,"production/rolling/latest.parquet",rolling); s3.put_object(Bucket=bucket,Key="production/rolling/readiness.json",Body=json.dumps(readiness,indent=2).encode(),ContentType="application/json"); run_id=os.getenv("GITHUB_RUN_ID","local"); attempt=os.getenv("GITHUB_RUN_ATTEMPT","1"); report={"created_at":created_at,"snapshot_date":args.snapshot_date,"batch_size":args.batch_size,"batch_delay_seconds":args.request_delay,"operational_securities":len(operational_ids),"updated_histories":updated_histories,"daily_outputs":daily_outputs,"rolling_rows":len(rolling),"rolling_bytes":rolling_size,"ready":len(ready_ids),"insufficient_history":len(insufficient_ids),"reviewed_no_retry":len(reviewed_no_retry_ids),"data_unavailable":len(unavailable_ids),"failures":failures}; report_key=f"production/manifests/run-{run_id}-{attempt}.json"; s3.put_object(Bucket=bucket,Key=report_key,Body=json.dumps(report,indent=2).encode(),ContentType="application/json"); print(json.dumps({k:v for k,v in report.items() if k!="failures"},indent=2)); print(f"Production manifest: {report_key}")

if __name__=="__main__": logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s"); main()
