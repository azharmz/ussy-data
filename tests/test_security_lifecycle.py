import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import security_lifecycle

class SecurityLifecycleTests(unittest.TestCase):
    def test_terminated_boundary(self):
        self.assertTrue(security_lifecycle.acquisition_allowed("US89055F1030", date(2026, 6, 30)))
        self.assertFalse(security_lifecycle.acquisition_allowed("US89055F1030", date(2026, 7, 1)))

    def test_nontradable_boundary(self):
        self.assertTrue(security_lifecycle.acquisition_allowed("KYG3041J1067", date(2025, 10, 22)))
        self.assertFalse(security_lifecycle.acquisition_allowed("KYG3041J1067", date(2025, 10, 23)))

    def test_four_reviewed_cases_excluded_now(self):
        for security_id in security_lifecycle.EXCLUDED:
            self.assertFalse(security_lifecycle.acquisition_allowed(security_id, date(2026, 9, 18)))

    def test_unknown_is_not_silently_excluded(self):
        self.assertTrue(security_lifecycle.acquisition_allowed("UNKNOWN", date(2026, 9, 18)))

if __name__ == "__main__":
    unittest.main()
