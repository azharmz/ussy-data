"""Audit recent-session continuity in the current canonical READY snapshot.

Read-only: downloads production/ready/current.json and its parquet from R2,
then reports securities that reached the candidate terminal date while missing
an observed intermediate session after --previous-as-of.
"""
from __future__ import annotations
import argparse, hashlib, io, json, os
from datetime import timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import HISTORY_PREFIX, OHLCV_COLUMNS, make_s3_client, yahoo_symbol
from export_ready import recent_session_gap_summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--previous-as-of", required=True)
    p.add_argument("--snapshot-date", help="Audit immutable production/ready/runs/YYYY-MM-DD.parquet instead of current pointer")
    args=p.parse_args()
    s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
    if args.snapshot_date:
        ready_key=f"production/ready/runs/{args.snapshot_date}.parquet"
        raw=s3.get_object(Bucket=bucket,Key=ready_key)["Body"].read()
        ready_as_of_date=args.snapshot_date
        ready_sha256=hashlib.sha256(raw).hexdigest()
    else:
        manifest=json.loads(s3.get_object(Bucket=bucket,Key="production/ready/current.json")["Body"].read())
        raw=s3.get_object(Bucket=bucket,Key=manifest["parquet_key"])["Body"].read()
        ready_as_of_date=manifest["as_of_date"]
        ready_sha256=manifest["sha256"]
    summary=recent_session_gap_summary(frame,args.previous_as_of,ready_as_of_date)
    ticker_by_sid=(frame.sort_values("date").groupby("security_id")["ticker"].last().astype(str).to_dict())
    gaps=[]
    for sid, missing_sessions in sorted(summary["gaps"].items()):
        key=f"{HISTORY_PREFIX}{sid}.parquet"
        evidence={"history_key":key}
        try:
            history_raw=s3.get_object(Bucket=bucket,Key=key)["Body"].read()
            history=pd.read_parquet(io.BytesIO(history_raw),columns=OHLCV_COLUMNS)
            history_dates=set(pd.to_datetime(history["date"],errors="raise").dt.date.astype(str))
            history_date_rows=pd.to_datetime(history["date"],errors="raise").dt.date.astype(str)
            stored_rows={}
            for d in missing_sessions:
                subset=history.loc[history_date_rows==d,OHLCV_COLUMNS]
                stored_rows[d]=[
                    {col:(int(row[col]) if col=="volume" else (str(row[col]) if col in ("date","security_id","ticker") else float(row[col])))
                     for col in OHLCV_COLUMNS}
                    for _,row in subset.iterrows()
                ]
            evidence.update({
                "history_rows":int(len(history)),
                "history_latest_date":max(history_dates) if history_dates else None,
                "missing_sessions_present_in_history":{d:d in history_dates for d in missing_sessions},
                "target_rows_now_in_history":stored_rows,
            })
        except Exception as exc:
            evidence["history_read_error"]=str(exc)[:300]
        ticker=ticker_by_sid.get(sid)
        yahoo_symbol_name=yahoo_symbol(ticker,sid) if ticker else None
        yahoo_evidence={"symbol":yahoo_symbol_name,"sessions":{}}
        try:
            targets=[pd.Timestamp(d).date() for d in missing_sessions]
            fields=["Open","High","Low","Close","Adj Close","Volume"]
            def row_evidence(frame,target):
                dates=pd.Index(pd.to_datetime(frame.index,errors="coerce")).date if not frame.empty else pd.Index([])
                rows=frame.loc[dates==target] if not frame.empty else pd.DataFrame()
                item={"row_count":int(len(rows))}
                if len(rows)==1 and all(field in rows.columns for field in fields):
                    row=rows.iloc[0]
                    vals={field:(None if pd.isna(row[field]) else float(row[field])) for field in fields}
                    missing=[field for field,value in vals.items() if value is None]
                    valid=(not missing and min(vals[x] for x in ["Open","High","Low","Close","Adj Close"])>0
                           and vals["Volume"]>=0
                           and vals["High"]>=max(vals["Open"],vals["Close"],vals["Low"])
                           and vals["Low"]<=min(vals["Open"],vals["Close"],vals["High"]))
                    item.update(status="VALID" if valid else "INVALID",
                                reason=("missing_values:"+",".join(missing)) if missing else (None if valid else "OHLCV bounds/positivity check failed"),
                                values=vals)
                else:
                    item["status"]="NO_SINGLE_COMPLETE_ROW"
                return item
            combined=yf.Ticker(yahoo_symbol_name).history(
                start=(min(targets)-timedelta(days=7)).isoformat(),
                end=(max(targets)+timedelta(days=3)).isoformat(),
                interval="1d",auto_adjust=False,actions=False,repair=True)
            for target in targets:
                start=(target-timedelta(days=7)).isoformat()
                end=(target+timedelta(days=3)).isoformat()
                raw_history=yf.Ticker(yahoo_symbol_name).history(
                    start=start,end=end,interval="1d",auto_adjust=False,actions=False,repair=False)
                repaired_history=yf.Ticker(yahoo_symbol_name).history(
                    start=start,end=end,interval="1d",auto_adjust=False,actions=False,repair=True)
                yahoo_evidence["sessions"][target.isoformat()]={
                    "single_window_raw":row_evidence(raw_history,target),
                    "single_window_repair":row_evidence(repaired_history,target),
                    "combined_window_repair":row_evidence(combined,target),
                }
        except Exception as exc:
            yahoo_evidence["error"]=str(exc)[:300]
        gaps.append({"security_id":sid,"ticker":ticker,
                     "missing_sessions":missing_sessions,"source_history":evidence,
                     "yahoo_raw_evidence":yahoo_evidence})
    out={k:v for k,v in summary.items() if k!="gaps"}
    out["ready_as_of_date"]=ready_as_of_date
    out["ready_sha256"]=ready_sha256
    out["gaps"]=gaps
    print("READY_CONTINUITY_AUDIT="+json.dumps(out,sort_keys=True))
if __name__=="__main__":
    main()
