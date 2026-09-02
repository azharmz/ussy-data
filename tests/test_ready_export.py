import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from export_ready import select_ready_ids


class ReadySelectionTests(unittest.TestCase):
    def setUp(self):
        self.members = {'snapshot_date': '2026-08-28', 'count': 3, 'records': [
            {'security_id': sid, 'sharia_compliance': 'COMPLIANT', 'musaffaHalalRating': 'COMPLIANT'}
            for sid in ['A', 'B', 'C']]}
        self.ready = {'snapshot_date': '2026-08-28', 'confirmed_compliant': 3, 'ready': 1, 'ready_security_ids': ['A'],
                      'insufficient_history_security_ids': ['B'], 'data_unavailable_security_ids': ['C']}

    def select(self):
        return select_ready_ids(self.ready, self.members, '2026-08-28')

    def test_excludes_others_without_mutation(self):
        before = copy.deepcopy((self.members, self.ready))
        self.assertEqual(self.select(), {'A'})
        self.assertEqual((self.members, self.ready), before)

    def test_dynamic_ready_count(self):
        self.ready.update(ready=2, ready_security_ids=['A', 'B'], insufficient_history_security_ids=[])
        self.assertEqual(self.select(), {'A', 'B'})

    def test_snapshot_mismatch(self):
        self.ready['snapshot_date'] = '2026-08-27'
        with self.assertRaises(ValueError): self.select()

    def test_noncompliant_rejected(self):
        self.members['records'][0]['sharia_compliance'] = 'DOUBTFUL'
        with self.assertRaises(ValueError): self.select()

    def test_duplicates_rejected(self):
        self.ready.update(ready=2, ready_security_ids=['A', 'A'])
        with self.assertRaises(ValueError): self.select()

    def test_secondary_rating_ignored(self):
        for value in ('NON_COMPLIANT', 'QUESTIONABLE', None, ''):
            self.members['records'][0]['musaffaHalalRating'] = value
            self.assertEqual(self.select(), {'A'})

    def test_stale_policy_readiness_rejected(self):
        self.ready['confirmed_compliant'] = 2
        with self.assertRaises(ValueError): self.select()

    def test_overlap_rejected(self):
        self.ready['data_unavailable_security_ids'].append('A')
        with self.assertRaises(ValueError): self.select()

    def test_count_mismatch(self):
        self.ready['ready'] = 1024
        with self.assertRaises(ValueError): self.select()


if __name__ == '__main__':
    unittest.main()
