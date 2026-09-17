import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corporate_action_identity import (
    ProviderAlias,
    resolve_historical_identity,
    validate_alias_registry,
)


class CorporateActionIdentityTests(unittest.TestCase):
    def test_evtv_azio_lifecycle(self):
        sid = "US29414V3087"
        self.assertEqual(resolve_historical_identity("tiingo_eod", "EVTV", date(2026, 7, 12)), sid)
        self.assertEqual(resolve_historical_identity("tiingo_eod", "AZIO", date(2026, 7, 13)), sid)

    def test_alias_is_date_bounded(self):
        with self.assertRaises(ValueError):
            resolve_historical_identity("tiingo_eod", "EVTV", date(2026, 7, 13))
        with self.assertRaises(ValueError):
            resolve_historical_identity("tiingo_eod", "AZIO", date(2026, 7, 12))

    def test_unknown_ticker_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_historical_identity("tiingo_eod", "UNKNOWN", date(2026, 7, 13))

    def test_overlapping_reused_ticker_fails_registry_validation(self):
        aliases = (
            ProviderAlias("SEC-A", "tiingo_eod", "REUSE", date(2020, 1, 1), date(2021, 12, 31), "reviewed-a"),
            ProviderAlias("SEC-B", "tiingo_eod", "REUSE", date(2021, 1, 1), date(2022, 12, 31), "reviewed-b"),
        )
        with self.assertRaises(ValueError):
            validate_alias_registry(aliases)

    def test_non_overlapping_reuse_is_allowed(self):
        aliases = (
            ProviderAlias("SEC-A", "tiingo_eod", "REUSE", None, date(2020, 12, 31), "reviewed-a"),
            ProviderAlias("SEC-B", "tiingo_eod", "REUSE", date(2021, 1, 1), None, "reviewed-b"),
        )
        validate_alias_registry(aliases)
        self.assertEqual(resolve_historical_identity("tiingo_eod", "REUSE", date(2020, 12, 31), aliases), "SEC-A")
        self.assertEqual(resolve_historical_identity("tiingo_eod", "REUSE", date(2021, 1, 1), aliases), "SEC-B")


if __name__ == "__main__":
    unittest.main()
