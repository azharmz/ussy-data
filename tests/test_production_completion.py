import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from production_completion import marker_matches, lineage_valid, DOWNSTREAM_VERSION

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
 def test_lineage_mismatch_not_complete(self):
  i=self.identity();m={"schema_version":1,"status":"FULLY_COMPLETE","downstream_version":DOWNSTREAM_VERSION,**i};m["ready_sha256"]="other";self.assertFalse(marker_matches(m,i))
if __name__=="__main__":unittest.main()
