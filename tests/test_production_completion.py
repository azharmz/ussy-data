import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from production_completion import marker_matches, lineage_valid, intended_finalized_identity, recovery_stage, quarantined_ready, DOWNSTREAM_VERSION
import production_completion
from datetime import date

class CompletionTests(unittest.TestCase):
 def identity(self):
  return {"finalized_through":"2026-09-18","ready_as_of_date":"2026-09-18","ready_parquet_key":"production/ready/runs/2026-09-18.parquet","ready_sha256":"r","ema_parquet_key":"production/indicators/ema/runs/x.parquet","ema_sha256":"e","ema_source_ready_parquet_key":"production/ready/runs/2026-09-18.parquet","ema_source_ready_sha256":"r"}
 def test_exact_completed_marker(self):
  i=self.identity();m={"schema_version":1,"status":"FULLY_COMPLETE","downstream_version":DOWNSTREAM_VERSION,**i};self.assertTrue(lineage_valid(i));self.assertTrue(marker_matches(m,i))
 def test_ready_only_not_complete(self):
  i=self.identity();i["ema_parquet_key"]=None;self.assertFalse(lineage_valid(i))
 def test_ema_wrong_ready_not_complete(self):
  i=self.identity();i["ema_source_ready_sha256"]="old";self.assertFalse(lineage_valid(i))
 def test_downstream_incomplete_not_complete(self):
  i=self.identity();m={"schema_version":1,"status":"FULLY_COMPLETE","downstream_version":"old",**i};self.assertFalse(marker_matches(m,i))
 def test_weekend_maps_to_friday(self):
  old=production_completion.finalized_through
  try:
   production_completion.finalized_through=lambda:date(2026,9,19)
   self.assertEqual(intended_finalized_identity(),"2026-09-18")
  finally:production_completion.finalized_through=old
 def test_recovery_stage_ready_without_ema_resumes_ema(self):
  i=self.identity()
  class S:
   def get_object(self,**kw):
    import io,json
    key=kw["Key"]
    if key=="production/ready/current.json":
     body={"as_of_date":"2026-09-18","parquet_key":i["ready_parquet_key"],"sha256":"r"}
     return {"Body":io.BytesIO(json.dumps(body).encode())}
    raise KeyError(key)
  old=production_completion.intended_finalized_identity
  try:
   production_completion.intended_finalized_identity=lambda:"2026-09-18"
   self.assertEqual(recovery_stage(S(),"b")[0],"ema")
  finally:production_completion.intended_finalized_identity=old
 def test_lineage_mismatch_not_complete(self):
  i=self.identity();m={"schema_version":1,"status":"FULLY_COMPLETE","downstream_version":DOWNSTREAM_VERSION,**i};m["ready_sha256"]="other";self.assertFalse(marker_matches(m,i))
 def test_known_bad_ready_is_quarantined(self):
  import json,tempfile
  from pathlib import Path
  i=self.identity();i["ready_as_of_date"]="2026-09-23";i["ready_sha256"]="badsha"
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"q.json";p.write_text(json.dumps({"quarantined_ready":[{"ready_as_of_date":"2026-09-23","ready_sha256":"badsha","reason":"continuity"}]}))
   self.assertIsNotNone(quarantined_ready(i,p))
   i["ready_sha256"]="other";self.assertIsNone(quarantined_ready(i,p))
 def test_recovery_stage_quarantined_ready_forces_ohlcv(self):
  i=self.identity()
  class S:
   def get_object(self,**kw):
    import io,json
    if kw["Key"]=="production/ready/current.json":
     return {"Body":io.BytesIO(json.dumps({"as_of_date":"2026-09-23","parquet_key":"production/ready/runs/2026-09-23.parquet","sha256":"ee6bdfae279e87c5cb7e6f545bb1ba24c38b2902f475d9e7e732982696c7898f"}).encode())}
    raise KeyError(kw["Key"])
  old_target=production_completion.intended_finalized_identity
  try:
   production_completion.intended_finalized_identity=lambda:"2026-09-23"
   self.assertEqual(recovery_stage(S(),"b")[0],"ohlcv")
  finally:production_completion.intended_finalized_identity=old_target
if __name__=="__main__":unittest.main()
