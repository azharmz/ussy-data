import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProductionWorkflowCheckpointingTests(unittest.TestCase):
    def test_full_pipeline_has_checkpointed_job_dependencies(self):
        text = (ROOT / ".github/workflows/production-daily.yml").read_text()
        self.assertIn("ohlcv:\n    needs: preflight", text)
        self.assertIn("ready:\n    needs: ohlcv", text)
        self.assertIn("ema:\n    needs: ready", text)
        self.assertIn("downstream:\n    needs: ema", text)

    def test_ema_patch_has_downstream_only_resume_workflow(self):
        text = (ROOT / ".github/workflows/production-ema-resume.yml").read_text()
        self.assertIn("src/verify_ema_equivalence.py", text)
        self.assertIn("ema:\n    needs: preflight", text)
        self.assertIn("downstream:\n    needs: ema", text)
        self.assertNotIn("run_production_finalized.py --snapshot-date", text)
        self.assertNotIn("bootstrap_missing_ohlcv.py --snapshot-date", text)


if __name__ == "__main__":
    unittest.main()
