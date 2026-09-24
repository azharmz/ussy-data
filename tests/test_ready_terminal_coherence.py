import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from export_ready import terminal_date_summary, recent_session_gap_summary, enforce_recent_session_continuity, continuity_predecessor


class ReadyTerminalCoherenceTests(unittest.TestCase):
    def test_coherent_latest_mode_passes_with_older_stale_minority(self):
        frame = pd.DataFrame({
            'security_id': ['A', 'A', 'B', 'B', 'C'],
            'date': pd.to_datetime(['2026-09-15', '2026-09-16', '2026-09-15', '2026-09-16', '2026-09-03']),
        })
        summary = terminal_date_summary(frame)
        self.assertEqual(summary['as_of_date'], '2026-09-16')
        self.assertEqual(summary['as_of_security_count'], 2)

    def test_partial_newer_date_fails_closed(self):
        frame = pd.DataFrame({
            'security_id': ['A', 'B', 'C'],
            'date': pd.to_datetime(['2026-09-16', '2026-09-15', '2026-09-15']),
        })
        with self.assertRaisesRegex(RuntimeError, 'partial leading-edge'):
            terminal_date_summary(frame)

    def test_catchup_gap_is_detected_without_inventing_weekend_sessions(self):
        frame = pd.DataFrame({
            'security_id': ['A', 'A', 'A', 'B', 'B', 'B', 'B'],
            'date': pd.to_datetime([
                '2026-09-21', '2026-09-22', '2026-09-23',
                '2026-09-21', '2026-09-23', '2026-09-22', '2026-09-23',
            ]),
        })
        # Remove B's 22 row while keeping the cross-section's observed 22 session.
        frame = frame.drop(index=5).reset_index(drop=True)
        summary = recent_session_gap_summary(frame, '2026-09-21', '2026-09-23')
        self.assertEqual(summary['expected_sessions'], ['2026-09-22', '2026-09-23'])
        self.assertEqual(summary['gap_security_count'], 1)
        self.assertEqual(summary['gaps']['B'], ['2026-09-22'])
        with self.assertRaisesRegex(RuntimeError, 'continuity rejected'):
            enforce_recent_session_continuity(frame, '2026-09-21', '2026-09-23')

    def test_stale_security_does_not_create_false_internal_gap(self):
        frame = pd.DataFrame({
            'security_id': ['A', 'A', 'A', 'B'],
            'date': pd.to_datetime(['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-21']),
        })
        summary = recent_session_gap_summary(frame, '2026-09-21', '2026-09-23')
        self.assertEqual(summary['gap_security_count'], 0)

    def test_quarantined_current_uses_last_known_good_predecessor(self):
        import json, tempfile
        current={"as_of_date":"2026-09-23","sha256":"bad"}
        payload={"quarantined_ready":[{"ready_as_of_date":"2026-09-23","ready_sha256":"bad","last_known_good_as_of_date":"2026-09-21"}]}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"q.json"
            path.write_text(json.dumps(payload))
            self.assertEqual(continuity_predecessor(current,path),"2026-09-21")


if __name__ == '__main__':
    unittest.main()
