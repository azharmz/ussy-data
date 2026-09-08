import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from audit_tiingo import compare_rows, issues


class TiingoAuditTests(unittest.TestCase):
    def test_valid_eod_bar(self):
        row = {"open": 100, "high": 103, "low": 99, "close": 102, "volume": 1000}
        self.assertEqual(issues(row), [])

    def test_rejects_incomplete_or_invalid_eod_bar(self):
        self.assertEqual(issues({"open": 100}), ["missing_or_nonnumeric"])
        row = {"open": 100, "high": 99, "low": 98, "close": 101, "volume": 1}
        self.assertIn("high_below_range", issues(row))

    def test_comparison_records_raw_and_adjusted_deltas(self):
        values = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000.0}
        tiingo = [{"date": "2026-09-03", **values, "adj_close": 99.0}]
        yahoo = [{"date": "2026-09-03", **values, "close": 100.0, "adj_close": 98.0, "issues": []}]
        result = compare_rows(tiingo, yahoo)
        self.assertEqual(result["common_date_count"], 1)
        self.assertEqual(result["overlap"][0]["absolute_delta"]["close"], 1.0)
        self.assertEqual(result["overlap"][0]["adj_close_absolute_delta"], 1.0)


if __name__ == "__main__":
    unittest.main()
