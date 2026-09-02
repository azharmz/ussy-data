import copy
import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_screening_freshness import build_report, classify


class FreshnessTests(unittest.TestCase):
    def classify(self, earnings, **extra):
        return classify({"next_earnings_date": earnings, **extra}, date(2026, 8, 28), date(2026, 9, 2))[0]

    def test_missing_and_invalid(self):
        for value in (None, "", "bad-date"):
            self.assertEqual(self.classify(value), "UNKNOWN")

    def test_future(self):
        self.assertEqual(self.classify("2026-09-03"), "AWAITING_NEXT_EARNINGS")

    def test_today_not_confirmed(self):
        self.assertEqual(self.classify("2026-09-02"), "UNKNOWN")

    def test_passed_since_snapshot(self):
        self.assertEqual(self.classify("2026-09-01"), "POTENTIALLY_STALE")

    def test_generic_updated_not_screening_proof(self):
        self.assertEqual(self.classify("2026-08-20", updated_at="2026-08-28"), "UNKNOWN")

    def test_updates_before_event(self):
        self.assertEqual(self.classify("2026-08-20", updated_at="2026-08-19"), "POTENTIALLY_STALE")

    def fixtures(self):
        records = [
            {"security_id": "A", "ticker": "A", "sharia_compliance": "COMPLIANT", "musaffaHalalRating": "COMPLIANT"},
            {"security_id": "B", "ticker": "B", "sharia_compliance": "COMPLIANT", "musaffaHalalRating": "DOUBTFUL"},
        ]
        membership = {"snapshot_date": "2026-08-28", "count": 2, "records": records}
        master = {"snapshot_date": "2026-08-28", "count": 1, "records": [{"security_id": "A", "next_earnings_date": "2026-09-01"}]}
        return membership, master

    def test_scope_and_no_mutation(self):
        membership, master = self.fixtures()
        original = copy.deepcopy(membership)
        report = build_report(membership, master, "2026-08-28", datetime(2026, 9, 2, tzinfo=UTC))
        self.assertEqual(report["securities_audited"], 1)
        self.assertEqual(report["potentially_stale"], 1)
        self.assertEqual(report["post_earnings_refreshed"], 0)
        self.assertIsNone(report["records"][0]["screening_updated_at"])
        self.assertEqual(membership, original)

    def test_missing_master_is_unknown(self):
        membership, master = self.fixtures()
        master.update(count=0, records=[])
        report = build_report(membership, master, "2026-08-28", datetime(2026, 9, 2, tzinfo=UTC))
        self.assertEqual(report["unknown"], 1)

    def test_snapshot_mismatch_rejected(self):
        membership, master = self.fixtures()
        master["snapshot_date"] = "2026-08-27"
        with self.assertRaises(ValueError):
            build_report(membership, master, "2026-08-28")


if __name__ == "__main__":
    unittest.main()
