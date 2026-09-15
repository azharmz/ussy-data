import unittest

from src.r2_storage_guard import GIB, classify


class StorageGuardTests(unittest.TestCase):
    def test_safe_below_warning(self):
        self.assertEqual(classify(int(6.99 * GIB), 7.0, 9.0), "SAFE")

    def test_warning_at_warning_threshold(self):
        self.assertEqual(classify(7 * GIB, 7.0, 9.0), "WARNING")

    def test_hard_stop_at_hard_threshold(self):
        self.assertEqual(classify(9 * GIB, 7.0, 9.0), "HARD_STOP")

    def test_invalid_thresholds_fail_closed(self):
        with self.assertRaises(ValueError):
            classify(0, 9.0, 9.0)


if __name__ == "__main__":
    unittest.main()
