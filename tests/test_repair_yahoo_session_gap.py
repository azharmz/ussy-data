import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repair_yahoo_session_gap import normalize_target_session, raw_target_session


class RepairYahooSessionGapTest(unittest.TestCase):
    def frame(self, index):
        return pd.DataFrame(
            {"Open":[10.0],"High":[11.0],"Low":[9.0],"Close":[10.5],"Adj Close":[10.5],"Volume":[100]},
            index=pd.DatetimeIndex([index], name="Date"),
        )

    def test_selects_raw_exchange_session_before_normalization(self):
        target=pd.Timestamp("2026-09-22")
        selected=raw_target_session(self.frame("2026-09-22"),target)
        self.assertEqual(len(selected),1)

    def test_preserves_target_date_even_when_utc_conversion_would_shift(self):
        target=pd.Timestamp("2026-09-22")
        raw=self.frame(pd.Timestamp("2026-09-22 00:00:00",tz="Pacific/Kiritimati"))
        row=normalize_target_session(raw,"sid","TEST",target)
        self.assertEqual(row.iloc[0]["date"],target)

    def test_rejects_non_target_raw_session(self):
        target=pd.Timestamp("2026-09-22")
        row=normalize_target_session(self.frame("2026-09-23"),"sid","TEST",target)
        self.assertTrue(row.empty)


if __name__=="__main__":
    unittest.main()
