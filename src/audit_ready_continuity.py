"""Audit recent-session continuity in the current canonical READY snapshot.

Read-only: downloads production/ready/current.json and its parquet from R2,
then reports securities that reached the candidate terminal date while missing
an observed intermediate session after --previous-as-of.
"""
from __future__ import annotations
import argparse, io, json, os
import pandas as pd
from bootstrap_ohlcv import HISTORY_PREFIX, make_s3_client
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
        gaps.append({"security_id":sid,"ticker":ticker_by_sid.get(sid),
                     "missing_sessions":missing_sessions,"source_history":evidence})
    out={k:v for k,v in summary.items() if k!="gaps"}
    out["ready_as_of_date"]=manifest["as_of_date"]
    out["ready_sha256"]=manifest["sha256"]
    out["gaps"]=gaps
    print("READY_CONTINUITY_AUDIT="+json.dumps(out,sort_keys=True))
if __name__=="__main__":
    main()
