"""Read-only Yahoo recovery audit for a missing canonical session."""
import argparse, io, json, os
from datetime import timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import make_s3_client, yahoo_symbol

def extract(frame,symbol,n):
    if frame.empty: return pd.DataFrame()
    if not isinstance(frame.columns,pd.MultiIndex): return frame.copy() if n==1 else pd.DataFrame()
    if symbol in frame.columns.get_level_values(0): return frame[symbol].dropna(how="all")
    if symbol in frame.columns.get_level_values(1): return frame.xs(symbol,axis=1,level=1).dropna(how="all")
    return pd.DataFrame()

def main():
    p=argparse.ArgumentParser(); p.add_argument("--target-date",required=True); p.add_argument("--batch-size",type=int,default=25); a=p.parse_args()
    s3=make_s3_client(); b=os.environ["R2_BUCKET_NAME"]
    m=json.loads(s3.get_object(Bucket=b,Key="production/ready/current.json")["Body"].read())
    f=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=b,Key=m["parquet_key"])["Body"].read()),columns=["security_id","ticker","date"])
    f["date"]=pd.to_datetime(f["date"]).dt.date; t=pd.Timestamp(a.target_date).date()
    before=f[f.date<t].groupby("security_id").date.max(); after=f[f.date>t].groupby("security_id").date.min()
    present=set(f.loc[f.date==t,"security_id"].astype(str)); ids=sorted((set(before.index.astype(str))&set(after.index.astype(str)))-present)
    latest=f.sort_values("date").groupby("security_id").ticker.last().astype(str)
    pairs=[(sid,latest.loc[sid],yahoo_symbol(latest.loc[sid],sid)) for sid in ids if sid in latest.index]
    recovered=[]; missing=[]; errors=[]
    start=(t-timedelta(days=1)).isoformat(); end=(t+timedelta(days=1)).isoformat()
    for i in range(0,len(pairs),a.batch_size):
        batch=pairs[i:i+a.batch_size]; syms=[x[2] for x in batch]
        try: raw=yf.download(syms,start=start,end=end,interval="1d",auto_adjust=False,actions=False,progress=False,group_by="ticker",threads=False,timeout=30)
        except Exception as e: errors.append({"batch":i//a.batch_size+1,"error":str(e)[:300]}); missing.extend(x[1] for x in batch); continue
        for sid,ticker,sym in batch:
            one=extract(raw,sym,len(syms))
            if not one.empty and t in set(pd.to_datetime(one.index).date): recovered.append(ticker)
            else: missing.append(ticker)
        print(f"batch={i//a.batch_size+1} checked={min(i+a.batch_size,len(pairs))}/{len(pairs)} recovered={len(recovered)} missing={len(missing)}",flush=True)
    out={"status":"READ_ONLY","ready_as_of_date":m["as_of_date"],"target_date":a.target_date,"gap_population":len(pairs),"yahoo_recovered":len(recovered),"yahoo_recovered_pct":round(100*len(recovered)/len(pairs),2) if pairs else None,"still_missing_count":len(missing),"still_missing":missing,"batch_errors":errors}
    print("YAHOO_RECOVERY_AUDIT="+json.dumps(out,sort_keys=True))
if __name__=="__main__": main()
