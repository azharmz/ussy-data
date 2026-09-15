import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bootstrap_missing_ohlcv import (
    load_deferred_policy,
    missing_members,
    partition_retryable_missing,
)


class MissingMemberOnboardingTests(unittest.TestCase):
    def test_selects_only_members_without_full_history(self):
        membership = [
            {"security_id": "A", "ticker": "AAA"},
            {"security_id": "B", "ticker": "BBB"},
            {"security_id": "C", "ticker": "CCC"},
        ]
        selected = missing_members(membership, {"A", "C"})
        self.assertEqual(selected, [{"security_id": "B", "ticker": "BBB"}])

    def test_preserves_membership_order(self):
        membership = [
            {"security_id": "C", "ticker": "CCC"},
            {"security_id": "A", "ticker": "AAA"},
            {"security_id": "B", "ticker": "BBB"},
        ]
        selected = missing_members(membership, {"A"})
        self.assertEqual([row["security_id"] for row in selected], ["C", "B"])

    def test_empty_when_all_histories_exist(self):
        membership = [
            {"security_id": "A", "ticker": "AAA"},
            {"security_id": "B", "ticker": "BBB"},
        ]
        self.assertEqual(missing_members(membership, {"A", "B"}), [])

    def test_partition_skips_reviewed_deferred_by_security_id(self):
        missing = [
            {"security_id": "OLD", "ticker": "OLDT"},
            {"security_id": "NEW", "ticker": "NEWT"},
        ]
        policy = {
            "OLD": {
                "security_id": "OLD",
                "ticker": "OLDT",
                "reason": "Acquisition completed",
                "event_date": "2026-01-01",
                "source": "https://example.invalid/source",
                "reviewed_at": "2026-09-02",
            }
        }
        retryable, deferred = partition_retryable_missing(missing, policy)
        self.assertEqual(retryable, [{"security_id": "NEW", "ticker": "NEWT"}])
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]["status"], "DEFERRED_NO_RETRY")
        self.assertEqual(deferred[0]["security_id"], "OLD")
        self.assertEqual(deferred[0]["reason"], "Acquisition completed")

    def test_ticker_reuse_does_not_defer_different_security(self):
        missing = [{"security_id": "NEW-ID", "ticker": "SAME"}]
        policy = {"OLD-ID": {"security_id": "OLD-ID", "ticker": "SAME", "reason": "Old security retired"}}
        retryable, deferred = partition_retryable_missing(missing, policy)
        self.assertEqual(retryable, missing)
        self.assertEqual(deferred, [])

    def test_load_deferred_policy_rejects_duplicate_security_id(self):
        payload = {
            "deferred": [
                {"security_id": "A", "ticker": "AAA"},
                {"security_id": "A", "ticker": "AAA"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_deferred_policy(path)


if __name__ == "__main__":
    unittest.main()
