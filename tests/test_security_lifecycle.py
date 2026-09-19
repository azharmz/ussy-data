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


if __name__ == "__main__":
    unittest.main()
