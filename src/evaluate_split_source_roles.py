"""Read-only cross-provider split source-role evaluation.

Candidate architecture under evaluation only:
Yahoo/yfinance = primary batched acquisition.
Tiingo EOD splitFactor = independent validation.

Frozen corporate-action contract is NOT changed by this probe.
No R2 access or writes.
"""
from __future__ import annotations
import json, math, os, urllib.parse, urllib.request
import yfinance as yf

CASES = {
    "NVDA": {"date":"2024-06-10","factor":10.0,"start":"2024-06-09","end":"2024-06-11"},
    "AVGO": {"date":"2024-07-15","factor":10.0,"start":"2024-07-11","end":"2024-07-16"},
    "WMT": {"date":"2024-02-26","factor":3.0,"start":"2024-02-23","end":"2024-02-28"},
    "CMG": {"date":"2024-06-26","factor":50.0,"start":"2024-06-24","end":"2024-06-28"},
    "GE": {"date":"2021-08-02","factor":0.125,"start":"2021-07-30","end":"2021-08-04"},
    "AIG": {"date":"2009-07-01","factor":0.05,"start":"2009-06-29","end":"2009-07-03"},
}
TIINGO_REQUEST_BUDGET = len(CASES)

def yahoo_events(df, ticker):
    if not hasattr(df.columns, "nlevels") or df.columns.nlevels < 2:
        raise ValueError("expected MultiIndex Yahoo batch result")
    if ticker not in df.columns.get_level_values(0):
        raise ValueError(f"missing Yahoo ticker group {ticker}")
    sub = df[ticker]
    if "Stock Splits" not in sub.columns:
        raise ValueError(f"Yahoo Stock Splits missing for {ticker}")
    return {idx.strftime("%Y-%m-%d"): float(v) for idx, v in sub["Stock Splits"].dropna().items() if float(v) != 0.0}

def tiingo_events(ticker, case, token):
    q=urllib.parse.urlencode({"startDate":case["start"],"endDate":case["end"],"token":token})
    req=urllib.request.Request(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?{q}",
        headers={"User-Agent":"ussy-data-split-source-role-eval/1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data=json.loads(r.read())
    if not isinstance(data,list):
        raise ValueError(f"Tiingo response is not a list for {ticker}")
    return {str(row["date"])[:10]:float(row["splitFactor"]) for row in data
            if row.get("splitFactor") is not None and float(row["splitFactor"]) != 1.0}

def main():
    token=os.getenv("TIINGO_API_KEY")
    if not token:
        raise RuntimeError("missing TIINGO_API_KEY")
    symbols=list(CASES)
    yahoo=yf.download(
        symbols,start="2009-06-01",end="2024-08-01",interval="1d",
        auto_adjust=False,actions=True,repair=False,progress=False,
        threads=False,group_by="ticker",multi_level_index=True,timeout=30,
    )
    results={}; failures=[]; tiingo_requests=0
    for ticker,case in CASES.items():
        ye=yahoo_events(yahoo,ticker)
        if tiingo_requests >= TIINGO_REQUEST_BUDGET:
            raise RuntimeError("Tiingo request budget exhausted")
        te=tiingo_events(ticker,case,token); tiingo_requests += 1
        y=ye.get(case["date"]); t=te.get(case["date"]); exp=case["factor"]
        ok=(y is not None and t is not None and
            math.isclose(y,exp,rel_tol=0,abs_tol=1e-12) and
            math.isclose(t,exp,rel_tol=0,abs_tol=1e-12) and
            math.isclose(y,t,rel_tol=0,abs_tol=1e-12))
        results[ticker]={
            "expected":{"date":case["date"],"factor":exp},
            "yahoo_factor":y,"tiingo_factor":t,
            "cross_provider_match": bool(y is not None and t is not None and math.isclose(y,t,rel_tol=0,abs_tol=1e-12)),
            "status":"PASS" if ok else "FAIL",
        }
        if not ok: failures.append(ticker)
    out={
        "status":"PASS" if not failures else "FAIL",
        "architecture_under_evaluation":{
            "primary_acquisition_candidate":"yfinance_yahoo_batched_actions",
            "independent_validation_candidate":"tiingo_eod_splitFactor",
            "frozen_contract_changed":False,
        },
        "yahoo_batch_symbol_count":len(symbols),
        "tiingo_request_budget":TIINGO_REQUEST_BUDGET,
        "tiingo_requests":tiingo_requests,
        "r2_reads":0,"r2_writes":0,
        "results":results,"failures":failures,
    }
    print(json.dumps(out,indent=2,sort_keys=True))
    if failures: raise SystemExit(2)

if __name__=="__main__":
    main()
