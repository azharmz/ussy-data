import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from audit_twelve_data import compare_rows, parse_symbols, parse_twelve_payload, row_issues


class TwelveDataAuditTests(unittest.TestCase):
    def test_symbol_validation(self):
        self.assertEqual(parse_symbols("spy,BHP"), ["SPY", "BHP"])
        for value in ("", "SPY,SPY", "SPY;BAD"):
            with self.assertRaises(ValueError):
                parse_symbols(value)

    def test_parse_and_qc(self):
        payload = {
            "meta": {"symbol": "SPY", "interval": "1day", "exchange": "NYSE"},
            "values": [{"datetime": "2026-09-03", "open": "100", "high": "103",
                        "low": "99", "close": "102", "volume": "1000"}],
            "status": "ok",
        }
        meta, rows = parse_twelve_payload(payload, "SPY")
        self.assertEqual(meta["symbol"], "SPY")
        self.assertEqual(rows[0]["close"], 102.0)
        self.assertEqual(row_issues(rows[0]), [])

    def test_rejects_provider_error_and_invalid_bar(self):
        with self.assertRaisesRegex(ValueError, "response error"):
            parse_twelve_payload({"status": "error", "code": 429}, "SPY")
        payload = {"meta": {"symbol": "SPY"}, "status": "ok", "values": [
            {"datetime": "2026-09-03", "open": "100", "high": "99", "low": "98",
             "close": "101", "volume": "1"}]}
        with self.assertRaisesRegex(ValueError, "QC failed"):
            parse_twelve_payload(payload, "SPY")

    def test_comparison_identifies_missing_yahoo_date(self):
        base = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000.0}
        twelve = [{"date": "2026-09-02", **base}, {"date": "2026-09-03", **base}]
        yahoo = [{"date": "2026-09-02", **base}]
        result = compare_rows(twelve, yahoo)
        self.assertEqual(result["dates_only_in_twelve"], ["2026-09-03"])
        self.assertEqual(result["common_date_count"], 1)

    def test_comparison_treats_incomplete_yahoo_bar_as_missing(self):
        base = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000.0}
        twelve = [{"date": "2026-09-03", **base}]
        yahoo = [{"date": "2026-09-03", **base, "close": float("nan"), "issues": ["nonfinite"]}]
        result = compare_rows(twelve, yahoo)
        self.assertEqual(result["dates_only_in_twelve"], ["2026-09-03"])
        self.assertEqual(result["yahoo_observed_dates_with_issues"], {"2026-09-03": ["nonfinite"]})
        self.assertIsNone(result["yahoo_latest_valid_date"])


if __name__ == "__main__":
    unittest.main()
