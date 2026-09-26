"""One-shot bounded repair for verified same-security ticker transitions."""
from __future__ import annotations
import io, json, os
from datetime import date, timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import HISTORY_PREFIX, OHLCV_COLUMNS, make_s3_client, normalize_history, yahoo_symbol
from security_lifecycle import acquisition_ticker, records

TARGET=date(2026,9,22)
CASES={
 "US37892C1062":("GGRP","BTLN"),
 "US45769N1054":("ISSC","IA"),
 "VGG9888Q1110":("YYGH","YFOR"),
}

def read_parquet(s3,bucket,key):
 return pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket,Key=key)["Body"].read()),engine="pyarrow")
def write_parquet(s3,bucket,key,frame):
 b=io.BytesIO(); frame.to_parquet(b,engine="pyarrow",index=False,compression="zstd")
 s3.put_object(Bucket=bucket,Key=key,Body=b.getvalue(),ContentType="application/vnd.apache.parquet")

def main():
 s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]; results=[]
 for sid,(old,new) in CASES.items():
  rec=records().get(sid)
  if not rec or rec.get("lifecycle_status")!="VERIFIED_TICKER_CHANGE_SAME_SECURITY":
   raise RuntimeError(f"{sid}: lifecycle contract mismatch")
  if rec.get("successor_security_id")!=sid or acquisition_ticker(sid,old,TARGET)!=new:
   raise RuntimeError(f"{sid}: successor identity/ticker mismatch")
  key=f"{HISTORY_PREFIX}{sid}.parquet"; hist=read_parquet(s3,bucket,key)[OHLCV_COLUMNS].copy()
  hist["date"]=pd.to_datetime(hist["date"]).dt.tz_localize(None)
  if (hist["date"].dt.date==TARGET).any():
   results.append({"security_id":sid,"provider_ticker":new,"status":"already_present"}); continue
  start=date.fromisoformat(rec["effective_date"])-timedelta(days=7)
  symbol=yahoo_symbol(new,sid)
  raw=yf.download(symbol,start=start.isoformat(),end=(TARGET+timedelta(days=1)).isoformat(),interval="1d",auto_adjust=False,actions=False,progress=False,threads=False,timeout=30)
  if raw.empty: raise RuntimeError(f"{sid}/{new}: Yahoo returned no rows")
  downloaded=normalize_history(raw,sid,old)[OHLCV_COLUMNS].copy()
  downloaded["date"]=pd.to_datetime(downloaded["date"]).dt.tz_localize(None)
  target_rows=downloaded[downloaded["date"].dt.date==TARGET]
  if len(target_rows)!=1: raise RuntimeError(f"{sid}/{new}: expected exactly one valid Sep22 row, got {len(target_rows)}")
  existing=set(hist["date"]); add=downloaded[~downloaded["date"].isin(existing)].copy()
  merged=pd.concat([hist,add],ignore_index=True)[OHLCV_COLUMNS].drop_duplicates("date",keep="last").sort_values("date").reset_index(drop=True)
  if not (merged["date"].dt.date==TARGET).any(): raise RuntimeError(f"{sid}: target absent after merge")
  write_parquet(s3,bucket,key,merged)
  results.append({"security_id":sid,"provider_ticker":new,"status":"repaired","added_rows":len(add)})
 print("TICKER_TRANSITION_REPAIR="+json.dumps(results,sort_keys=True))
if __name__=="__main__": main()
