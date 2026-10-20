"""Audit recent-session continuity in the current canonical READY snapshot.

Read-only: downloads production/ready/current.json and its parquet from R2,
then reports securities that reached the candidate terminal date while missing
an observed intermediate session after --previous-as-of.
"""
from __future__ import annotations
import argparse, io, json, os
from datetime import timedelta
import pandas as pd
import yfinance as yf
from bootstrap_ohlcv import HISTORY_PREFIX, make_s3_client, yahoo_symbol
from export_ready import recent_session_gap_summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--previous-as-of", required=True)
    args=p.parse_args()
    s3=make_s3_client(); bucket=os.environ["R2_BUCKET_NAME"]
    manifest=json.loads(s3.get_object(Bucket=bucket,Key="production/ready/current.json")["Body"].read())
    raw=s3.get_object(Bucket=bucket,Key=manifest["parquet_key"])["Body"].read()
    frame=pd.read_parquet(io.BytesIO(raw), columns=["security_id","ticker","date"])
    summary=recent_session_gap_summary(frame,args.previous_as_of,manifest["as_of_date"])
    ticker_by_sid=(frame.sort_values("date").groupby("security_id")["ticker"].last().astype(str).to_dict())
    gaps=[]
    for sid, missing_sessions in sorted(summary["gaps"].items()):
        key=f"{HISTORY_PREFIX}{sid}.parquet"
        evidence={"history_key":key}
        try:
            history_raw=s3.get_object(Bucket=bucket,Key=key)["Body"].read()
            history=pd.read_parquet(io.BytesIO(history_raw),columns=["date"])
            history_dates=set(pd.to_datetime(history["date"],errors="raise").dt.date.astype(str))
            evidence.update({
                "history_rows":int(len(history)),
                "history_latest_date":max(history_dates) if history_dates else None,
                "missing_sessions_present_in_history":{d:d in history_dates for d in missing_sessions},
            })
        except Exception as exc:
            evidence["history_read_error"]=str(exc)[:300]
        ticker=ticker_by_sid.get(sid)
        yahoo_symbol_name=yahoo_symbol(ticker,sid) if ticker else None
        yahoo_evidence={"symbol":yahoo_symbol_name,"sessions":{}}
        try:
            targets=[pd.Timestamp(d).date() for d in missing_sessions]
            start=(min(targets)-timedelta(days=1)).isoformat()
            end=(max(targets)+timedelta(days=2)).isoformat()
            raw=yf.download(yahoo_symbol_name,start=start,end=end,interval="1d",
                            auto_adjust=False,actions=False,repair=False,keepna=True,
                            progress=False,group_by="ticker",threads=False,timeout=30)
            one=raw
            if isinstance(raw.columns,pd.MultiIndex):
                if yahoo_symbol_name in raw.columns.get_level_values(0):
                    one=raw[yahoo_symbol_name]
                elif yahoo_symbol_name in raw.columns.get_level_values(1):
                    one=raw.xs(yahoo_symbol_name,axis=1,level=1)
                else:
                    one=pd.DataFrame()
            fields=["Open","High","Low","Close","Adj Close","Volume"]
            for target in targets:
                matching=one.loc[pd.Index(pd.to_datetime(one.index,errors="coerce")).date==target] if not one.empty else pd.DataFrame()
                item={"row_count":int(len(matching))}
                if len(matching)==1 and all(field in matching.columns for field in fields):
                    row=matching.iloc[0]
                    vals={field:(None if pd.isna(row[field]) else float(row[field])) for field in fields}
                    missing_fields=[field for field,value in vals.items() if value is None]
                    if missing_fields:
                        item.update(status="INVALID",reason="missing_values:"+",".join(missing_fields),values=vals)
                    else:
                        valid=(min(vals[x] for x in ["Open","High","Low","Close","Adj Close"])>0
                               and vals["Volume"]>=0
                               and vals["High"]>=max(vals["Open"],vals["Close"],vals["Low"])
                               and vals["Low"]<=min(vals["Open"],vals["Close"],vals["High"]))
                        item.update(status="VALID" if valid else "INVALID",
                                    reason=None if valid else "OHLCV bounds/positivity check failed",
                                    values=vals)
                else:
                    item.update(status="NO_SINGLE_COMPLETE_ROW")
                yahoo_evidence["sessions"][target.isoformat()]=item
        except Exception as exc:
            yahoo_evidence["error"]=str(exc)[:300]
        gaps.append({"security_id":sid,"ticker":ticker,
                     "missing_sessions":missing_sessions,"source_history":evidence,
                     "yahoo_raw_evidence":yahoo_evidence})
    out={k:v for k,v in summary.items() if k!="gaps"}
    out["ready_as_of_date"]=manifest["as_of_date"]
    out["ready_sha256"]=manifest["sha256"]
    out["gaps"]=gaps
    print("READY_CONTINUITY_AUDIT="+json.dumps(out,sort_keys=True))
if __name__=="__main__":
    main()
