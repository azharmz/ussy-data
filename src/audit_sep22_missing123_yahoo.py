"""Read-only raw Yahoo audit of canonical Sep-22 missing population."""
import io,json,os,time
from datetime import timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import make_s3_client,yahoo_symbol
from compliance import is_eligible
from security_lifecycle import acquisition_allowed
T=pd.Timestamp("2026-09-22")

def extract(f,s,n):
    if f.empty:return pd.DataFrame()
    if not isinstance(f.columns,pd.MultiIndex):return f.copy() if n==1 else pd.DataFrame()
    if s in f.columns.get_level_values(0):return f[s].dropna(how="all")
    if s in f.columns.get_level_values(1):return f.xs(s,axis=1,level=1).dropna(how="all")
    return pd.DataFrame()
def row(f):
    if f.empty:return None
    d=pd.Index(pd.to_datetime(f.index,errors="coerce")).normalize(); x=f.loc[d==T]
    req=["Open","High","Low","Close","Adj Close","Volume"]
    if len(x)!=1 or any(c not in x.columns for c in req):return None
    v={c:pd.to_numeric(x.iloc[0][c],errors="coerce") for c in req}
    if any(pd.isna(v[c]) for c in req):return None
    return {c:float(v[c]) for c in req}

s3=make_s3_client(); b=os.environ["R2_BUCKET_NAME"]
u=json.loads(s3.get_object(Bucket=b,Key="universe/current.json")["Body"].read()); snap=str(u["snapshot_date"])
m=json.loads(s3.get_object(Bucket=b,Key=f"universe/membership/{snap}.json")["Body"].read())["records"]
elig={str(x["security_id"]):str(x.get("ticker") or "") for x in m if is_eligible(x)}
keys=[]
for p in s3.get_paginator("list_objects_v2").paginate(Bucket=b,Prefix="history/ohlcv/"):keys += [x["Key"] for x in p.get("Contents",[]) if x["Key"].endswith(".parquet")]
oper={k.split("/")[-1][:-8] for k in keys}&set(elig)
allowed=sorted(s for s in oper if acquisition_allowed(s,T.date()))
missing=[]
for sid in allowed:
    f=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=b,Key=f"history/ohlcv/{sid}.parquet")["Body"].read()),columns=["date"])
    if not (pd.to_datetime(f.date).dt.normalize()==T).any():missing.append(sid)
if len(missing)!=123:raise RuntimeError(f"Expected 123 canonical-missing, got {len(missing)}")
valid=[]; absent=[]; partial=[]; errors=[]
start=(T.date()-timedelta(days=1)).isoformat(); end=(T.date()+timedelta(days=2)).isoformat()
for i in range(0,len(missing),25):
    ids=missing[i:i+25]; pairs=[(sid,elig[sid]) for sid in ids]; syms=[yahoo_symbol(t,sid) for sid,t in pairs]
    try: raw=yf.download(syms,start=start,end=end,interval="1d",auto_adjust=False,actions=False,repair=False,progress=False,group_by="ticker",threads=False,timeout=30)
    except Exception as e: errors.append({"batch":i//25+1,"error":str(e)});continue
    for sid,t in pairs:
        sym=yahoo_symbol(t,sid); one=extract(raw,sym,len(syms)); r=row(one)
        if r:valid.append({"security_id":sid,"ticker":t,"yahoo_symbol":sym,"row":r});continue
        d=pd.Index(pd.to_datetime(one.index,errors="coerce")).normalize() if not one.empty else pd.Index([])
        if len(d) and (d==T).any():partial.append({"security_id":sid,"ticker":t,"yahoo_symbol":sym});continue
        try:
            solo=yf.Ticker(sym).history(start=(T.date()-timedelta(days=2)).isoformat(),end=end,interval="1d",auto_adjust=False,actions=False,repair=False)
            r=row(solo)
            (valid if r else absent).append({"security_id":sid,"ticker":t,"yahoo_symbol":sym,**({"row":r,"method":"solo_raw"} if r else {})})
        except Exception as e:errors.append({"security_id":sid,"ticker":t,"error":str(e)})
    print(f"YF123_PROGRESS checked={min(i+25,len(missing))}/123 valid={len(valid)} missing={len(absent)} partial={len(partial)} errors={len(errors)}",flush=True)
out={"population":123,"valid_raw":len(valid),"missing_raw":len(absent),"partial_raw":len(partial),"errors":len(errors),"available":valid,"missing":absent,"partial":partial,"error_details":errors}
print("YF123_RESULT="+json.dumps(out,sort_keys=True),flush=True)
