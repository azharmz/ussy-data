import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import security_lifecycle


class SecurityLifecycleTests(unittest.TestCase):
    def test_blocking_records_respect_effective_date_boundary(self):
        for security_id, record in security_lifecycle.records().items():
            if record.get("lifecycle_status") not in security_lifecycle.BLOCKING_STATUSES:
                continue
            effective = record.get("effective_date")
            if not effective:
                continue
            boundary = date.fromisoformat(effective)
            self.assertTrue(security_lifecycle.acquisition_allowed(security_id, boundary - timedelta(days=1)))
            self.assertFalse(security_lifecycle.acquisition_allowed(security_id, boundary))

    def test_blocking_records_without_effective_date_are_not_backdated(self):
        for security_id, record in security_lifecycle.records().items():
            if record.get("lifecycle_status") in security_lifecycle.BLOCKING_STATUSES and not record.get("effective_date"):
                self.assertTrue(security_lifecycle.acquisition_allowed(security_id, date(2026, 9, 19)))

    def test_review_required_is_not_silently_excluded(self):
        for security_id, record in security_lifecycle.records().items():
            if record.get("lifecycle_status") == "REVIEW_REQUIRED":
                self.assertTrue(security_lifecycle.acquisition_allowed(security_id, date(2026, 9, 19)))

    def test_unknown_is_not_silently_excluded(self):
        self.assertTrue(security_lifecycle.acquisition_allowed("UNKNOWN", date(2026, 9, 19)))


    def test_same_security_ticker_transitions_preserve_security_id(self):
        cases = [
            ("US37892C1062", "GGRP", "BTLN", date(2026, 8, 20)),
            ("US45769N1054", "ISSC", "IA", date(2026, 8, 18)),
            ("VGG9888Q1110", "YYGH", "YFOR", date(2026, 9, 2)),
        ]
        for security_id, old_ticker, new_ticker, boundary in cases:
            self.assertTrue(security_lifecycle.acquisition_allowed(security_id, boundary))
            self.assertEqual(security_lifecycle.acquisition_ticker(security_id, old_ticker, boundary - timedelta(days=1)), old_ticker)
            self.assertEqual(security_lifecycle.acquisition_ticker(security_id, old_ticker, boundary), new_ticker)

    def test_sep22_stale_terminal_ids_are_blocked(self):
        blocked = {
            "CA98942X1024", "IL0010828585", "US05350V1061", "US0554742090",
            "US2274831047", "US45828L1089", "US46658E1073", "US6793691089", "US87427V1035",
        }
        for security_id in blocked:
            self.assertFalse(security_lifecycle.acquisition_allowed(security_id, date(2026, 9, 22)))


if __name__ == "__main__":
    unittest.main()
