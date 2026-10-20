import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repair_specific_history_sessions import merge_history


class RepairSpecificHistorySessionsTest(unittest.TestCase):
    def frame(self, dates, closes):
        return pd.DataFrame({
            "date": pd.to_datetime(dates),
            "security_id": ["sid"] * len(dates),
            "ticker": ["DDS"] * len(dates),
            "open": [x - 1 for x in closes],
            "high": [x + 1 for x in closes],
            "low": [x - 2 for x in closes],
            "close": closes,
            "adj_close": closes,
            "volume": [100] * len(dates),
        })

    def test_adds_only_requested_dates_and_preserves_existing_rows(self):
        history = self.frame(["2026-10-09", "2026-10-14"], [10.0, 12.0])
        additions = self.frame(["2026-10-12", "2026-10-13"], [11.0, 11.5])
        merged = merge_history(history, additions, ["2026-10-12", "2026-10-13"])
        self.assertEqual(merged["date"].dt.strftime("%Y-%m-%d").tolist(),
                         ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"])
        self.assertEqual(merged["close"].tolist(), [10.0, 11.0, 11.5, 12.0])

    def test_rejects_duplicate_existing_dates(self):
        history = self.frame(["2026-10-09", "2026-10-09"], [10.0, 10.1])
        additions = self.frame(["2026-10-12", "2026-10-13"], [11.0, 11.5])
        with self.assertRaises(ValueError):
            merge_history(history, additions, ["2026-10-12", "2026-10-13"])

    def test_rejects_target_already_present(self):
        history = self.frame(["2026-10-12"], [10.0])
        additions = self.frame(["2026-10-12"], [11.0])
        with self.assertRaises(ValueError):
            merge_history(history, additions, ["2026-10-12"])

    def test_rejects_incomplete_target_set(self):
        history = self.frame(["2026-10-09"], [10.0])
        additions = self.frame(["2026-10-12"], [11.0])
        with self.assertRaises(ValueError):
            merge_history(history, additions, ["2026-10-12", "2026-10-13"])


if __name__ == "__main__":
    unittest.main()
