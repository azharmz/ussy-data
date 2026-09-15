import unittest
from unittest.mock import MagicMock

from src.publish_r2_storage_status import build_document


class R2StorageStatusTests(unittest.TestCase):
    def fake_s3(self):
        s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [
                {"Key": "production/a.parquet", "Size": 2 * 1024**3},
                {"Key": "canslim/a.json.gz", "Size": 1 * 1024**3},
                {"Key": "root.json", "Size": 100},
            ]}
        ]
        s3.get_paginator.return_value = paginator
        return s3

    def test_contract_and_prefix_breakdown(self):
        doc = build_document(self.fake_s3(), "bucket", 7, 9)
        self.assertEqual(doc["contract"], "ussy-r2-storage-status-v1")
        self.assertEqual(doc["scope"], "ENTIRE_BUCKET")
        self.assertEqual(doc["status"], "SAFE")
        self.assertTrue(doc["write_allowed"])
        self.assertEqual(doc["total_objects"], 3)
        self.assertEqual(doc["prefixes"][0]["prefix"], "production")
        self.assertEqual({row["prefix"] for row in doc["prefixes"]}, {"production", "canslim", "(root)"})

    def test_hard_stop_matches_guard_semantics(self):
        s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": [{"Key": "x/a", "Size": 9 * 1024**3}]}]
        s3.get_paginator.return_value = paginator
        doc = build_document(s3, "bucket", 7, 9)
        self.assertEqual(doc["status"], "HARD_STOP")
        self.assertFalse(doc["write_allowed"])
        self.assertEqual(doc["headroom_to_hard_stop_gib"], 0)


if __name__ == "__main__":
    unittest.main()
