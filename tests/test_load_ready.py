import hashlib
import io
import json
import unittest

import pandas as pd

from src.load_ready import load_ready


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


def parquet_bytes(frame):
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    return buf.getvalue()


class LoadReadyTests(unittest.TestCase):
    def _fixture(self, schema_version=2, **overrides):
        frame = pd.DataFrame({"security_id": ["SEC1"], "date": [pd.Timestamp("2026-09-16")]})
        body = parquet_bytes(frame)
        manifest = {
            "schema_version": schema_version,
            "parquet_key": "production/ready/runs/test.parquet",
            "sha256": hashlib.sha256(body).hexdigest(),
            "rows": 1,
            "security_ids": ["SEC1"],
        }
        if schema_version == 2:
            manifest.update({
                "as_of_date": "2026-09-16",
                "as_of_security_count": 1,
                "terminal_date_min": "2026-09-16",
                "terminal_date_max": "2026-09-16",
            })
        manifest.update(overrides)
        objects = {
            "production/ready/current.json": json.dumps(manifest).encode(),
            manifest["parquet_key"]: body,
        }
        return FakeS3(objects)

    def test_accepts_v2_manifest(self):
        frame, manifest = load_ready(self._fixture(), "bucket")
        self.assertEqual(len(frame), 1)
        self.assertEqual(manifest["as_of_date"], "2026-09-16")

    def test_keeps_v1_backward_compatibility(self):
        frame, manifest = load_ready(self._fixture(schema_version=1), "bucket")
        self.assertEqual(len(frame), 1)
        self.assertEqual(manifest["schema_version"], 1)

    def test_rejects_incoherent_v2_as_of(self):
        with self.assertRaisesRegex(ValueError, "as-of/max terminal-date mismatch"):
            load_ready(self._fixture(terminal_date_max="2026-09-15"), "bucket")


if __name__ == "__main__":
    unittest.main()
