import copy
import json
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bootstrap_policy import select_candidates


class PolicyQueueTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'snapshot_date': 'x', 'candidates': [{'security_id': 'A'}], 'deferred': [{'security_id': 'B'}]}
        self.members = {'snapshot_date': 'x', 'count': 2, 'records': [
            {'security_id': sid, 'ticker': sid, 'sharia_compliance': 'COMPLIANT', 'musaffaHalalRating': None}
            for sid in ('A', 'B')]}

    def test_secondary_ignored_and_deferred_not_selected(self):
        before = copy.deepcopy(self.members)
        selected, excluded = select_candidates(self.plan, self.members, 'x')
        self.assertEqual([r['security_id'] for r in selected], ['A'])
        self.assertEqual(excluded, [])
        self.assertEqual(self.members, before)

    def test_noncompliant_excluded(self):
        self.members['records'][0]['sharia_compliance'] = None
        self.assertEqual(select_candidates(self.plan, self.members, 'x')[0], [])

    def test_snapshot_change_rejected(self):
        with self.assertRaises(ValueError): select_candidates(self.plan, self.members, 'y')

    def test_overlap_rejected(self):
        self.plan['deferred'].append({'security_id': 'A'})
        with self.assertRaises(ValueError): select_candidates(self.plan, self.members, 'x')

    def test_reviewed_plan_counts(self):
        plan = json.loads((ROOT / 'config/bootstrap-policy-2026-08-28.json').read_text())
        self.assertEqual(len(plan['candidates']), 239)
        self.assertEqual(len(plan['deferred']), 23)
        ids = [r['security_id'] for r in plan['candidates'] + plan['deferred']]
        self.assertEqual(len(set(ids)), 262)
