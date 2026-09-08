"""Read-only comparison of Tiingo EOD and Yahoo daily OHLCV."""
from __future__ import annotations
import argparse, csv, json, math, os, time, urllib.error, urllib.parse, urllib.request
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
    try:
        with urllib.request.urlopen(req, timeout=30) as r: data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        # The body normally identifies entitlement, quota, or symbol problems.
        # Do not include request URLs: they contain the API token.
        detail = exc.read().decode("utf-8", "replace")[:500].replace(token, "[REDACTED]")
        raise ValueError(f"Tiingo HTTP {exc.code}: {detail}") from exc
    if not isinstance(data, list): raise ValueError(f"Tiingo response was not a price list: {str(data)[:500]}")
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

def compare_rows(tiingo_rows, yahoo_rows):
    tiingo = {row["date"]: row for row in tiingo_rows}
    yahoo_observed = {row["date"]: row for row in yahoo_rows}
    yahoo_valid = {day: row for day, row in yahoo_observed.items() if not row["issues"]}
    common = sorted(set(tiingo) & set(yahoo_valid))
    overlap = []
    for day in common:
        t, y = tiingo[day], yahoo_valid[day]
        raw_delta = {field: t[field] - y[field] for field in FIELDS}
        adjusted_delta = None
        if (t["adj_close"] is not None and y["adj_close"] is not None
                and math.isfinite(t["adj_close"]) and math.isfinite(y["adj_close"])):
            adjusted_delta = t["adj_close"] - y["adj_close"]
        overlap.append({
            "date": day,
            "absolute_delta": raw_delta,
            "relative_delta": {field: None if y[field] == 0 else raw_delta[field] / y[field] for field in FIELDS},
            "adj_close_absolute_delta": adjusted_delta,
        })
    return {
        "tiingo_latest_date": max(tiingo) if tiingo else None,
        "yahoo_latest_valid_date": max(yahoo_valid) if yahoo_valid else None,
        "yahoo_observed_dates_with_issues": {day: row["issues"] for day, row in yahoo_observed.items() if row["issues"]},
        "dates_only_in_tiingo": sorted(set(tiingo) - set(yahoo_valid)),
        "dates_only_in_yahoo": sorted(set(yahoo_valid) - set(tiingo)),
        "common_date_count": len(common),
        "overlap": overlap,
    }

def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("date", *FIELDS, "adj_close", "issues"), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "issues": "|".join(row.get("issues", []))})

def yahoo(symbol, start, end):
    import pandas as pd, yfinance as yf
    raw=yf.download(symbol,start=start,end=end,interval="1d",auto_adjust=False,actions=False,repair=False,prepost=False,progress=False,threads=False,timeout=30)
    if isinstance(raw.columns,pd.MultiIndex): raw=raw.xs(symbol,axis=1,level=1) if symbol in raw.columns.get_level_values(1) else raw[symbol]
    out=[]
    for stamp, src in raw.iterrows():
        row={"date":pd.Timestamp(stamp).strftime("%Y-%m-%d")}
        for f,c in (("open","Open"),("high","High"),("low","Low"),("close","Close"),("volume","Volume")): row[f]=float(src[c])
        row["adj_close"] = float(src["Adj Close"])
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
            t=fetch(s,start,end,token); y=yahoo(s,start,end)
            write_csv(out / f"{s}-tiingo.csv", t)
            write_csv(out / f"{s}-yahoo.csv", y)
            result.update({"status":"complete", "tiingo_rows":len(t), "yahoo_rows":len(y), "comparison":compare_rows(t, y)})
        except Exception as e: result.update(status="failed",error_type=type(e).__name__,error=str(e)); report["errors"].append({"symbol":s,"error_type":type(e).__name__})
        path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    report.update(status="complete" if not report["errors"] else "complete_with_errors",finished_at=datetime.now(UTC).isoformat()); path.write_text(json.dumps(report,indent=2),encoding="utf-8"); return 1 if report["errors"] else 0
if __name__ == "__main__": raise SystemExit(main())
