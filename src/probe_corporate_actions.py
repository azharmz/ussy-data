"""Read-only feasibility probe for authoritative stock-split facts. No R2 writes."""
from __future__ import annotations
import json, os, urllib.error, urllib.parse, urllib.request
from datetime import UTC, datetime, timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import make_s3_client

CASES=[
 {"ticker":"NVDA","ex_date":"2024-06-10","expected_factor":10.0},
 {"ticker":"AVGO","ex_date":"2024-07-15","expected_factor":10.0},
 {"ticker":"WMT","ex_date":"2024-02-26","expected_factor":3.0},
 {"ticker":"CMG","ex_date":"2024-06-26","expected_factor":50.0},
]

def get_json(url,token):
 req=urllib.request.Request(url,headers={"User-Agent":"ussy-data-corp-action-probe/2"})
 try:
  with urllib.request.urlopen(req,timeout=30) as r:return r.status,json.loads(r.read())
 except urllib.error.HTTPError as exc:
  return exc.code,{"error":exc.read().decode("utf-8","replace")[:1000].replace(token,"[REDACTED]")}

def tiingo_eod(ticker,day,token):
 d=pd.Timestamp(day); q=urllib.parse.urlencode({"startDate":(d-timedelta(days=3)).date().isoformat(),"endDate":(d+timedelta(days=3)).date().isoformat(),"token":token})
 return get_json(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?{q}",token)

def tiingo_ca(ticker,day,token):
 d=pd.Timestamp(day); q=urllib.parse.urlencode({"startExDate":(d-timedelta(days=3)).date().isoformat(),"endExDate":(d+timedelta(days=3)).date().isoformat(),"token":token})
 return get_json(f"https://api.tiingo.com/tiingo/corporate-actions/{ticker}/splits?{q}",token)

def yahoo_split(ticker,day):
 d=pd.Timestamp(day); raw=yf.download(ticker,start=(d-timedelta(days=3)).date().isoformat(),end=(d+timedelta(days=4)).date().isoformat(),auto_adjust=False,actions=True,repair=False,progress=False,threads=False,timeout=30)
 if isinstance(raw.columns,pd.MultiIndex):
  try:raw=raw.xs(ticker,axis=1,level=1)
  except Exception:pass
 out=[]
 if "Stock Splits" in raw.columns:
  for stamp,val in raw["Stock Splits"].items():
   if pd.notna(val) and float(val)!=0:out.append({"date":pd.Timestamp(stamp).date().isoformat(),"factor":float(val)})
 return out

def canonical_aliases():
 s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
 def read(k):return json.loads(s3.get_object(Bucket=bucket,Key=k)["Body"].read())
 current=read("universe/current.json"); snap=current["snapshot_date"]; membership=read(f"universe/membership/{snap}.json")
 by_ticker={}
 for row in membership.get("records",[]):
  ticker=str(row.get("ticker") or "").upper()
  if ticker:by_ticker.setdefault(ticker,[]).append(str(row.get("security_id")))
 return snap,by_ticker

def main():
 token=os.environ.get("TIINGO_API_KEY")
 if not token:raise RuntimeError("TIINGO_API_KEY is required")
 snap,aliases=canonical_aliases()
 report={"status":"RUNNING","mode":"READ_ONLY","started_at":datetime.now(UTC).isoformat(),"r2_writes":0,"universe_snapshot":snap,"cases":[]}
 for case in CASES:
  ticker,day,expected=case["ticker"],case["ex_date"],case["expected_factor"]
  es,eod=tiingo_eod(ticker,day,token); cs,ca=tiingo_ca(ticker,day,token)
  ee=[]
  if isinstance(eod,list):ee=[{"date":str(x.get("date",""))[:10],"splitFactor":x.get("splitFactor"),"adjVolume":x.get("adjVolume"),"volume":x.get("volume"),"adjOpen":x.get("adjOpen"),"open":x.get("open")} for x in eod if x.get("splitFactor") not in (None,1,1.0)]
  ce=[]
  if isinstance(ca,list):ce=[{k:x.get(k) for k in ("permaTicker","ticker","exDate","splitFrom","splitTo","splitFactor","splitStatus")} for x in ca]
  y=yahoo_split(ticker,day); ids=aliases.get(ticker,[])
  report["cases"].append({**case,"canonical_security_ids":ids,"canonical_identity_unique":len(ids)==1,"tiingo_eod_http":es,"tiingo_eod_events":ee,"tiingo_eod_match":any(str(x.get("date",""))[:10]==day and abs(float(x.get("splitFactor"))-expected)<1e-9 for x in ee if x.get("splitFactor") is not None),"tiingo_ca_http":cs,"tiingo_ca_events":ce,"tiingo_ca_match":any(str(x.get("exDate",""))[:10]==day and abs(float(x.get("splitFactor"))-expected)<1e-9 for x in ce if x.get("splitFactor") is not None),"yahoo_events":y,"yahoo_match":any(x["date"]==day and abs(x["factor"]-expected)<1e-9 for x in y)})
 report["summary"]={"case_count":len(CASES),"eod_matches":sum(x["tiingo_eod_match"] for x in report["cases"]),"ca_endpoint_accessible":sum(x["tiingo_ca_http"]==200 for x in report["cases"]),"ca_matches":sum(x["tiingo_ca_match"] for x in report["cases"]),"yahoo_matches":sum(x["yahoo_match"] for x in report["cases"]),"canonical_identity_unique":sum(x["canonical_identity_unique"] for x in report["cases"]),"permaTicker_present":sum(any(e.get("permaTicker") for e in x["tiingo_ca_events"]) for x in report["cases"])}
 report.update(status="COMPLETE",finished_at=datetime.now(UTC).isoformat());print(json.dumps(report,indent=2,default=str))
if __name__=="__main__":main()
