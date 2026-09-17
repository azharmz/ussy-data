import io
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ema_state import bootstrap_state
from publish_ema_state import build_state

def frame(sid,ticker,prices,start="2020-01-01"):
    return pd.DataFrame({"date":pd.bdate_range(start,periods=len(prices)),"security_id":sid,"ticker":ticker,"adj_close":np.asarray(prices,dtype="float64")})
class FakeS3:
    def __init__(self,histories):
        self.objects={}
        for sid,history in histories.items():
            buf=io.BytesIO(); history.to_parquet(buf,engine="pyarrow",index=False); self.objects[f"history/ohlcv/{sid}.parquet"]=buf.getvalue()
    def get_object(self,Bucket,Key): return {"Body":io.BytesIO(self.objects[Key])}
class PublishEMAStateTests(unittest.TestCase):
    def test_membership_change_bootstraps_only_new_security(self):
        a=frame("A","AAA",50+np.linspace(0,20,300)); b=frame("B","BBB",30+np.linspace(0,10,300)); ready=pd.concat([a.iloc[-250:],b.iloc[-250:]],ignore_index=True); prior=pd.DataFrame([bootstrap_state(a.iloc[:299],"A")])
        state,counters=build_state(FakeS3({"A":a,"B":b}),"bucket",ready,prior); by_id=state.set_index("security_id")
        self.assertEqual(counters["bootstrap"],1); self.assertEqual(counters["recursive"],1); self.assertEqual(counters["rebuild"],0)
        for sid,reference in [("A",bootstrap_state(a,"A")),("B",bootstrap_state(b,"B"))]:
            for field in ["last_price","ema20","ema50","ema150","ema200"]: self.assertTrue(np.isclose(by_id.loc[sid,field],reference[field],rtol=1e-12,atol=1e-12))
    def test_unchanged_membership_keeps_recursive_path(self):
        a=frame("A","AAA",50+np.linspace(0,20,300)); prior=pd.DataFrame([bootstrap_state(a.iloc[:299],"A")]); ready=a.iloc[-250:].copy(); state,counters=build_state(FakeS3({"A":a}),"bucket",ready,prior)
        self.assertEqual(counters["recursive"],1)
        for field in ["last_price","ema20","ema50","ema150","ema200"]: self.assertTrue(np.isclose(state.iloc[0][field],bootstrap_state(a,"A")[field],rtol=1e-12,atol=1e-12))
    def test_equivalence_hint_forces_only_named_security_rebuild(self):
        a=frame("A","AAA",50+np.linspace(0,20,300)); b=frame("B","BBB",30+np.linspace(0,10,300)); ready=pd.concat([a.iloc[-250:],b.iloc[-250:]],ignore_index=True); prior=pd.DataFrame([bootstrap_state(a.iloc[:299],"A"),bootstrap_state(b.iloc[:299],"B")])
        state,counters=build_state(FakeS3({"A":a,"B":b}),"bucket",ready,prior,{"A"}); self.assertEqual(counters["rebuild"],1); self.assertEqual(counters["recursive"],1)
    def test_rebuild_truncates_canonical_history_to_ready_date(self):
        a=frame("A","AAA",50+np.linspace(0,20,301)); ready=a.iloc[-251:-1].copy(); prior=pd.DataFrame([bootstrap_state(a,"A")])
        state,counters=build_state(FakeS3({"A":a}),"bucket",ready,prior)
        reference=bootstrap_state(a.iloc[:-1],"A"); self.assertEqual(counters["rebuild"],1); self.assertEqual(pd.Timestamp(state.iloc[0]["as_of_date"]),pd.Timestamp(reference["as_of_date"]))
        self.assertTrue(np.isclose(state.iloc[0]["last_price"],reference["last_price"]))
if __name__=="__main__": unittest.main()
