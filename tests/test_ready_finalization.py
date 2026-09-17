import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from export_ready import finalized_ready_frame, terminal_date_summary
from us_market_finalization import finalized_through

NY = ZoneInfo("America/New_York")


class ReadyFinalizationTests(unittest.TestCase):
    def test_same_day_is_not_final_before_close_buffer(self):
        now = datetime(2026, 9, 16, 17, 0, tzinfo=NY)
        self.assertEqual(finalized_through(now), date(2026, 9, 15))

    def test_same_day_is_final_after_close_buffer(self):
        now = datetime(2026, 9, 16, 17, 31, tzinfo=NY)
        self.assertEqual(finalized_through(now), date(2026, 9, 16))

    def test_ready_uses_shared_finalized_through_cutoff(self):
        frame = pd.DataFrame({
            "security_id": ["A", "A", "B", "B"],
            "date": pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-15", "2026-09-16"]),
            "adj_close": [10.0, 11.0, 20.0, 21.0],
        })
        before = finalized_ready_frame(frame, cutoff=date(2026, 9, 15))
        self.assertEqual(set(before["date"].dt.date), {date(2026, 9, 15)})
        after = finalized_ready_frame(frame, cutoff=date(2026, 9, 16))
        self.assertEqual(set(after["date"].dt.date), {date(2026, 9, 15), date(2026, 9, 16)})
        summary = terminal_date_summary(after)
        self.assertEqual(summary["as_of_date"], "2026-09-16")
        self.assertEqual(summary["as_of_security_count"], 2)

    def test_future_dates_are_removed(self):
        frame = pd.DataFrame({
            "security_id": ["A", "A"],
            "date": pd.to_datetime(["2026-09-16", "2026-09-17"]),
        })
        result = finalized_ready_frame(frame, cutoff=date(2026, 9, 16))
        self.assertEqual(set(result["date"].dt.date), {date(2026, 9, 16)})


if __name__ == "__main__":
    unittest.main()
