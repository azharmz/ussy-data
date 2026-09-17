"""Read-only reverse-split evidence probe. Exactly two Tiingo requests; zero R2 writes."""
from __future__ import annotations
import json, os, urllib.parse, urllib.request

CASES = [
    {"ticker":"GE", "start":"2021-07-29", "end":"2021-08-03", "expected_date":"2021-08-02", "expected_factor":0.125, "external_identity_note":"reverse split changed GE ISIN to US3696043013"},
    {"ticker":"AIG", "start":"2009-06-29", "end":"2009-07-02", "expected_date_candidates":["2009-06-30","2009-07-01"], "expected_factor":0.05},
]
MAX_TIINGO_REQUESTS = 2

def fetch(case, token):
    q=urllib.parse.urlencode({"startDate":case["start"],"endDate":case["end"],"token":token})
    req=urllib.request.Request(f"https://api.tiingo.com/tiingo/daily/{case['ticker']}/prices?{q}",headers={"User-Agent":"ussy-data-reverse-split-probe/1"})
    with urllib.request.urlopen(req,timeout=30) as r:
        data=json.loads(r.read())
    if not isinstance(data,list): raise RuntimeError("Tiingo response not list")
    return data

def main():
    token=os.environ.get("TIINGO_API_KEY")
    if not token: raise RuntimeError("TIINGO_API_KEY required")
    if len(CASES)>MAX_TIINGO_REQUESTS: raise RuntimeError("request budget exceeded")
    report={"mode":"READ_ONLY","tiingo_request_budget":MAX_TIINGO_REQUESTS,"tiingo_requests_used":0,"r2_writes":0,"cases":[]}
    for case in CASES:
        rows=fetch(case,token); report["tiingo_requests_used"]+=1
        events=[{"date":str(x.get("date",""))[:10],"splitFactor":x.get("splitFactor")} for x in rows if x.get("splitFactor") not in (None,1,1.0)]
        expected_dates=set(case.get("expected_date_candidates",[case.get("expected_date")]))
        matched=any(e["date"] in expected_dates and e["splitFactor"] is not None and abs(float(e["splitFactor"])-case["expected_factor"])<1e-12 for e in events)
        report["cases"].append({"ticker":case["ticker"],"expected_factor":case["expected_factor"],"expected_dates":sorted(expected_dates),"events":events,"match":matched,"external_identity_note":case.get("external_identity_note")})
    report["status"]="PASS" if all(x["match"] for x in report["cases"]) and report["tiingo_requests_used"]<=MAX_TIINGO_REQUESTS else "FAIL"
    print(json.dumps(report,indent=2))
    if report["status"]!="PASS": raise SystemExit(1)
if __name__=="__main__": main()
