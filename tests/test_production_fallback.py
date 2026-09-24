import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.modules.setdefault("yfinance", MagicMock())
sys.modules.setdefault("boto3", MagicMock())
botocore = sys.modules.setdefault("botocore", types.ModuleType("botocore"))
botocore_config = types.ModuleType("botocore.config")
botocore_config.Config = MagicMock
botocore_exceptions = types.ModuleType("botocore.exceptions")
botocore_exceptions.ClientError = Exception
sys.modules.setdefault("botocore.config", botocore_config)
sys.modules.setdefault("botocore.exceptions", botocore_exceptions)

import update_production


class ProductionFallbackTests(unittest.TestCase):
    def setUp(self):
        self.batch = pd.DataFrame({"source": ["batch"]})
        self.individual = pd.DataFrame({"source": ["individual"]})
        self.normalized = pd.DataFrame({"date": [pd.Timestamp("2026-09-03")]})
        self.last_date = pd.Timestamp("2026-09-01")

    @patch.object(update_production, "download_since")
    @patch.object(update_production, "normalize_history")
    def test_valid_batch_does_not_fetch_individually(self, normalize, download):
        normalize.return_value = self.normalized

        result = update_production.normalize_with_individual_fallback(
            self.batch, "BHP", self.last_date, 3, "AU000000BHP4", "BHP"
        )

        self.assertIs(result, self.normalized)
        download.assert_not_called()

    @patch.object(update_production, "download_since")
    @patch.object(update_production, "normalize_history")
    def test_qc_failure_retries_individually(self, normalize, download):
        normalize.side_effect = [ValueError("OHLCV QC rejected 1 bars"), self.normalized]
        download.return_value = self.individual

        result = update_production.normalize_with_individual_fallback(
            self.batch, "BHP", self.last_date, 3, "AU000000BHP4", "BHP"
        )

        self.assertIs(result, self.normalized)
        download.assert_called_once_with("BHP", self.last_date, 3)
        self.assertEqual(normalize.call_args_list[1].args[0].iloc[0]["source"], "individual")

    @patch.object(update_production, "download_since")
    @patch.object(update_production, "normalize_history")
    def test_both_qc_failures_raise_without_accepting_data(self, normalize, download):
        normalize.side_effect = [ValueError("batch invalid"), ValueError("individual invalid")]
        download.return_value = self.individual

        with self.assertRaisesRegex(RuntimeError, "batch and individual"):
            update_production.normalize_with_individual_fallback(
                self.batch, "BHP", self.last_date, 3, "AU000000BHP4", "BHP"
            )

    @patch.object(update_production, "download_since")
    @patch.object(update_production, "normalize_history")
    def test_empty_batch_preserves_existing_empty_retry_behavior(self, normalize, download):
        download.return_value = pd.DataFrame()

        result = update_production.normalize_with_individual_fallback(
            pd.DataFrame(), "BHP", self.last_date, 3, "AU000000BHP4", "BHP"
        )

        self.assertTrue(result.empty)
        normalize.assert_not_called()

    def test_merge_downloaded_history_repairs_internal_gap(self):
        cols=update_production.OHLCV_COLUMNS
        def row(date, close):
            return {"date":pd.Timestamp(date),"security_id":"S","ticker":"X","open":close,"high":close,"low":close,"close":close,"adj_close":close,"volume":100}
        historical=pd.DataFrame([row("2026-09-21",10),row("2026-09-23",12)],columns=cols)
        downloaded=pd.DataFrame([row("2026-09-21",10),row("2026-09-22",11),row("2026-09-23",12)],columns=cols)
        merged,additions,backfills=update_production.merge_downloaded_history(historical,downloaded,pd.Timestamp("2026-09-23"))
        self.assertTrue(additions.empty)
        self.assertEqual(backfills["date"].dt.strftime("%Y-%m-%d").tolist(),["2026-09-22"])
        self.assertEqual(merged["date"].dt.strftime("%Y-%m-%d").tolist(),["2026-09-21","2026-09-22","2026-09-23"])


if __name__ == "__main__":
    unittest.main()
