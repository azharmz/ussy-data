import io,json,os
import pandas as pd
from bootstrap_ohlcv import make_s3_client
T=pd.Timestamp("2026-09-22")
BASE="recovery/stooq-2026-09-25/sep22-repair/"
STAGE="recovery/stooq-2026-09-25/history/ohlcv/"
s3=make_s3_client(); b=os.environ["R2_BUCKET_NAME"]
m=json.loads(s3.get_object(Bucket=b,Key=BASE+"manifest.json")["Body"].read())
y=json.loads(s3.get_object(Bucket=b,Key=BASE+"yahoo-raw-reaudit.json")["Body"].read())
ym={x["security_id"]:x for x in y["available"]}
counts={"exact":0,"volume_only":0,"price_diff":0}
samples=[]
for n,sid in enumerate(m["stooq_ready"],1):
    d=pd.read_parquet(io.BytesIO(s3.get_object(Bucket=b,Key=STAGE+sid+".parquet")["Body"].read()))
    d["date"]=pd.to_datetime(d["date"]).dt.normalize()
    r=d.loc[d["date"]==T].iloc[0]; q=ym[sid]["row"]
    pairs=[("open","Open"),("high","High"),("low","Low"),("close","Close"),("adj_close","Adj Close")]
    pdiff={a:float(r[a])-float(q[z]) for a,z in pairs}
    peq=all(abs(v)<1e-8 for v in pdiff.values())
    vd=int(r["volume"])-int(q["Volume"])
    k="exact" if peq and vd==0 else ("volume_only" if peq else "price_diff")
    counts[k]+=1
    if k!="exact" and len(samples)<30: samples.append({"security_id":sid,"ticker":ym[sid]["ticker"],"kind":k,"price_diff":pdiff,"volume_diff":vd})
    if n%100==0 or n==len(m["stooq_ready"]): print("progress",n,len(m["stooq_ready"]),counts,flush=True)
u=sorted(set(m["targets"])-set(m["stooq_ready"]))
out={"compared":len(m["stooq_ready"]),**counts,"unresolved":u,"unresolved_yahoo_available":sum(x in ym for x in u),"samples":samples}
print("STOOQ_YAHOO_COMPARE="+json.dumps(out,sort_keys=True),flush=True)
