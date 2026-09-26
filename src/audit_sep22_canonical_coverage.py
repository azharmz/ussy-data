import io,json,os
import pandas as pd
from bootstrap_ohlcv import make_s3_client
from compliance import is_eligible
from security_lifecycle import acquisition_allowed
T=pd.Timestamp("2026-09-22")
s3=make_s3_client(); b=os.environ["R2_BUCKET_NAME"]
u=json.loads(s3.get_object(Bucket=b,Key="universe/current.json")["Body"].read()); snap=str(u["snapshot_date"])
rows=json.loads(s3.get_object(Bucket=b,Key=f"universe/membership/{snap}.json")["Body"].read())["records"]
eligible={str(r["security_id"]):r for r in rows if is_eligible(r)}
hist=[]
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=b,Prefix="history/ohlcv/"):
    hist += [o["Key"] for o in page.get("Contents",[]) if o["Key"].endswith(".parquet")]
oper={k.split("/")[-1][:-8] for k in hist}&set(eligible)
allowed=sorted(s for s in oper if acquisition_allowed(s,T.date()))
present=[]; absent=[]
for i,sid in enumerate(allowed,1):
    f=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=b,Key=f"history/ohlcv/{sid}.parquet")["Body"].read()),columns=["date"])
    dates=pd.to_datetime(f["date"]).dt.normalize()
    (present if (dates==T).any() else absent).append(sid)
    if i%200==0: print(f"SEP22_PROGRESS {i}/{len(allowed)} present={len(present)} absent={len(absent)}",flush=True)
print("SEP22_COVERAGE="+json.dumps({"eligible":len(eligible),"operational":len(oper),"allowed":len(allowed),"present":len(present),"absent":len(absent),"absent_ids":absent},sort_keys=True),flush=True)
