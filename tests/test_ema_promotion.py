import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ema_promotion import build_promoted_manifest
from load_ema_state import PROMOTION_POLICY


class EMAPromotionTests(unittest.TestCase):
    def candidate(self):
        return {
            "status": "CANDIDATE_NOT_PRODUCTION_APPROVED",
            "price_basis": "adj_close",
            "periods": [20, 50, 150, 200],
            "securities": 1,
            "security_ids": ["A"],
            "source_ready_parquet_key": "production/ready/runs/source.parquet",
            "source_ready_sha256": "abc",
            "source_ready_created_at": "2026-09-14T00:00:00+00:00",
            "update_method": "test",
            "bootstrap_count": 1,
            "recursive_count": 0,
            "unchanged_count": 0,
            "rebuild_count": 0,
            "as_of_date_min": "2026-09-11",
            "as_of_date_max": "2026-09-11",
            "candidate_manifest_key": "validation/indicators/ema/run-test.json",
        }

    def equivalence(self):
        return {"verified": 1, "numeric_failures": 0, "classification_mismatches": 0}

    def test_builds_approved_manifest_only_after_pass(self):
        result = build_promoted_manifest(self.candidate(), "production/indicators/ema/runs/run-test.parquet", "sha", self.equivalence())
        self.assertEqual(result["promotion_policy"], PROMOTION_POLICY)
        self.assertEqual(result["equivalence"]["numeric_failures"], 0)

    def test_rejects_failed_equivalence(self):
        eq = self.equivalence()
        eq["classification_mismatches"] = 1
        with self.assertRaises(ValueError):
            build_promoted_manifest(self.candidate(), "production/indicators/ema/runs/run-test.parquet", "sha", eq)

    def test_rejects_unapproved_candidate_status(self):
        candidate = self.candidate()
        candidate["status"] = "UNKNOWN"
        with self.assertRaises(ValueError):
            build_promoted_manifest(candidate, "production/indicators/ema/runs/run-test.parquet", "sha", self.equivalence())


if __name__ == '__main__':
    unittest.main()
