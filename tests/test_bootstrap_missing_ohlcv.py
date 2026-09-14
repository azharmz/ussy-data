import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bootstrap_missing_ohlcv import missing_members


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


if __name__ == "__main__":
    unittest.main()
