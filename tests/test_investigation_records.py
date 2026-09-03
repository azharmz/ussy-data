import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from investigation_records import load_records, attach_investigations


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.records = load_records()
        self.record = self.records[0]
        self.row = {'security_id': self.record['security_id'], 'ticker': self.record['ticker'],
                    'category': 'DATA_UNAVAILABLE', 'reason': 'No history'}

    def test_dated_evidence_imported(self):
        self.assertEqual(len(self.records), 27)
        self.assertTrue(all(r['source_url'].startswith('https://') for r in self.records))

    def test_reviewed_is_not_resolved_and_preserves_original(self):
        original = copy.deepcopy(self.row)
        result = attach_investigations([self.row], self.records, '2026-08-28')[0]
        self.assertEqual(result['investigation']['status'], 'REVIEWED_FOLLOW_UP_REQUIRED')
        self.assertEqual(self.row, original)
        self.assertEqual(result['reason'], original['reason'])

    def test_market_review_does_not_verify_earnings(self):
        self.row['category'] = 'UNKNOWN'
        result = attach_investigations([self.row], self.records, '2026-08-28')[0]
        self.assertEqual(result['investigation']['status'], 'DETECTED_ONLY')

    def test_wrong_snapshot_or_identity_not_inherited(self):
        for key, value in [('ticker', 'OTHER'), ('security_id', 'OTHER')]:
            row = dict(self.row, **{key: value})
            self.assertEqual(attach_investigations([row], self.records, '2026-08-28')[0]['investigation']['status'], 'DETECTED_ONLY')
        self.assertEqual(attach_investigations([self.row], self.records, '2026-09-03')[0]['investigation']['status'], 'DETECTED_ONLY')

    def test_slai_no_fabricated_review(self):
        row = dict(security_id='US0554742090', ticker='SLAI', category='INSUFFICIENT_HISTORY')
        self.assertEqual(attach_investigations([row], self.records, '2026-08-28')[0]['investigation']['status'], 'DETECTED_ONLY')
