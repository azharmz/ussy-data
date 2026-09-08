import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_prebacktest_invalid_bars import TARGETS, compare, target_row


class PrebacktestInvalidBarAuditTests(unittest.TestCase):
    def test_target_row_requires_expected_qc_failure(self):
        target = TARGETS[0]
        rows = [{"security_id": target["security_id"], "ticker": target["ticker"], "date": "2020-01-01",
                 "open": 2, "high": 3, "low": 4, "close": 2, "adj_close": 2, "volume": 1}] * (target["position"] + 1)
        result = target_row(pd.DataFrame(rows), target)
        self.assertEqual(result["low"], 4)

    def test_compare_is_provider_minus_stored(self):
        stored = dict(open=1, high=2, low=1, close=2, adj_close=2, volume=3)
        provider = dict(open=2, high=3, low=2, close=3, adj_close=3, volume=4)
        self.assertEqual(compare(stored, provider), {key: 1.0 for key in stored})


if __name__ == "__main__":
    unittest.main()
