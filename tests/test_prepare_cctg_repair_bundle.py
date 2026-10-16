import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_cctg_repair_bundle import (
    CANONICAL_ADJ_CLOSE, RECOVERY_ADJ_CLOSE, SECURITY_ID, TARGET_DATE,
    correct_only_adjusted_close,
)


def histories(old=CANONICAL_ADJ_CLOSE, new=RECOVERY_ADJ_CLOSE):
    canonical = pd.DataFrame([
        {"date": "2026-09-21", "security_id": SECURITY_ID, "ticker": "CCTG", "adj_close": 0.52, "close": 0.52},
        {"date": "2026-09-22", "security_id": SECURITY_ID, "ticker": "CCTG", "adj_close": old, "close": 0.52},
        {"date": "2026-09-23", "security_id": SECURITY_ID, "ticker": "CCTG", "adj_close": 0.51, "close": 0.51},
    ])
    recovery = canonical.copy()
    recovery.loc[recovery["date"] == "2026-09-22", "adj_close"] = new
    return canonical, recovery


class CCTGRepairBundleTests(unittest.TestCase):
    def test_changes_only_diagnosed_adjusted_close(self):
        canonical, recovery = histories()
        corrected, change = correct_only_adjusted_close(canonical, recovery)
        row = corrected.loc[corrected["date"] == TARGET_DATE]
        self.assertEqual(len(row), 1)
        self.assertEqual(float(row.iloc[0]["adj_close"]), RECOVERY_ADJ_CLOSE)
        self.assertEqual(float(row.iloc[0]["close"]), 0.52)
        self.assertEqual(change["before"], CANONICAL_ADJ_CLOSE)
        self.assertEqual(change["after"], RECOVERY_ADJ_CLOSE)
        self.assertFalse(change["other_fields_changed"])

    def test_fails_closed_if_canonical_value_changed(self):
        canonical, recovery = histories(old=0.5181)
        with self.assertRaisesRegex(ValueError, "drifted"):
            correct_only_adjusted_close(canonical, recovery)

    def test_fails_closed_if_recovery_value_changed(self):
        canonical, recovery = histories(new=0.5183)
        with self.assertRaisesRegex(ValueError, "recovery adj_close"):
            correct_only_adjusted_close(canonical, recovery)

    def test_fails_closed_on_missing_or_duplicate_target_rows(self):
        canonical, recovery = histories()
        with self.assertRaisesRegex(ValueError, "Expected one"):
            correct_only_adjusted_close(canonical.iloc[:1], recovery)
        duplicated = pd.concat([recovery, recovery.iloc[[1]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Expected one"):
            correct_only_adjusted_close(canonical, duplicated)


if __name__ == "__main__":
    unittest.main()
