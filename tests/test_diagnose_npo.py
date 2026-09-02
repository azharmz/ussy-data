import ast
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from diagnose_npo import differences, qc, read_evidence


class DiagnosticTests(unittest.TestCase):
    def test_bad_open_preserved(self):
        row = dict(open=298.739990234375, high=293.8699951171875, low=287.2300109863281,
                   close=287.9599914550781, adj_close=287.9599914550781, volume=113344)
        original = row.copy()
        self.assertEqual(qc(row), ['open_above_high'])
        self.assertEqual(row, original)
        self.assertTrue(all(v == 0 for v in differences(row, row).values()))

    def test_low_violation(self):
        self.assertEqual(qc(dict(open=1, high=3, low=2, close=2.5, adj_close=2.5, volume=1)), ['open_below_low'])

    def test_capture_keeps_exact_bytes(self):
        s3 = Mock()
        s3.get_object.return_value = {'Body': io.BytesIO(b'unchanged evidence'), 'ETag': 'tag'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'evidence'
            body, metadata = read_evidence(s3, 'bucket', 'key', path)
            self.assertEqual(path.read_bytes(), b'unchanged evidence')
            self.assertEqual(metadata['bytes'], len(body))
        self.assertEqual([c[0] for c in s3.method_calls], ['get_object'])

    def test_r2_only_read_calls(self):
        path = Path(__file__).resolve().parents[1] / 'src/diagnose_npo.py'
        tree = ast.parse(path.read_text())
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == 's3']
        self.assertEqual(set(calls), {'get_object', 'head_object'})
