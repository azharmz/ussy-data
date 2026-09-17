import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from export_ready import finalized_ready_frame, terminal_date_summary


class ReadyFinalizationTests(unittest.TestCase):
    def test_current_ny_date_is_excluded_even_if_already_persisted(self):
        frame = pd.DataFrame({
            "security_id": ["A", "A", "B", "B"],
            "date": pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-15", "2026-09-16"]),
            "adj_close": [10.0, 11.0, 20.0, 21.0],
        })
        result = finalized_ready_frame(frame, date(2026, 9, 16))
        self.assertEqual(set(result["date"].dt.date), {date(2026, 9, 15)})
        summary = terminal_date_summary(result)
        self.assertEqual(summary["as_of_date"], "2026-09-15")
        self.assertEqual(summary["as_of_security_count"], 2)

    def test_prior_dates_are_not_removed(self):
        frame = pd.DataFrame({
            "security_id": ["A", "B"],
            "date": pd.to_datetime(["2026-09-15", "2026-09-14"]),
        })
        result = finalized_ready_frame(frame, date(2026, 9, 16))
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
