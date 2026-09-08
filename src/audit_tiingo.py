"""Read-only comparison of Tiingo EOD and Yahoo daily OHLCV."""
from __future__ import annotations
import argparse, csv, json, math, os, time, urllib.parse, urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_SYMBOLS = ("SPY", "BHP", "FSI", "NVS", "AMAT", "PODD")

def issues(row):
    try: v = {f: float(row[f]) for f in FIELDS}
    except (KeyError, TypeError, ValueError): return ["missing_or_nonnumeric"]
    if not all(math.isfinite(x) for x in v.values()): return ["nonfinite"]
    out = []
    if any(v[f] <= 0 for f in FIELDS[:-1]): out.append("nonpositive_price")
    if v["volume"] < 0: out.append("negative_volume")
    if v["high"] < max(v["open"], v["close"], v["low"]): out.append("high_below_range")
    if v["low"] > min(v["open"], v["close"], v["high"]): out.append("low_above_range")
    return out

def fetch(symbol, start, end, token):
    q = urllib.parse.urlencode({"startDate": start, "endDate": end, "token": token})
    req = urllib.request.Request(f"https://api.tiingo.com/tiingo/daily/{symbol}/prices?{q}", headers={"User-Agent":"ussy-data-audit/1"})
    with urllib.request.urlopen(req, timeout=30) as r: data = json.loads(r.read())
    if not isinstance(data, list): raise ValueError("Tiingo response was not a price list")
    rows=[]
    for src in data:
        row={"date": str(src.get("date", ""))[:10]}
        for f in FIELDS: row[f] = float(src[f])
        row["adj_close"] = float(src["adjClose"]) if src.get("adjClose") is not None else None
        row["issues"] = issues(row)
        if row["issues"]: raise ValueError(f"Tiingo QC failed date={row['date']}: {row['issues']}")
        rows.append(row)
    rows.sort(key=lambda x:x["date"])
    if not rows or len({x["date"] for x in rows}) != len(rows): raise ValueError("empty or duplicate dates")
    return rows

def yahoo(symbol, start, end):
    import pandas as pd, yfinance as yf
    raw=yf.download(symbol,start=start,end=end,interval="1d",auto_adjust=False,actions=False,repair=False,prepost=False,progress=False,threads=False,timeout=30)
    if isinstance(raw.columns,pd.MultiIndex): raw=raw.xs(symbol,axis=1,level=1) if symbol in raw.columns.get_level_values(1) else raw[symbol]
    out=[]
    for stamp, src in raw.iterrows():
        row={"date":pd.Timestamp(stamp).strftime("%Y-%m-%d")}
        for f,c in (("open","Open"),("high","High"),("low","Low"),("close","Close"),("volume","Volume")): row[f]=float(src[c])
        row["issues"]=issues(row); out.append(row)
    return out

def main():
    p=argparse.ArgumentParser(); p.add_argument("--symbols",default=",".join(DEFAULT_SYMBOLS)); p.add_argument("--lookback-days",type=int,default=20); p.add_argument("--request-delay",type=float,default=2); a=p.parse_args()
    token=os.environ.get("TIINGO_API_KEY");
    if not token: raise ValueError("TIINGO_API_KEY is required")
    symbols=[x.strip().upper() for x in a.symbols.split(",") if x.strip()]; out=Path("diagnostics/tiingo-data"); out.mkdir(parents=True,exist_ok=False)
    start=(date.today()-timedelta(days=a.lookback_days)).isoformat(); end=(date.today()+timedelta(days=1)).isoformat()
    report={"started_at":datetime.now(UTC).isoformat(),"status":"running","mode":"audit_only","r2_reads":0,"r2_writes":0,"symbols":symbols,"parameters":{"start_date":start,"end_date":end,"request_delay_seconds":a.request_delay},"results":[],"errors":[],"limitations":["Audit only; no R2 writes or automatic filling.","Tiingo adjusted fields are recorded for contract review."]}
    path=out/"report.json"; path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    for i,s in enumerate(symbols):
        if i: time.sleep(a.request_delay)
        result={"symbol":s}; report["results"].append(result)
        try:
            t=fetch(s,start,end,token); y=yahoo(s,start,end); result.update({"status":"complete","tiingo_rows":len(t),"yahoo_rows":len(y),"tiingo_latest_date":max(x["date"] for x in t),"yahoo_latest_date":max((x["date"] for x in y),default=None),"dates_only_in_tiingo":sorted(set(x["date"] for x in t)-set(x["date"] for x in y)),"dates_only_in_yahoo":sorted(set(x["date"] for x in y)-set(x["date"] for x in t))})
        except Exception as e: result.update(status="failed",error_type=type(e).__name__,error=str(e)); report["errors"].append({"symbol":s,"error_type":type(e).__name__})
        path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    report.update(status="complete" if not report["errors"] else "complete_with_errors",finished_at=datetime.now(UTC).isoformat()); path.write_text(json.dumps(report,indent=2),encoding="utf-8"); return 1 if report["errors"] else 0
if __name__ == "__main__": raise SystemExit(main())
