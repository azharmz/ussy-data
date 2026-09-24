"""Audit recent-session continuity in the current canonical READY snapshot.

Read-only: downloads production/ready/current.json and its parquet from R2,
then reports securities that reached the candidate terminal date while missing
an observed intermediate session after --previous-as-of.
"""
from __future__ import annotations
import argparse, io, json, os
import pandas as pd
from bootstrap_ohlcv import make_s3_client
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
    gaps=[{"security_id":sid,"ticker":ticker_by_sid.get(sid),"missing_sessions":dates}
          for sid,dates in sorted(summary["gaps"].items())]
    out={k:v for k,v in summary.items() if k!="gaps"}
    out["ready_as_of_date"]=manifest["as_of_date"]
    out["ready_sha256"]=manifest["sha256"]
    out["gaps"]=gaps
    print("READY_CONTINUITY_AUDIT="+json.dumps(out,sort_keys=True))
if __name__=="__main__":
    main()
