"""Broad read-only Yahoo split acquisition validation.

Purpose: test Yahoo as the scalable primary acquisition candidate on the current
universe without changing the frozen corporate-action contract. Tiingo is not
called here; independent validation remains a separate bounded stage.

No R2 access or writes.
"""
from __future__ import annotations
import json
from pathlib import Path
import yfinance as yf

UNIVERSE=Path("universe/musaffa_membership_2026-08-28.json")
BATCH_SIZE=100
START="2000-01-01"
END="2026-09-18"

def extract(df,ticker):
    if not hasattr(df.columns,"nlevels") or df.columns.nlevels < 2:
        return None,"missing_multiindex"
    if ticker not in df.columns.get_level_values(0):
        return None,"missing_ticker_group"
    sub=df[ticker]
    if "Stock Splits" not in sub.columns:
        return None,"missing_stock_splits_column"
    events={idx.strftime("%Y-%m-%d"):float(v) for idx,v in sub["Stock Splits"].dropna().items() if float(v)!=0.0}
    return events,None

def main():
    payload=json.loads(UNIVERSE.read_text())
    records=[r for r in payload["records"] if r.get("sharia_compliance")=="COMPLIANT" and r.get("ticker")]
    # De-duplicate aliases while retaining canonical identity evidence.
    by_ticker={}
    for r in records:
        by_ticker.setdefault(r["ticker"].upper(),r["security_id"])
    symbols=sorted(by_ticker)
    events=[]; failures=[]; batches=0
    for i in range(0,len(symbols),BATCH_SIZE):
        batch=symbols[i:i+BATCH_SIZE]; batches+=1
        try:
            df=yf.download(batch,start=START,end=END,interval="1d",auto_adjust=False,actions=True,
                repair=False,progress=False,threads=False,group_by="ticker",multi_level_index=True,timeout=30)
        except Exception as exc:
            failures.append({"batch":batches,"symbols":batch,"error":f"batch_exception:{type(exc).__name__}:{exc}"})
            continue
        for ticker in batch:
            found,err=extract(df,ticker)
            if err:
                failures.append({"batch":batches,"ticker":ticker,"security_id":by_ticker[ticker],"error":err})
                continue
            for day,factor in found.items():
                events.append({"security_id":by_ticker[ticker],"provider_ticker":ticker,"effective_date":day,"split_factor":factor})
    # Discovery output is evidence only: no canonical publication.
    unique={(e["security_id"],e["effective_date"]):e for e in events}
    factors=[e["split_factor"] for e in unique.values()]
    out={
        "status":"PASS" if not failures else "PASS_WITH_ACQUISITION_GAPS",
        "mode":"READ_ONLY_BROAD_YAHOO_DISCOVERY",
        "frozen_contract_changed":False,
        "universe_snapshot":str(UNIVERSE),
        "universe_records_considered":len(records),
        "unique_tickers":len(symbols),
        "batch_size":BATCH_SIZE,
        "batch_count":batches,
        "history_start":START,
        "history_end_exclusive":END,
        "discovered_event_rows":len(unique),
        "distinct_securities_with_events":len({e["security_id"] for e in unique.values()}),
        "forward_events":sum(f>1 for f in factors),
        "reverse_events":sum(0<f<1 for f in factors),
        "acquisition_gap_count":len(failures),
        "acquisition_gaps":failures[:100],
        "tiingo_requests":0,
        "r2_reads":0,
        "r2_writes":0,
    }
    print(json.dumps(out,indent=2,sort_keys=True))
    if failures:
        raise SystemExit(2)

if __name__=="__main__": main()
