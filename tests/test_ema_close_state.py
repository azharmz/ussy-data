import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ema_close_state import PERIODS, advance_state, bootstrap_state, classify_trend


class EmaCloseStateTests(unittest.TestCase):
    def frame(self, n=260):
        dates = pd.bdate_range("2025-01-01", periods=n)
        close = np.linspace(50.0, 100.0, n)
        return pd.DataFrame({
            "date": dates,
            "security_id": ["sec-1"] * n,
            "ticker": ["TEST"] * n,
            "close": close,
            "adj_close": close * 0.8,  # prove close basis is actually used
        })

    def test_bootstrap_matches_pandas_adjust_false_on_close(self):
        frame = self.frame()
        state = bootstrap_state(frame, "sec-1")
        for period in PERIODS:
            expected = frame["close"].ewm(span=period, adjust=False).mean().iloc[-1]
            self.assertAlmostEqual(state[f"ema{period}"], expected, places=12)
        self.assertAlmostEqual(state["last_price"], frame["close"].iloc[-1], places=12)

    def test_recursive_advance_matches_full_recompute(self):
        frame = self.frame(270)
        old = bootstrap_state(frame.iloc[:260], "sec-1")
        advanced, count = advance_state(old, frame.iloc[-30:])
        reference = bootstrap_state(frame, "sec-1")
        self.assertEqual(count, 10)
        for field in ["last_price", *(f"ema{p}" for p in PERIODS)]:
            self.assertAlmostEqual(advanced[field], reference[field], places=12)

    def test_classification_uses_close_state(self):
        row = {"last_price": 10, "ema20": 9, "ema50": 8, "ema150": 7, "ema200": 6}
        self.assertEqual(classify_trend(row), "STACKED_UP")


if __name__ == "__main__":
    unittest.main()
