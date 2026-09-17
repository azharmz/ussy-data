import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ready_snapshot_guard import decide_ready_publication


class ReadySnapshotGuardTests(unittest.TestCase):
    def current(self, as_of="2026-09-16", sha="abc"):
        return {"as_of_date": as_of, "sha256": sha, "parquet_key": "production/ready/runs/existing.parquet"}

    def test_first_snapshot_is_created(self):
        self.assertEqual(decide_ready_publication(None, "2026-09-16", "abc"), "CREATE")

    def test_same_day_same_hash_is_reused(self):
        self.assertEqual(decide_ready_publication(self.current(), "2026-09-16", "abc"), "REUSE")

    def test_same_day_different_hash_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "same-day mutation rejected"):
            decide_ready_publication(self.current(), "2026-09-16", "different")

    def test_older_snapshot_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "rollback rejected"):
            decide_ready_publication(self.current(), "2026-09-15", "old")

    def test_newer_trading_date_is_created(self):
        self.assertEqual(decide_ready_publication(self.current(), "2026-09-17", "new"), "CREATE")

    def test_malformed_current_pointer_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "lacks canonical lineage fields"):
            decide_ready_publication({"as_of_date": "2026-09-16"}, "2026-09-17", "new")


if __name__ == "__main__":
    unittest.main()
