"""R2-only pre-backtest gate. Emits QC evidence, never performance metrics."""
import csv
import hashlib
import io
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from ohlcv_qc import validate_frame

READY_POINTER = "production/ready/current.json"
SPY_POINTER = "benchmarks/SPY/current.json"
PARITY_FIXTURE = Path(__file__).resolve().parents[1] / "config/ussy-swing-parity-v1.json"
REQUIRED = ("date", "security_id", "ticker", "open", "high", "low", "close", "adj_close", "volume")


def adjusted_ohlc(frame):
    import numpy as np
    result = frame.copy()
    factor = result["adj_close"].astype(float) / result["close"].astype(float)
    if not np.isfinite(factor).all() or (factor <= 0).any():
        raise ValueError("Invalid adj_close/close factor")
    for column in ("open", "high", "low", "close"):
        result[column] = result[column].astype(float) * factor
    values = result[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Adjusted OHLC is nonfinite or nonpositive")
    if (result["high"] < result[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("Adjusted high violates OHLC range")
    if (result["low"] > result[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("Adjusted low violates OHLC range")
    result["adjustment_factor"] = factor
    return result


def common_calendar(stock, spy):
    import pandas as pd
    left = stock.copy(); right = spy.copy()
    left["date"] = pd.to_datetime(left["date"], utc=True)
    right["date"] = pd.to_datetime(right["date"], utc=True)
    return left.merge(right[["date", "close"]], on="date", how="inner", suffixes=("_stock", "_spy")).sort_values("date")


def relative_strength_20(aligned):
    if len(aligned) < 21:
        raise ValueError("Fewer than 21 shared stock-SPY bars")
    stock = aligned["close_stock"]
    spy = aligned["close_spy"]
    return round(((stock.iloc[-1] / stock.iloc[-21] - 1) - (spy.iloc[-1] / spy.iloc[-21] - 1)) * 100, 2)


def simulate_case(case):
    state = dict(case["state"])
    for bar in case["bars"]:
        new_days = state["days_in_status"] + 1
        if state["status"] == "pending":
            triggered = bar["high"] >= state["entry_price"] if state["strategy"] == "breakout" else bar["low"] <= state["entry_price"]
            stop = bar["low"] <= state["stop_loss"]
            if triggered and stop:
                fill = round(max(bar["open"], state["entry_price"]) if state["strategy"] == "breakout" else min(bar["open"], state["entry_price"]), 2)
                state.update(status="sl_hit", entry_price_actual=fill, exit_price=min(round(min(bar["open"], state["stop_loss"]), 2), fill), days_in_status=0)
            elif stop:
                state.update(status="missed", missed_reason="sl_hit_before_entry", days_in_status=new_days)
            elif triggered:
                fill = round(max(bar["open"], state["entry_price"]) if state["strategy"] == "breakout" else min(bar["open"], state["entry_price"]), 2)
                state.update(status="entered", entry_price_actual=fill, days_in_status=0)
            elif new_days >= 3:
                state.update(status="missed", missed_reason="expired", days_in_status=new_days)
            else:
                state["days_in_status"] = new_days
        elif state["status"] == "entered":
            if bar["low"] <= state["stop_loss"]:
                state.update(status="sl_hit", exit_price=round(min(bar["open"], state["stop_loss"]), 2), days_in_status=new_days)
            elif bar["high"] >= state["take_profit"]:
                state.update(status="tp_hit", exit_price=state["take_profit"], days_in_status=new_days)
            elif new_days >= 10:
                state.update(status="closed_timeout", exit_price=round(bar["close"], 2), days_in_status=new_days)
            else:
                state["days_in_status"] = new_days
    return state


def parity_results(fixture):
    results = []
    for case in fixture["cases"]:
        actual = simulate_case(case)
        mismatches = {key: {"expected": value, "actual": actual.get(key)} for key, value in case["expected"].items() if actual.get(key) != value}
        results.append({"name": case["name"], "passed": not mismatches, "mismatches": mismatches})
    return results


def main():
    import pandas as pd
    from bootstrap_ohlcv import make_s3_client
    out = Path("diagnostics/prebacktest-gate"); out.mkdir(parents=True, exist_ok=False)
    report = {"schema_version":1, "started_at":datetime.now(UTC).isoformat(), "status":"running", "mode":"r2_read_only", "yahoo_downloads":0, "r2_writes":0, "performance_metrics_emitted":False, "gates":{}, "errors":[], "objects":{}}
    rows=[]
    def save():
        (out/"report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        if rows:
            with (out/"tickers.csv").open("w", newline="", encoding="utf-8") as handle:
                writer=csv.DictWriter(handle, fieldnames=sorted(set().union(*(r.keys() for r in rows)))); writer.writeheader(); writer.writerows(rows)
    save()
    try:
        s3, bucket = make_s3_client(), os.environ["R2_BUCKET_NAME"]
        captured={}
        def read(key):
            obj=s3.get_object(Bucket=bucket, Key=key); body=obj["Body"].read(); captured[key]=obj["ETag"]
            report["objects"][key]={"etag":obj["ETag"],"bytes":len(body),"sha256":hashlib.sha256(body).hexdigest()}; return body
        ready_pointer=json.loads(read(READY_POINTER)); spy_pointer=json.loads(read(SPY_POINTER))
        report["input_provenance"]={
            "ready_pointer_key":READY_POINTER,
            "ready_parquet_key":ready_pointer.get("parquet_key"),
            "ready_created_at":ready_pointer.get("created_at"),
            "ready_snapshot_date":ready_pointer.get("snapshot_date"),
            "spy_pointer_key":SPY_POINTER,
            "spy_parquet_key":spy_pointer.get("parquet_key"),
            "spy_created_at":spy_pointer.get("created_at"),
            "spy_first_date":spy_pointer.get("first_date"),
            "spy_last_date":spy_pointer.get("last_date"),
            "spy_adjustment_policy":spy_pointer.get("adjustment_policy"),
        }
        if ready_pointer.get("securities") != 1223 or len(ready_pointer.get("security_ids", [])) != 1223:
            raise ValueError("Ready pointer does not contain exactly 1223 securities")
        ready_payload=read(ready_pointer["parquet_key"])
        if hashlib.sha256(ready_payload).hexdigest() != ready_pointer["sha256"]: raise ValueError("Ready pointer checksum mismatch")
        ready=pd.read_parquet(io.BytesIO(ready_payload), columns=["security_id", "ticker"])
        ids=sorted(set(ready["security_id"].astype(str)))
        if len(ids) != 1223: raise ValueError("Ready Parquet identity count mismatch")
        spy_payload=read(spy_pointer["parquet_key"])
        if hashlib.sha256(spy_payload).hexdigest() != spy_pointer["sha256"]: raise ValueError("SPY pointer checksum mismatch")
        spy=pd.read_parquet(io.BytesIO(spy_payload))
        if len(spy) != 8458 or set(spy["security_id"]) != {"benchmark:SPY"}: raise ValueError("SPY identity/row-count mismatch")
        validate_frame(spy); spy_adj=adjusted_ohlc(spy)
        ticker_map=dict(zip(ready["security_id"].astype(str), ready["ticker"].astype(str)))
        for i,sid in enumerate(ids):
            item={"security_id":sid,"ticker":ticker_map[sid],"status":"failed"}; rows.append(item)
            try:
                payload=read(f"backtest/ohlcv/{sid}.parquet"); frame=pd.read_parquet(io.BytesIO(payload))
                if (set(REQUIRED)-set(frame.columns) or set(frame["security_id"].astype(str)) != {sid}
                        or set(frame["ticker"].astype(str)) != {ticker_map[sid]}):
                    raise ValueError("Schema, security identity, or ticker mismatch")
                dates=pd.to_datetime(frame["date"], utc=True)
                if frame.empty or dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing: raise ValueError("Invalid, duplicate, or unordered dates")
                validate_frame(frame); adjusted=adjusted_ohlc(frame); aligned=common_calendar(adjusted, spy_adj); rs=relative_strength_20(aligned)
                item.update(status="passed",history_rows=len(frame),first_date=dates.iloc[0].date().isoformat(),last_date=dates.iloc[-1].date().isoformat(),shared_spy_rows=len(aligned),shared_last_date=aligned["date"].iloc[-1].date().isoformat(),factor_min=float(adjusted["adjustment_factor"].min()),factor_max=float(adjusted["adjustment_factor"].max()),rs20_last=rs)
            except Exception as exc:
                item["error_type"]=type(exc).__name__; item["error"]=str(exc)[:300]
            if (i+1)%50==0: print(f"Audited {i+1}/1223 full histories", flush=True); save()
        failures=[r for r in rows if r["status"]!="passed"]
        fixture=json.loads(PARITY_FIXTURE.read_text(encoding="utf-8")); parity=parity_results(fixture)
        report["parity"]={"fixture":str(PARITY_FIXTURE.name),"source_repository":fixture["source_repository"],"source_commit":fixture["source_commit"],"results":parity}
        report["gates"]={"ready_count":{"passed":len(ids)==1223,"actual":len(ids)},"spy":{"passed":len(spy)==8458,"rows":len(spy)},"full_history_qc":{"passed":not failures,"passed_tickers":len(rows)-len(failures),"failed_tickers":len(failures)},"adjusted_ohlc":{"passed":not failures,"formula":"factor=adj_close/close; adjusted OHLC=raw OHLC*factor"},"shared_spy_calendar":{"passed":not failures,"method":"inner join on date before RS20"},"engine_parity":{"passed":all(x["passed"] for x in parity),"cases":len(parity)}}
        for key,etag in captured.items():
            if s3.head_object(Bucket=bucket, Key=key)["ETag"] != etag: report["errors"].append({"stage":"atomicity","key":key,"error":"etag_changed"})
        passed=all(g["passed"] for g in report["gates"].values()) and not report["errors"]
        report["status"]="passed" if passed else "failed"; report["failed_or_skipped_tickers"]=failures; report["finished_at"]=datetime.now(UTC).isoformat(); save()
        return 0 if passed else 1
    except Exception as exc:
        report.update(status="failed",error_type=type(exc).__name__,error=str(exc)[:500],finished_at=datetime.now(UTC).isoformat()); save(); return 1

if __name__ == "__main__": raise SystemExit(main())
