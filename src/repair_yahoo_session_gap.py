"""Targeted idempotent repair of one missing Yahoo session."""
import argparse, io, json, os
from datetime import timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import make_s3_client, yahoo_symbol, OHLCV_COLUMNS
HISTORY_PREFIX="history/ohlcv/"

def extract(frame,symbol,n):
    if frame.empty: return pd.DataFrame()
    if not isinstance(frame.columns,pd.MultiIndex): return frame.copy() if n==1 else pd.DataFrame()
    if symbol in frame.columns.get_level_values(0): return frame[symbol].dropna(how="all")
    if symbol in frame.columns.get_level_values(1): return frame.xs(symbol,axis=1,level=1).dropna(how="all")
    return pd.DataFrame()

def raw_target_session(frame,target):
    """Select the Yahoo row by its exchange-session label before normalization."""
    if frame.empty:
        return frame
    dates=pd.Index(pd.to_datetime(frame.index,errors="coerce")).date
    return frame.loc[dates==target.date()].copy()

def normalize_target_session(frame,security_id,ticker,target):
    """Map one verified Yahoo daily row directly to canonical OHLCV."""
    selected=raw_target_session(frame,target)
    if selected.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    if len(selected)!=1:
        raise ValueError(f"Expected one Yahoo row for {target.date()}, got {len(selected)}")
    required=["Open","High","Low","Close","Adj Close","Volume"]
    missing=[name for name in required if name not in selected.columns]
    if missing:
        raise ValueError(f"Yahoo target row missing columns: {missing}")
    src=selected.iloc[0]
    vals={name:pd.to_numeric(src[name],errors="coerce") for name in required}
    if any(pd.isna(vals[name]) for name in required):
        raise ValueError(f"Yahoo target row contains non-numeric OHLCV: { {k: repr(vals[k]) for k in required} }")
    if min(vals["Open"],vals["High"],vals["Low"],vals["Close"],vals["Adj Close"])<=0 or vals["Volume"]<0:
        raise ValueError("Yahoo target row contains invalid OHLCV values")
    if vals["High"] < max(vals["Open"],vals["Close"],vals["Low"]) or vals["Low"] > min(vals["Open"],vals["Close"],vals["High"]):
        raise ValueError("Yahoo target row violates OHLC bounds")
    return pd.DataFrame([{
        "date":target,"security_id":security_id,"ticker":ticker,
        "open":float(vals["Open"]),"high":float(vals["High"]),"low":float(vals["Low"]),
        "close":float(vals["Close"]),"adj_close":float(vals["Adj Close"]),"volume":int(vals["Volume"]),
    }],columns=OHLCV_COLUMNS)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--target-date",required=True); p.add_argument("--batch-size",type=int,default=25); p.add_argument("--max-batches",type=int); a=p.parse_args()
    s3=make_s3_client(); b=os.environ["R2_BUCKET_NAME"]; t=pd.Timestamp(a.target_date).normalize()
    m=json.loads(s3.get_object(Bucket=b,Key="production/ready/current.json")["Body"].read())
    f=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=b,Key=m["parquet_key"])["Body"].read()),columns=["security_id","ticker","date"]); f["date"]=pd.to_datetime(f["date"]).dt.normalize()
    ids=sorted((set(f.loc[f.date<t,"security_id"].astype(str)) & set(f.loc[f.date>t,"security_id"].astype(str)))-set(f.loc[f.date==t,"security_id"].astype(str)))
    latest=f.sort_values("date").groupby("security_id").ticker.last().astype(str); pairs=[(sid,latest.loc[sid],yahoo_symbol(latest.loc[sid],sid)) for sid in ids if sid in latest.index]
    repaired=[]; already=[]; missing=[]; errors=[]; start=(t.date()-timedelta(days=1)).isoformat(); end=(t.date()+timedelta(days=1)).isoformat()
    limit=len(pairs) if a.max_batches is None else min(len(pairs),a.batch_size*a.max_batches)
    for i in range(0,limit,a.batch_size):
        batch=pairs[i:i+a.batch_size]; syms=[x[2] for x in batch]
        try: raw=yf.download(syms,start=start,end=end,interval="1d",auto_adjust=False,actions=False,progress=False,group_by="ticker",threads=False,timeout=30)
        except Exception as e: errors.append({"batch":i//a.batch_size+1,"error":str(e)[:300]}); missing.extend(x[1] for x in batch); continue
        for sid,ticker,sym in batch:
            key=HISTORY_PREFIX+sid+".parquet"
            try:
                hist=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=b,Key=key)["Body"].read())); hist["date"]=pd.to_datetime(hist["date"]).dt.normalize()
                if t in set(hist["date"]): already.append(ticker); continue
                one=extract(raw,sym,len(syms))
                try:
                    row=normalize_target_session(one,sid,ticker,t)
                except ValueError as first_error:
                    solo=yf.download(sym,start=(t.date()-timedelta(days=7)).isoformat(),end=(t.date()+timedelta(days=3)).isoformat(),interval="1d",auto_adjust=False,actions=False,progress=False,threads=False,timeout=30,repair=True)
                    try:
                        row=normalize_target_session(solo,sid,ticker,t)
                    except Exception:
                        raise first_error
                if row.empty: missing.append(ticker); continue
                merged=pd.concat([hist[OHLCV_COLUMNS],row],ignore_index=True).drop_duplicates(["date"],keep="last").sort_values("date").reset_index(drop=True)
                buf=io.BytesIO(); merged.to_parquet(buf,engine="pyarrow",index=False,compression="zstd"); s3.put_object(Bucket=b,Key=key,Body=buf.getvalue(),ContentType="application/vnd.apache.parquet"); repaired.append(ticker)
            except Exception as e: errors.append({"ticker":ticker,"security_id":sid,"error":str(e)[:300]})
        print("batch=%s checked=%s/%s repaired=%s already=%s missing=%s errors=%s"%(i//a.batch_size+1,min(i+a.batch_size,limit),limit,len(repaired),len(already),len(missing),len(errors)),flush=True)
    out={"status":"APPLY","target_date":a.target_date,"gap_population":len(pairs),"repaired":len(repaired),"already_present":len(already),"still_missing_count":len(missing),"still_missing":missing,"errors":errors}; print("YAHOO_GAP_REPAIR="+json.dumps(out,sort_keys=True))
    if missing or errors: raise RuntimeError("Targeted Yahoo gap repair incomplete")
if __name__=="__main__": main()
