import io
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ema_state import bootstrap_state
from publish_ema_state import build_state


def frame(sid: str, ticker: str, prices, start="2020-01-01"):
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=len(prices)),
        "security_id": sid,
        "ticker": ticker,
        "adj_close": np.asarray(prices, dtype="float64"),
    })


class FakeS3:
    def __init__(self, histories):
        self.objects = {}
        for sid, history in histories.items():
            buf = io.BytesIO()
            history.to_parquet(buf, engine="pyarrow", index=False)
            self.objects[f"backtest/ohlcv/{sid}.parquet"] = buf.getvalue()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


class PublishEMAStateTests(unittest.TestCase):
    def test_membership_change_rebases_all_from_long_history(self):
        a = frame("A", "AAA", 50 + np.linspace(0, 20, 300))
        b = frame("B", "BBB", 30 + np.linspace(0, 10, 300))
        ready = pd.concat([a.iloc[-250:], b.iloc[-250:]], ignore_index=True)

        # Simulate an old persisted A state whose EMA seed came from a slightly
        # different historical adjusted-price view while its latest price/date
        # still agree with current Ready.  B is a new universe member.
        old_a = a.copy()
        old_a.loc[old_a.index[:-1], "adj_close"] += 1e-5
        prior = pd.DataFrame([bootstrap_state(old_a, "A")])

        state, counters = build_state(FakeS3({"A": a, "B": b}), "bucket", ready, prior)
        by_id = state.set_index("security_id")
        reference_a = bootstrap_state(a, "A")

        self.assertEqual(counters["bootstrap"], 2)
        self.assertEqual(counters["recursive"], 0)
        self.assertEqual(counters["unchanged"], 0)
        self.assertEqual(counters["rebuild"], 0)
        for field in ["last_price", "ema20", "ema50", "ema150", "ema200"]:
            self.assertEqual(float(by_id.loc["A", field]), float(reference_a[field]))

    def test_unchanged_membership_keeps_recursive_path(self):
        a = frame("A", "AAA", 50 + np.linspace(0, 20, 300))
        prior = pd.DataFrame([bootstrap_state(a.iloc[:299], "A")])
        ready = a.iloc[-250:].copy()

        state, counters = build_state(FakeS3({"A": a}), "bucket", ready, prior)
        reference = bootstrap_state(a, "A")

        self.assertEqual(counters["bootstrap"], 0)
        self.assertEqual(counters["recursive"], 1)
        for field in ["last_price", "ema20", "ema50", "ema150", "ema200"]:
            self.assertTrue(np.isclose(state.iloc[0][field], reference[field], rtol=1e-12, atol=1e-12))


if __name__ == "__main__":
    unittest.main()
