import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from audit_alpaca_parity import parity_metrics


class AlpacaParityProbeTests(unittest.TestCase):
    def test_exact_adjusted_close_parity(self):
        canonical = pd.DataFrame({
            "ticker": ["A", "A"], "date": ["2026-09-18", "2026-09-21"],
            "adj_close": [100.0, 102.0],
        })
        alpaca = pd.DataFrame({
            "ticker": ["A", "A"], "date": ["2026-09-18", "2026-09-21"],
            "alpaca_adjusted_close": [100.0, 102.0],
        })
        out = parity_metrics(canonical, alpaca)
        self.assertEqual(out["overlap_rows"], 2)
        self.assertEqual(out["max_abs_pct_error"], 0.0)

    def test_no_overlap_is_explicit(self):
        canonical = pd.DataFrame({"ticker": ["A"], "date": ["2026-09-18"], "adj_close": [100.0]})
        alpaca = pd.DataFrame({"ticker": ["A"], "date": ["2026-09-19"], "alpaca_adjusted_close": [100.0]})
        out = parity_metrics(canonical, alpaca)
        self.assertEqual(out["overlap_rows"], 0)
        self.assertIsNone(out["median_abs_pct_error"])


if __name__ == "__main__":
    unittest.main()
