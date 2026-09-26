import sys, unittest
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import security_lifecycle
class TickerTransitionRepairContractTests(unittest.TestCase):
 def test_verified_cases_resolve_to_successor_without_identity_change(self):
  cases={"US37892C1062":("GGRP","BTLN"),"US45769N1054":("ISSC","IA"),"VGG9888Q1110":("YYGH","YFOR")}
  for sid,(old,new) in cases.items():
   rec=security_lifecycle.records()[sid]
   self.assertEqual(rec["successor_security_id"],sid)
   self.assertEqual(rec["lifecycle_status"],"VERIFIED_TICKER_CHANGE_SAME_SECURITY")
   self.assertEqual(security_lifecycle.acquisition_ticker(sid,old,date(2026,9,22)),new)
if __name__=="__main__": unittest.main()
