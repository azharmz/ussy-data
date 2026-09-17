import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductionConcurrencyContractTests(unittest.TestCase):
    def test_daily_and_ema_resume_share_serialization_group(self):
        for relative in (
            ".github/workflows/production-daily.yml",
            ".github/workflows/production-ema-resume.yml",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("concurrency:\n  group: production-daily\n  cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
