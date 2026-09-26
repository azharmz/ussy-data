import sys
import tempfile
import unittest
from pathlib import Path
import importlib.util
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from update_spy import validate, merge_incremental, audit_and_normalize_download, IDENTITY, POINTER


@unittest.skipUnless(importlib.util.find_spec('pandas'), 'pandas required')
class SPYTests(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.pd = pd
        self.old = pd.DataFrame([dict(date=pd.Timestamp(d), security_id=IDENTITY, ticker='SPY',
            open=100., high=102., low=99., close=101., adj_close=100., volume=1000)
            for d in ('2026-08-31', '2026-09-01')])

    def test_valid(self):
        validate(self.old)
        self.assertTrue(POINTER.startswith('benchmarks/'))

    def test_incremental_preserves_older_rows(self):
        new = self.old.iloc[[1]].copy()
        extra = new.copy()
        extra['date'] = self.pd.Timestamp('2026-09-02')
        result = merge_incremental(self.old, self.pd.concat([new, extra]))
        self.assertEqual(len(result), 3)
        self.pd.testing.assert_frame_equal(result.iloc[:2], self.old)

    def test_same_date_revision_is_not_ignored(self):
        new = self.old.iloc[[1]].copy()
        new['volume'] = 1001
        result = merge_incremental(self.old, new)
        self.assertEqual(result.iloc[-1].volume, 1001)

    def test_adjustment_revision_stops(self):
        new = self.old.iloc[[1]].copy()
        new['adj_close'] = 99.
        with self.assertRaisesRegex(ValueError, 'Adjustment basis'):
            merge_incremental(self.old, new)

    def test_uniform_dividend_adjustment_restatement_rebases_history(self):
        new = self.old.copy()
        new['adj_close'] = new['adj_close'] * 0.99
        result = merge_incremental(self.old, new)
        self.pd.testing.assert_series_equal(
            result['adj_close'].reset_index(drop=True),
            new['adj_close'].reset_index(drop=True),
            check_names=False,
        )

    def test_close_revision_stops(self):
        new = self.old.iloc[[1]].copy()
        new['close'] = 100.
        new['adj_close'] = 100. * 100. / 101.
        with self.assertRaisesRegex(ValueError, 'revised historical closes'):
            merge_incremental(self.old, new)

    def test_wrong_identity_or_invalid_ohlc_rejected(self):
        for col, value in [('ticker', 'OTHER'), ('high', 90.)]:
            frame = self.old.copy()
            frame[col] = value
            with self.assertRaises(ValueError): validate(frame)

    def test_no_overlap_or_duplicate_rejected(self):
        new = self.old.iloc[[1]].copy()
        new['date'] = self.pd.Timestamp('2026-09-03')
        with self.assertRaises(ValueError): merge_incremental(self.old, new)
        with self.assertRaises(ValueError): validate(self.pd.concat([self.old, self.old]))

    def test_incomplete_yahoo_row_is_audited_then_discarded(self):
        pd = self.pd
        raw = pd.DataFrame(
            {
                'Open': [100., 101., 102.],
                'High': [102., float('nan'), 104.],
                'Low': [99., 100., 101.],
                'Close': [101., 102., 103.],
                'Adj Close': [100., 101., 102.],
                'Volume': [1000, 1100, 1200],
            },
            index=pd.DatetimeIndex(['2026-09-08', '2026-09-09', '2026-09-10'], name='Date'),
        )
        with tempfile.TemporaryDirectory() as tmp:
            normalized, dropped, valid = audit_and_normalize_download(raw, Path(tmp))
            self.assertEqual(dropped, 1)
            self.assertEqual(valid, 2)
            self.assertEqual(len(normalized), 2)
            self.assertTrue((Path(tmp) / 'discarded-incomplete-source-rows.csv').exists())
