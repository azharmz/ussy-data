import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from audit_tiingo import issues


class TiingoAuditTests(unittest.TestCase):
    def test_valid_eod_bar(self):
        row = {"open": 100, "high": 103, "low": 99, "close": 102, "volume": 1000}
        self.assertEqual(issues(row), [])

    def test_rejects_incomplete_or_invalid_eod_bar(self):
        self.assertEqual(issues({"open": 100}), ["missing_or_nonnumeric"])
        row = {"open": 100, "high": 99, "low": 98, "close": 101, "volume": 1}
        self.assertIn("high_below_range", issues(row))


if __name__ == "__main__":
    unittest.main()
