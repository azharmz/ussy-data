import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTIVE_HISTORY_FILES = [
    "src/bootstrap_ohlcv.py",
    "src/bootstrap_missing_ohlcv.py",
    "src/update_production.py",
    "src/repair_ohlcv.py",
    "src/repair_partial_daily_bar.py",
]


class HistoryNamespaceContractTests(unittest.TestCase):
    def test_active_history_code_uses_canonical_namespace(self):
        for relative in ACTIVE_HISTORY_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotIn("backtest/ohlcv/", text)
                self.assertNotIn("backtest/manifests/", text)

    def test_canonical_constants_are_history_namespaced(self):
        text = (ROOT / "src/bootstrap_ohlcv.py").read_text(encoding="utf-8")
        self.assertIn('HISTORY_PREFIX = "history/ohlcv/"', text)
        self.assertIn('HISTORY_MANIFEST_PREFIX = "history/manifests/"', text)


if __name__ == "__main__":
    unittest.main()
