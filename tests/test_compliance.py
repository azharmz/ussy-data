import ast
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from compliance import is_eligible


class ComplianceTests(unittest.TestCase):
    def test_primary_only_and_no_mutation(self):
        for secondary in ('COMPLIANT', 'NON_COMPLIANT', 'QUESTIONABLE', '', None):
            row = {'sharia_compliance': 'COMPLIANT', 'musaffaHalalRating': secondary}
            before = copy.deepcopy(row)
            self.assertTrue(is_eligible(row))
            self.assertEqual(row, before)
        self.assertTrue(is_eligible({'sharia_compliance': 'COMPLIANT'}))

    def test_primary_fail_closed(self):
        for primary in ('NON_COMPLIANT', 'QUESTIONABLE', '', None, 'compliant'):
            self.assertFalse(is_eligible({'sharia_compliance': primary, 'musaffaHalalRating': 'COMPLIANT'}))
        self.assertFalse(is_eligible({}))

    def test_no_secondary_comparisons_in_pipeline(self):
        for path in (ROOT / 'src').glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    self.assertNotIn('musaffaHalalRating', ast.unparse(node), str(path))
