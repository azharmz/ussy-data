"""Compute and persist a non-production EMA candidate."""
from __future__ import annotations
import hashlib, io, json, os
from datetime import UTC, datetime
import numpy as np
import pandas as pd
from bootstrap_ohlcv import HISTORY_PREFIX, make_s3_client
from ema_state import PERIODS, PRICE_BASIS, StateNeedsRebuild, advance_state, bootstrap_state, validate_state_frame
from load_ema_state import load_ema_state
from load_ready import load_ready

UPDATE_METHOD = "per_security_bootstrap_rebuild_then_recursive_persisted_state_v5_canonical_history_ready_cutoff"
CANDIDATE_PREFIX = "validation/indicators/ema/"
REBUILD_HINT_KEY = "validation/indicators/ema/rebuild-required/current.json"
PROGRESS_EVERY = 100

def _progress(label, done, total):
    if done == 1 or done == total or done % PROGRESS_EVERY == 0: print(f"EMA {label}: {done}/{total}", flush=True)
def candidate_id(): return f"run-{os.getenv('GITHUB_RUN_ID','local')}-{os.getenv('GITHUB_RUN_ATTEMPT','1')}"
def candidate_keys():
    base=f"{CANDIDATE_PREFIX}{candidate_id()}"; return f"{base}.parquet", f"{base}.json"
def _read_full_history(s3,bucket,security_id):
    key=f"{HISTORY_PREFIX}{security_id}.parquet"
    return pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket,Key=key)["Body"].read()),engine="pyarrow")
def _ready_groups(frame):
    data=frame.copy(); data["security_id"]=data["security_id"].astype(str); data["date"]=pd.to_datetime(data["date"],errors="raise").dt.tz_localize(None).dt.normalize()
    return {sid:g.sort_values("date").reset_index(drop=True) for sid,g in data.groupby("security_id",sort=True)}
def _bootstrap_checked(s3,bucket,security_id,ready_rows):
    ready_latest=ready_rows.sort_values("date").iloc[-1]; ready_date=pd.Timestamp(ready_latest["date"]).normalize()
    history=_read_full_history(s3,bucket,security_id).copy(); history["date"]=pd.to_datetime(history["date"],errors="raise").dt.tz_localize(None).dt.normalize()
    history=history.loc[history["date"]<=ready_date].copy()
    if history.empty: raise RuntimeError(f"Canonical history has no rows through READY cutoff for {security_id}")
    state=bootstrap_state(history,security_id); state_date=pd.Timestamp(state["as_of_date"]).normalize()
    if state_date!=ready_date: raise RuntimeError(f"Long history/ready date mismatch for {security_id}: {state_date.date()} != {ready_date.date()}")
    if not np.isclose(float(state["last_price"]),float(ready_latest[PRICE_BASIS]),rtol=1e-10,atol=1e-10): raise RuntimeError(f"Long history/ready price mismatch for {security_id}")
    return state
def build_state(s3,bucket,ready,previous,force_rebuild_ids=None):
    groups=_ready_groups(ready); force_rebuild_ids=set(map(str,force_rebuild_ids or set()))&set(groups); print(f"EMA source: {len(groups)} ready securities",flush=True)
    previous_rows={}
    if previous is not None:
        previous=validate_state_frame(previous); previous_rows={str(r.security_id):r for r in previous.itertuples(index=False)}
    counters={"bootstrap":0,"recursive":0,"unchanged":0,"rebuild":0,"bootstrap_security_ids":[],"rebuild_security_ids":[],"forced_rebuild_security_ids":[]}; rows=[]; total=len(groups)
    if previous is None: print("EMA mode: initial per-security full-history bootstrap",flush=True)
    else: print(f"EMA mode: per-security update; {len(set(groups)-set(previous_rows))} new securities require bootstrap; {len(force_rebuild_ids)} equivalence hints require rebuild",flush=True)
    for done,(security_id,ready_rows) in enumerate(groups.items(),start=1):
        prior=previous_rows.get(security_id)
        if prior is None:
            rows.append(_bootstrap_checked(s3,bucket,security_id,ready_rows)); counters["bootstrap"]+=1; counters["bootstrap_security_ids"].append(security_id)
        elif security_id in force_rebuild_ids:
            rows.append(_bootstrap_checked(s3,bucket,security_id,ready_rows)); counters["rebuild"]+=1; counters["rebuild_security_ids"].append(security_id); counters["forced_rebuild_security_ids"].append(security_id)
        else:
            try:
                row,advanced=advance_state(prior._asdict(),ready_rows); rows.append(row); counters["recursive" if advanced else "unchanged"]+=1
            except StateNeedsRebuild:
                rows.append(_bootstrap_checked(s3,bucket,security_id,ready_rows)); counters["rebuild"]+=1; counters["rebuild_security_ids"].append(security_id)
        _progress("progress",done,total)
    state=validate_state_frame(pd.DataFrame(rows))
    if set(state["security_id"])!=set(groups): raise RuntimeError("EMA output security set does not match ready security set")
    return state,counters
def _read_ready_pointer_with_etag(s3,bucket):
    obj=s3.get_object(Bucket=bucket,Key="production/ready/current.json"); return json.loads(obj["Body"].read()),obj["ETag"]
def _read_rebuild_hints(s3,bucket):
    try: payload=json.loads(s3.get_object(Bucket=bucket,Key=REBUILD_HINT_KEY)["Body"].read())
    except Exception as exc:
        code=str(getattr(exc,"response",{}).get("Error",{}).get("Code",""))
        if code in {"NoSuchKey","404","NotFound"} or isinstance(exc,KeyError): return set()
        raise
    ids=payload.get("security_ids",[])
    if not isinstance(ids,list): raise RuntimeError("EMA rebuild hint security_ids must be a list")
    return set(map(str,ids))
def main():
    s3,bucket=make_s3_client(),os.environ["R2_BUCKET_NAME"]; print("EMA candidate: loading ready source and prior production state",flush=True)
    ready,ready_manifest=load_ready(s3,bucket); observed_ready,ready_etag=_read_ready_pointer_with_etag(s3,bucket)
    if observed_ready!=ready_manifest: raise RuntimeError("Ready pointer changed while EMA candidate was loading source data")
    previous=None
    try: previous,_=load_ema_state(s3,bucket)
    except (FileNotFoundError,ValueError): previous=None
    rebuild_hints=_read_rebuild_hints(s3,bucket)
    if os.getenv("EMA_FORCE_FULL_REBUILD") == "1":
        rebuild_hints=set(_ready_groups(ready))
        print(f"EMA candidate: FORCE full-history rebuild for all {len(rebuild_hints)} READY securities",flush=True)
    if rebuild_hints: print(f"EMA candidate: consuming {len(rebuild_hints)} equivalence rebuild hints",flush=True)
    state,counters=build_state(s3,bucket,ready,previous,rebuild_hints); print(f"EMA state built: {len(state)} securities; bootstrap={counters['bootstrap']}, recursive={counters['recursive']}, unchanged={counters['unchanged']}, rebuild={counters['rebuild']}",flush=True)
    buffer=io.BytesIO(); state.to_parquet(buffer,engine="pyarrow",index=False,compression="zstd"); body=buffer.getvalue(); digest=hashlib.sha256(body).hexdigest(); parquet_key,manifest_key=candidate_keys()
    print(f"EMA candidate: uploading {parquet_key}",flush=True); s3.put_object(Bucket=bucket,Key=parquet_key,Body=body,ContentType="application/vnd.apache.parquet")
    uploaded=s3.get_object(Bucket=bucket,Key=parquet_key)["Body"].read()
    if len(uploaded)!=len(body) or hashlib.sha256(uploaded).hexdigest()!=digest: raise RuntimeError("EMA candidate verification failed")
    print("EMA candidate: upload checksum verified",flush=True)
    if s3.head_object(Bucket=bucket,Key="production/ready/current.json")["ETag"]!=ready_etag: raise RuntimeError("Ready pointer changed before EMA candidate completed; candidate left unpromoted")
    as_of=pd.to_datetime(state["as_of_date"]); manifest={"schema_version":1,"candidate_created_at":datetime.now(UTC).isoformat(),"status":"CANDIDATE_NOT_PRODUCTION_APPROVED","price_basis":PRICE_BASIS,"periods":list(PERIODS),"securities":len(state),"security_ids":state["security_id"].tolist(),"candidate_parquet_key":parquet_key,"candidate_sha256":digest,"source_ready_parquet_key":ready_manifest["parquet_key"],"source_ready_sha256":ready_manifest["sha256"],"source_ready_created_at":ready_manifest.get("created_at"),"update_method":UPDATE_METHOD,"bootstrap_count":counters["bootstrap"],"recursive_count":counters["recursive"],"unchanged_count":counters["unchanged"],"rebuild_count":counters["rebuild"],"bootstrap_security_ids":counters["bootstrap_security_ids"],"rebuild_security_ids":counters["rebuild_security_ids"],"forced_rebuild_security_ids":counters["forced_rebuild_security_ids"],"as_of_date_min":as_of.min().date().isoformat(),"as_of_date_max":as_of.max().date().isoformat()}
    s3.put_object(Bucket=bucket,Key=manifest_key,Body=json.dumps(manifest,indent=2).encode(),ContentType="application/json"); print("EMA candidate: manifest published; candidate remains non-production until equivalence gate passes",flush=True); print(json.dumps({k:v for k,v in manifest.items() if k!="security_ids"},indent=2))
if __name__=="__main__": main()
