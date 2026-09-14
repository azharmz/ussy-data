import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ema_state import PERIODS, StateNeedsRebuild, advance_state, bootstrap_state, classify_trend, validate_state_frame


def frame(prices, start="2020-01-01", sid="A", ticker="AAA"):
    dates = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame({"date": dates, "security_id": sid, "ticker": ticker, "adj_close": prices})


class EMAStateTests(unittest.TestCase):
    def test_bootstrap_matches_pandas_adjust_false(self):
        prices = 50 + np.linspace(0, 30, 500) + np.sin(np.arange(500) / 7)
        source = frame(prices)
        state = bootstrap_state(source)
        series = pd.Series(prices, dtype="float64")
        for period in PERIODS:
            expected = series.ewm(span=period, adjust=False).mean().iloc[-1]
            self.assertAlmostEqual(state[f"ema{period}"], expected, places=12)

    def test_recursive_matches_long_history_and_classification(self):
        prices = 100 + np.linspace(0, 40, 650) + 2 * np.sin(np.arange(650) / 9)
        source = frame(prices)
        prior = bootstrap_state(source.iloc[:500])
        ready = source.iloc[350:].copy()
        advanced, count = advance_state(prior, ready)
        reference = bootstrap_state(source)
        self.assertEqual(count, 150)
        for field in ["last_price", *(f"ema{p}" for p in PERIODS)]:
            self.assertTrue(np.isclose(advanced[field], reference[field], rtol=1e-12, atol=1e-12), field)
        self.assertEqual(classify_trend(advanced), classify_trend(reference))

    def test_missing_prior_date_requires_rebuild(self):
        source = frame(np.linspace(10, 20, 500))
        prior = bootstrap_state(source.iloc[:200])
        with self.assertRaises(StateNeedsRebuild):
            advance_state(prior, source.iloc[250:])

    def test_changed_prior_price_requires_rebuild(self):
        source = frame(np.linspace(10, 20, 500))
        prior = bootstrap_state(source.iloc[:300])
        prior["last_price"] *= 1.01
        with self.assertRaises(StateNeedsRebuild):
            advance_state(prior, source.iloc[150:])

    def test_requires_200_bootstrap_bars(self):
        with self.assertRaises(ValueError):
            bootstrap_state(frame(np.linspace(10, 20, 199)))

    def test_state_validation_rejects_duplicates(self):
        source = frame(np.linspace(10, 20, 250))
        row = bootstrap_state(source)
        with self.assertRaises(ValueError):
            validate_state_frame(pd.DataFrame([row, row]))


if __name__ == '__main__':
    unittest.main()
