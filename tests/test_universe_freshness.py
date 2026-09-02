import ast
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from audit_universe_freshness import classify


class UniverseAuditTests(unittest.TestCase):
    def row(self, **changes):
        row = dict(history_status='available', history_rows=300, history_last_date='2026-09-01',
                   rolling_last_date='2026-09-01', ready_last_date='2026-09-01',
                   in_ready=True, declared_ready=True)
        row.update(changes)
        return row

    def test_matching_dates(self):
        self.assertEqual(classify(self.row(), '2026-09-01', 250), [])

    def test_lag_is_not_confirmed_calendar_failure(self):
        flags = classify(self.row(history_last_date='2026-08-28'), '2026-09-01', 250)
        self.assertIn('behind_exchange_peers_calendar_unverified', flags)

    def test_short_history_still_checked_for_lag(self):
        flags = classify(self.row(history_rows=100, history_last_date='2026-08-28', in_ready=False, declared_ready=False), '2026-09-01', 250)
        self.assertIn('insufficient_history', flags)
        self.assertIn('behind_exchange_peers_calendar_unverified', flags)

    def test_missing_and_unreadable_distinct(self):
        for status in ('missing_history', 'history_read_error'):
            self.assertIn(status, classify(self.row(history_status=status), None, 250))

    def test_readiness_mismatch(self):
        self.assertIn('readiness_export_membership_mismatch', classify(self.row(declared_ready=False), None, 250))

    def test_no_yahoo_or_r2_mutations(self):
        tree = ast.parse((ROOT / 'src/audit_universe_freshness.py').read_text())
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == 's3'}
        self.assertEqual(calls, {'get_object', 'head_object', 'get_paginator'})
        self.assertNotIn('yfinance', ast.unparse(tree))
