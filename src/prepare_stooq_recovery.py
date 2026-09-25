"""Stage a one-off Stooq full-history recovery candidate. Never writes R2.

Stooq bulk files are treated as a frozen recovery snapshot. Their OHLC values are
preserved exactly; adj_close is set equal to close because Stooq has no separate
adjusted-close field. This is an explicit recovery contract, not a claim that
Stooq close is raw exchange close.

Only rows mapped by a COPIED manifest entry are staged. Structural OHLC failures
are fail-closed per security so bad source bars cannot silently enter a candidate.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import UTC, datetime
from pathlib import Path
import pandas as pd
from ohlcv_qc import validate_frame

CANONICAL = ["date","security_id","ticker","open","high","low","close","adj_close","volume"]

def read_stooq(path: Path, security_id: str, ticker: str) -> pd.DataFrame:
    f=pd.read_csv(path)
    f.columns=[str(c).strip().upper().strip("<>") for c in f.columns]
    required={"DATE","OPEN","HIGH","LOW","CLOSE","VOL"}
    missing=required-set(f.columns)
    if missing: raise ValueError(f"missing Stooq columns: {sorted(missing)}")
    out=pd.DataFrame({
        "date":pd.to_datetime(f["DATE"].astype(str),format="%Y%m%d",errors="coerce"),
        "security_id":security_id,
        "ticker":ticker,
        "open":pd.to_numeric(f["OPEN"],errors="coerce"),
        "high":pd.to_numeric(f["HIGH"],errors="coerce"),
        "low":pd.to_numeric(f["LOW"],errors="coerce"),
        "close":pd.to_numeric(f["CLOSE"],errors="coerce"),
        "adj_close":pd.to_numeric(f["CLOSE"],errors="coerce"),
        "volume":pd.to_numeric(f["VOL"],errors="coerce"),
    })
    if out["date"].isna().any(): raise ValueError("invalid date")
    if out[["open","high","low","close","adj_close","volume"]].isna().any().any(): raise ValueError("null/non-numeric OHLCV")
    out["volume"]=out["volume"].astype("int64")
    out=out.drop_duplicates("date",keep="last").sort_values("date").reset_index(drop=True)
    validate_frame(out)
    return out[CANONICAL]

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda:fh.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source-dir",required=True,type=Path)
    p.add_argument("--manifest",required=True,type=Path)
    p.add_argument("--output-dir",required=True,type=Path)
    a=p.parse_args()
    a.output_dir.mkdir(parents=True,exist_ok=True)
    m=pd.read_csv(a.manifest)
    need={"security_id","ticker","status"}
    if not need.issubset(m.columns): raise ValueError(f"manifest missing {sorted(need-set(m.columns))}")
    copied=m[m["status"].astype(str).str.upper()=="COPIED"].copy()
    filename_col=next((c for c in ["stooq_filename","destination_filename","filename","matched_filename"] if c in copied.columns),None)
    if not filename_col: raise ValueError("manifest needs destination_filename/filename/stooq_filename/matched_filename")
    results=[]
    for i,row in enumerate(copied.itertuples(index=False),1):
        d=row._asdict(); sid=str(d["security_id"]); ticker=str(d["ticker"]); fn=str(d[filename_col]); src=a.source_dir/fn
        try:
            if not src.is_file(): raise FileNotFoundError(src)
            frame=read_stooq(src,sid,ticker)
            dst=a.output_dir/f"{sid}.parquet"
            frame.to_parquet(dst,index=False,compression="zstd")
            results.append({"security_id":sid,"ticker":ticker,"source_file":fn,"status":"STAGED","rows":len(frame),"first_date":frame.date.iloc[0].date().isoformat(),"last_date":frame.date.iloc[-1].date().isoformat(),"source_sha256":sha256(src),"candidate_sha256":sha256(dst),"rejected_bar_count":len(rejected_bars),"rejected_bars":rejected_bars})
        except Exception as e:
            results.append({"security_id":sid,"ticker":ticker,"source_file":fn,"status":"REJECTED","error":str(e)[:500]})
        if i%100==0: print(f"progress={i}/{len(copied)}")
    report={"schema_version":1,"created_at":datetime.now(UTC).isoformat(),"mode":"STAGING_ONLY_NO_R2_WRITES","price_contract":{"stooq_ohlc":"preserved","adj_close":"stooq_close","warning":"Recovery snapshot basis; not asserted to be raw exchange OHLC."},"expected":len(copied),"staged":sum(x["status"]=="STAGED" for x in results),"rejected":sum(x["status"]=="REJECTED" for x in results),"quarantined_bars":sum(x.get("rejected_bar_count",0) for x in results),"results":results}
    (a.output_dir/"stooq-recovery-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:report[k] for k in ["expected","staged","rejected"]}))

if __name__=="__main__": main()
