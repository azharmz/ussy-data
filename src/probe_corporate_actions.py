"""Read-only feasibility probe for authoritative stock-split facts.

No R2 writes. Tests Tiingo EOD splitFactor + Corporate Actions split endpoint
against known split cases and Yahoo actions as an independent cross-check.
"""
from __future__ import annotations
import json, os, urllib.error, urllib.parse, urllib.request
from datetime import UTC, datetime, timedelta

import pandas as pd
import yfinance as yf

CASES = [
    {"ticker":"NVDA","ex_date":"2024-06-10","expected_factor":10.0},
    {"ticker":"AVGO","ex_date":"2024-07-15","expected_factor":10.0},
    {"ticker":"WMT","ex_date":"2024-02-26","expected_factor":3.0},
    {"ticker":"CMG","ex_date":"2024-06-26","expected_factor":50.0},
]


def get_json(url: str, token: str):
    req=urllib.request.Request(url,headers={"User-Agent":"ussy-data-corp-action-probe/1"})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body=exc.read().decode("utf-8","replace")[:1000].replace(token,"[REDACTED]")
        return exc.code, {"error":body}


def tiingo_eod(ticker, day, token):
    d=pd.Timestamp(day); start=(d-timedelta(days=3)).date().isoformat(); end=(d+timedelta(days=3)).date().isoformat()
    q=urllib.parse.urlencode({"startDate":start,"endDate":end,"token":token})
    return get_json(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?{q}",token)


def tiingo_ca(ticker, day, token):
    d=pd.Timestamp(day); start=(d-timedelta(days=3)).date().isoformat(); end=(d+timedelta(days=3)).date().isoformat()
    q=urllib.parse.urlencode({"startExDate":start,"endExDate":end,"token":token})
    return get_json(f"https://api.tiingo.com/tiingo/corporate-actions/{ticker}/splits?{q}",token)


def yahoo_split(ticker, day):
    d=pd.Timestamp(day); start=(d-timedelta(days=3)).date().isoformat(); end=(d+timedelta(days=4)).date().isoformat()
    raw=yf.download(ticker,start=start,end=end,auto_adjust=False,actions=True,repair=False,progress=False,threads=False,timeout=30)
    if isinstance(raw.columns,pd.MultiIndex):
        try: raw=raw.xs(ticker,axis=1,level=1)
        except Exception: pass
    out=[]
    if "Stock Splits" in raw.columns:
        for stamp,val in raw["Stock Splits"].items():
            if pd.notna(val) and float(val)!=0:
                out.append({"date":pd.Timestamp(stamp).date().isoformat(),"factor":float(val)})
    return out


def main():
    token=os.environ.get("TIINGO_API_KEY")
    if not token: raise RuntimeError("TIINGO_API_KEY is required")
    report={"status":"RUNNING","mode":"READ_ONLY","started_at":datetime.now(UTC).isoformat(),"r2_writes":0,"cases":[]}
    for case in CASES:
        ticker, day, expected=case["ticker"],case["ex_date"],case["expected_factor"]
        eod_status,eod=tiingo_eod(ticker,day,token)
        ca_status,ca=tiingo_ca(ticker,day,token)
        eod_events=[]
        if isinstance(eod,list):
            eod_events=[{"date":str(x.get("date",""))[:10],"splitFactor":x.get("splitFactor"),"adjVolume":x.get("adjVolume"),"volume":x.get("volume"),"adjOpen":x.get("adjOpen"),"open":x.get("open")} for x in eod if x.get("splitFactor") not in (None,1,1.0)]
        ca_events=[]
        if isinstance(ca,list):
            ca_events=[{k:x.get(k) for k in ("permaTicker","ticker","exDate","splitFrom","splitTo","splitFactor","splitStatus")} for x in ca]
        y=yahoo_split(ticker,day)
        eod_match=any(str(x.get("date",""))[:10]==day and abs(float(x.get("splitFactor"))-expected)<1e-9 for x in eod_events if x.get("splitFactor") is not None)
        ca_match=any(str(x.get("exDate",""))[:10]==day and abs(float(x.get("splitFactor"))-expected)<1e-9 for x in ca_events if x.get("splitFactor") is not None)
        y_match=any(x["date"]==day and abs(x["factor"]-expected)<1e-9 for x in y)
        report["cases"].append({**case,"tiingo_eod_http":eod_status,"tiingo_eod_events":eod_events,"tiingo_eod_match":eod_match,"tiingo_ca_http":ca_status,"tiingo_ca_events":ca_events,"tiingo_ca_match":ca_match,"yahoo_events":y,"yahoo_match":y_match})
    report["summary"]={
        "case_count":len(CASES),
        "eod_matches":sum(x["tiingo_eod_match"] for x in report["cases"]),
        "ca_endpoint_accessible":sum(x["tiingo_ca_http"]==200 for x in report["cases"]),
        "ca_matches":sum(x["tiingo_ca_match"] for x in report["cases"]),
        "yahoo_matches":sum(x["yahoo_match"] for x in report["cases"]),
        "permaTicker_present":sum(any(e.get("permaTicker") for e in x["tiingo_ca_events"]) for x in report["cases"]),
    }
    report["status"]="COMPLETE"
    report["finished_at"]=datetime.now(UTC).isoformat()
    print(json.dumps(report,indent=2,default=str))

if __name__=="__main__": main()
