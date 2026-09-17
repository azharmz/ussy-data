import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from export_ready import terminal_date_summary


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


if __name__ == '__main__':
    unittest.main()
