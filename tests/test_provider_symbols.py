import ast
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from provider_symbols import yahoo_symbol, YAHOO_ALIASES


class ProviderSymbolTests(unittest.TestCase):
    def test_reviewed_aliases(self):
        self.assertEqual(len(YAHOO_ALIASES), 5)
        for (sid, ticker), target in YAHOO_ALIASES.items():
            self.assertEqual(yahoo_symbol(ticker, sid), target)
            self.assertEqual(yahoo_symbol(target, sid), target)

    def test_wrong_id_does_not_remap(self):
        self.assertEqual(yahoo_symbol('MBGL WI', 'US60744M1062'), 'MBGL')
        self.assertEqual(yahoo_symbol('MBGL WI', 'wrong-id'), 'MBGL WI')
        self.assertEqual(yahoo_symbol('PSTG', 'different-security'), 'PSTG')
        self.assertEqual(yahoo_symbol('PSTG'), 'PSTG')

    def test_mergers_and_ambiguous_id_not_aliased(self):
        self.assertEqual(yahoo_symbol('ESGL', 'KYG3R95P1087'), 'ESGL')
        self.assertEqual(yahoo_symbol('SGN', 'US82670R3057'), 'SGN')
        self.assertEqual(yahoo_symbol('CYBR', 'IL0011334468'), 'CYBR')

    def test_class_share_normalization(self):
        self.assertEqual(yahoo_symbol(' brk.b ', 'unknown'), 'BRK-B')

    def test_all_pipeline_calls_pass_identity(self):
        count = 0
        for path in (ROOT / 'src').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'yahoo_symbol':
                    self.assertEqual(len(node.args), 2, str(path))
                    count += 1
        self.assertEqual(count, 7)  # Includes repair and pre-backtest evidence paths.
