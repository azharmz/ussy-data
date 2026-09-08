import json
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from prebacktest_gate import adjusted_ohlc, common_calendar, parity_results, relative_strength_20


class PrebacktestGateTests(unittest.TestCase):
    def test_adjusted_ohlc_matches_auto_adjust_contract(self):
        frame=pd.DataFrame([{"date":"2026-01-01","open":100,"high":110,"low":90,"close":100,"adj_close":50,"volume":1}])
        result=adjusted_ohlc(frame)
        self.assertEqual(result.iloc[0]["open"],50)
        self.assertEqual(result.iloc[0]["high"],55)

    def test_adjusted_ohlc_rejects_invalid_factor(self):
        frame=pd.DataFrame([{"date":"2026-01-01","open":100,"high":110,"low":90,"close":0,"adj_close":50,"volume":1}])
        with self.assertRaisesRegex(ValueError,"factor"):
            adjusted_ohlc(frame)

    def test_rs_uses_shared_calendar(self):
        dates=pd.date_range("2026-01-01",periods=22)
        stock=pd.DataFrame({"date":dates,"close":[100+i for i in range(22)]})
        spy=pd.DataFrame({"date":dates.delete(5),"close":[100]*21})
        aligned=common_calendar(stock,spy)
        self.assertEqual(len(aligned),21)
        self.assertEqual(relative_strength_20(aligned),21.0)

    def test_engine_fixture_parity(self):
        fixture=json.loads((ROOT/"config/ussy-swing-parity-v1.json").read_text())
        self.assertEqual(fixture["holding_period_days"],10)
        self.assertTrue(all(r["passed"] for r in parity_results(fixture)))


if __name__=="__main__": unittest.main()
