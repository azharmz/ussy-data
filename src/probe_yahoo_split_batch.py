"""Read-only Yahoo corporate-action feasibility probe.

Tests yfinance actions=True in batched downloads against already frozen split
cases. No Tiingo calls and no R2 writes.
"""
from __future__ import annotations
import json
import yfinance as yf

CASES = {
 "NVDA": {"date":"2024-06-10","factor":10.0},
 "AVGO": {"date":"2024-07-15","factor":10.0},
 "WMT": {"date":"2024-02-26","factor":3.0},
 "CMG": {"date":"2024-06-26","factor":50.0},
 "GE": {"date":"2021-08-02","factor":0.125},
 "AIG": {"date":"2009-07-01","factor":0.05},
}

def extract(df,ticker):
    if not hasattr(df.columns,"nlevels") or df.columns.nlevels < 2:
        raise ValueError("expected MultiIndex from batched yf.download")
    # group_by=ticker => (Ticker, Price)
    if ticker not in df.columns.get_level_values(0):
        raise ValueError(f"missing ticker group {ticker}")
    sub=df[ticker]
    if "Stock Splits" not in sub.columns:
        raise ValueError(f"Stock Splits missing for {ticker}")
    s=sub["Stock Splits"].dropna()
    return {idx.strftime("%Y-%m-%d"):float(v) for idx,v in s.items() if float(v)!=0.0}

def main():
    symbols=list(CASES)
    # One batched public API call from our code; yfinance may perform internal
    # per-ticker requests. This probe deliberately uses no Tiingo entitlement.
    df=yf.download(symbols,start="2009-06-01",end="2024-08-01",interval="1d",
        auto_adjust=False,actions=True,repair=False,progress=False,
        threads=True,group_by="ticker",multi_level_index=True,timeout=30)
    results={}
    failures=[]
    for ticker,exp in CASES.items():
        events=extract(df,ticker)
        got=events.get(exp["date"])
        ok=got is not None and abs(got-exp["factor"]) < 1e-12
        results[ticker]={"expected":exp,"observed_factor":got,"all_split_events":events,"status":"PASS" if ok else "FAIL"}
        if not ok: failures.append(ticker)
    out={"status":"PASS" if not failures else "FAIL","source":"yfinance_yahoo","actions":True,
         "batch_symbol_count":len(symbols),"tiingo_requests":0,"r2_reads":0,"r2_writes":0,
         "results":results,"failures":failures}
    print(json.dumps(out,indent=2,sort_keys=True))
    if failures: raise SystemExit(2)

if __name__=="__main__": main()
