import csv
import copy
import json
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ohlcv_qc import issues, validate_frame
from repair_reported_bars import replace_bad_rows, load_cached, daily_targets, NEW_IDS


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.cached = json.loads((ROOT / 'audits/2026-09-01-cached-fetches.json').read_text())
        with (ROOT / 'audits/2026-09-01-invalid-bars.csv').open() as stream:
            self.targets = list(csv.DictReader(stream))

    def test_exactly_34_reused_and_three_new(self):
        before = copy.deepcopy(self.cached)
        reused = load_cached(self.cached, self.targets)
        self.assertEqual(len(reused), 34)
        self.assertEqual({r['security_id'] for r in self.targets} - set(reused), NEW_IDS)
        self.assertEqual(before, self.cached)

    def test_missing_cache_fails_without_fallback(self):
        self.cached['fetches'].pop()
        with self.assertRaises(ValueError): load_cached(self.cached, self.targets)

    def test_duplicate_rejected(self):
        self.cached['fetches'][-1] = self.cached['fetches'][0]
        with self.assertRaises(ValueError): load_cached(self.cached, self.targets)

    def test_wrong_date_or_price_rejected(self):
        for key, value in [('date', '2026-09-02 00:00:00'), ('high', 0), ('security_id', 'OTHER')]:
            cached = copy.deepcopy(self.cached)
            cached['fetches'][0]['replacement'][key] = value
            with self.assertRaises(ValueError): load_cached(cached, self.targets)

    def test_adjustment_parameters_rejected(self):
        self.cached['fetches'][0]['parameters']['auto_adjust'] = True
        with self.assertRaises(ValueError): load_cached(self.cached, self.targets)


class QCTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(open=10., high=12., low=9., close=11., adj_close=11., volume=100)

    def test_valid_and_no_mutation(self):
        original = self.row.copy()
        self.assertEqual(issues(self.row), [])
        self.assertEqual(original, self.row)

    def test_open_and_close_outside(self):
        for field, value in [('open', 13), ('open', 8), ('close', 13), ('close', 8)]:
            self.assertTrue(issues(dict(self.row, **{field: value})))

    def test_nonfinite_nonpositive_volume(self):
        for field, value in [('open', float('nan')), ('high', float('inf')), ('low', 0),
                             ('adj_close', -1), ('volume', -1), ('close', None)]:
            self.assertTrue(issues(dict(self.row, **{field: value})))

    def test_scope_and_all_reported_invalid(self):
        with (ROOT / 'audits/2026-09-01-invalid-bars.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 37)
        self.assertEqual(len({r['security_id'] for r in rows}), 37)
        self.assertEqual({r['date'] for r in rows}, {'2026-09-01'})
        self.assertTrue(all(issues(row) for row in rows))


@unittest.skipUnless(importlib.util.find_spec('pandas'), 'pandas available in GitHub workflow')
class FrameTests(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.pd = pd
        self.bad = dict(security_id='A', ticker='A', date='2026-09-01', open=13., high=12.,
                        low=9., close=11., adj_close=11., volume=100)
        self.good = dict(self.bad, high=13., volume=99)
        self.frame = pd.DataFrame([dict(self.good, date='2026-08-31'), self.bad,
                                   dict(self.good, security_id='B')])

    def test_only_one_row_and_id_changed(self):
        original = self.frame.copy(deep=True)
        result, changed = replace_bad_rows(self.frame, [self.bad], {'A': self.good})
        self.assertEqual(changed, ['A'])
        self.pd.testing.assert_frame_equal(original, self.frame)
        self.pd.testing.assert_frame_equal(result.iloc[[0, 2]], original.iloc[[0, 2]])
        validate_frame(result)
        self.assertEqual(len(result), len(original))

    def test_valid_existing_untouched_and_rerun(self):
        result, _ = replace_bad_rows(self.frame, [self.bad], {'A': self.good})
        again, changed = replace_bad_rows(result, [self.bad], {'A': dict(self.good, high=14)})
        self.assertEqual(changed, [])
        self.pd.testing.assert_frame_equal(result, again)

    def test_bad_source_rejected(self):
        with self.assertRaises(ValueError):
            replace_bad_rows(self.frame, [self.bad], {'A': self.bad})

    def test_changed_evidence_rejected(self):
        self.frame.loc[1, 'volume'] = 101
        with self.assertRaises(ValueError):
            replace_bad_rows(self.frame, [self.bad], {'A': self.good})

    def test_duplicate_or_missing_rejected(self):
        for frame in (self.frame.iloc[0:1], self.pd.concat([self.frame, self.frame.iloc[[1]]])):
            with self.assertRaises(ValueError):
                replace_bad_rows(frame, [self.bad], {'A': self.good})

    def test_full_frame_qc_rejects_bad_bar(self):
        with self.assertRaises(ValueError):
            validate_frame(self.frame)

    def test_export_qc_blocks_invalid_ready(self):
        from export_ready import filter_rolling
        readiness = dict(minimum_ready_bars=1, rolling_bars_target=300,
                         securities=[dict(security_id='A', rolling_bars=2, last_date='2026-09-01')])
        with self.assertRaisesRegex(ValueError, 'OHLCV QC'):
            filter_rolling(self.frame, readiness, {'A'})

    def test_daily_missing_target_not_inserted(self):
        targets = [self.bad, dict(self.bad, security_id='MISSING')]
        scoped = daily_targets(self.frame, targets)
        self.assertEqual(scoped, [self.bad])
        result, changed = replace_bad_rows(self.frame, scoped, {'A': self.good})
        self.assertEqual(len(result), len(self.frame))
        self.assertEqual(changed, ['A'])
