"""Read-only re-audit of the frozen Sep-22 incident population against raw Yahoo.

Never writes canonical history. Uses the frozen Stooq repair manifest as the
authoritative 1,066 population and rejects incomplete/partial Yahoo rows.
"""
import io, json, os, time
from datetime import timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import make_s3_client, yahoo_symbol

TARGET=pd.Timestamp("2026-09-22")
MANIFEST_KEY="recovery/stooq-2026-09-25/sep22-repair/manifest.json"
REPORT_KEY="recovery/stooq-2026-09-25/sep22-repair/yahoo-raw-reaudit.json"
EXPECTED=1066

def extract(frame,symbol,n):
    if frame.empty: return pd.DataFrame()
    if not isinstance(frame.columns,pd.MultiIndex): return frame.copy() if n==1 else pd.DataFrame()
    if symbol in frame.columns.get_level_values(0): return frame[symbol].dropna(how="all")
    if symbol in frame.columns.get_level_values(1): return frame.xs(symbol,axis=1,level=1).dropna(how="all")
    return pd.DataFrame()

def valid_row(frame):
    if frame.empty: return None
    dates=pd.Index(pd.to_datetime(frame.index,errors="coerce")).normalize()
    row=frame.loc[dates==TARGET]
    if len(row)!=1: return None
    req=["Open","High","Low","Close","Adj Close","Volume"]
    if any(c not in row.columns for c in req): return None
    vals={c:pd.to_numeric(row.iloc[0][c],errors="coerce") for c in req}
    if any(pd.isna(vals[c]) for c in req): return None
    if min(vals["Open"],vals["High"],vals["Low"],vals["Close"],vals["Adj Close"])<=0 or vals["Volume"]<0: return None
    if vals["High"]<max(vals["Open"],vals["Close"],vals["Low"]) or vals["Low"]>min(vals["Open"],vals["Close"],vals["High"]): return None
    return {c:float(vals[c]) for c in req}

def main():
    s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
    manifest=json.loads(s3.get_object(Bucket=bucket,Key=MANIFEST_KEY)["Body"].read())
    targets=manifest["targets"]
    if len(targets)!=EXPECTED: raise RuntimeError(f"Frozen population mismatch: {len(targets)}")

    ready_meta=json.loads(s3.get_object(Bucket=bucket,Key="production/ready/current.json")["Body"].read())
    ready=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket,Key=ready_meta["parquet_key"])["Body"].read()),columns=["security_id","ticker","date"])
    ready["date"]=pd.to_datetime(ready["date"]).dt.normalize()
    latest=ready.sort_values("date").groupby("security_id").ticker.last().astype(str)

    available=[]; missing=[]; partial=[]; errors=[]
    start=(TARGET.date()-timedelta(days=1)).isoformat(); end=(TARGET.date()+timedelta(days=2)).isoformat()
    batch_size=25
    for i in range(0,len(targets),batch_size):
        ids=targets[i:i+batch_size]
        pairs=[(sid,latest.get(sid)) for sid in ids]
        pairs=[(sid,t) for sid,t in pairs if isinstance(t,str) and t]
        syms=[yahoo_symbol(t,sid) for sid,t in pairs]
        try:
            raw=yf.download(syms,start=start,end=end,interval="1d",auto_adjust=False,actions=False,repair=False,progress=False,group_by="ticker",threads=False,timeout=30)
        except Exception as e:
            errors.append({"batch":i//batch_size+1,"error":str(e)[:300],"security_ids":ids}); continue
        for sid,ticker in pairs:
            sym=yahoo_symbol(ticker,sid)
            one=extract(raw,sym,len(syms))
            dates=pd.Index(pd.to_datetime(one.index,errors="coerce")).normalize() if not one.empty else pd.Index([])
            target=one.loc[dates==TARGET] if len(dates) else pd.DataFrame()
            row=valid_row(one)
            if row is not None:
                available.append({"security_id":sid,"ticker":ticker,"yahoo_symbol":sym,"row":row})
            elif len(target):
                partial.append({"security_id":sid,"ticker":ticker,"yahoo_symbol":sym})
            else:
                # Raw individual fallback only. Explicitly repair=False.
                try:
                    solo=yf.Ticker(sym).history(start=(TARGET.date()-timedelta(days=2)).isoformat(),end=(TARGET.date()+timedelta(days=2)).isoformat(),interval="1d",auto_adjust=False,actions=False,repair=False)
                    row=valid_row(solo)
                    if row is not None: available.append({"security_id":sid,"ticker":ticker,"yahoo_symbol":sym,"row":row,"method":"solo_raw"})
                    else: missing.append({"security_id":sid,"ticker":ticker,"yahoo_symbol":sym})
                except Exception as e:
                    errors.append({"security_id":sid,"ticker":ticker,"stage":"solo_raw","error":str(e)[:300]})
        print(f"[yahoo-raw] checked={min(i+batch_size,len(targets))}/{len(targets)} valid={len(available)} missing={len(missing)} partial={len(partial)} errors={len(errors)}",flush=True)
        time.sleep(1)

    out={"target_date":"2026-09-22","population":len(targets),"valid_raw":len(available),"missing_raw":len(missing),"partial_raw":len(partial),"errors":len(errors),"available":available,"missing":missing,"partial":partial,"error_details":errors}
    s3.put_object(Bucket=bucket,Key=REPORT_KEY,Body=json.dumps(out,indent=2,sort_keys=True).encode(),ContentType="application/json")
    print("YAHOO_RAW_REAUDIT="+json.dumps({k:out[k] for k in ["target_date","population","valid_raw","missing_raw","partial_raw","errors"]},sort_keys=True),flush=True)
    print(f"[report] r2://{bucket}/{REPORT_KEY}",flush=True)
    if errors: raise RuntimeError(f"Re-audit incomplete: {len(errors)} Yahoo errors")

if __name__=="__main__": main()
