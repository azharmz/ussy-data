import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location("cleanup_legacy_ema_r2", SRC / "cleanup_legacy_ema_r2.py")
cleanup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)

class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects); self.deleted = []; self.pointer_reads = 0; self.move_pointer_on_second_read = False
    def get_object(self, *, Bucket, Key):
        if Key == cleanup.POINTER_KEY:
            self.pointer_reads += 1; value = self.objects[Key]
            if self.move_pointer_on_second_read and self.pointer_reads >= 2:
                value = json.dumps({"parquet_key": cleanup.PRODUCTION_PREFIX + "run-999-1.parquet"}).encode()
            return {"Body": io.BytesIO(value)}
        return {"Body": io.BytesIO(self.objects[Key])}
    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        return {"Contents": [{"Key": k, "Size": len(v)} for k, v in sorted(self.objects.items()) if k.startswith(Prefix)], "IsTruncated": False}
    def delete_object(self, *, Bucket, Key):
        self.deleted.append(Key); self.objects.pop(Key, None)

def dataset():
    p, c = cleanup.PRODUCTION_PREFIX, cleanup.CANDIDATE_PREFIX; current = p + "run-300-1.parquet"
    return {cleanup.POINTER_KEY: json.dumps({"parquet_key": current}).encode(), p+"run-100-1.parquet": b"old-prod", p+"run-200-1.parquet": b"prev-prod", current: b"current-prod", c+"run-100-1.parquet": b"old-candidate", c+"run-100-1.json": b"old-evidence", c+"run-200-1.parquet": b"prev-candidate", c+"run-200-1.json": b"prev-evidence", c+"run-300-1.parquet": b"current-candidate", c+"run-300-1.json": b"current-evidence", cleanup.REBUILD_HINT_KEY: b'{"security_ids":["X"]}', c+"notes.txt": b"unknown-object"}, current

def run_main(fake, *args):
    with patch.object(cleanup, "make_s3_client", return_value=fake), patch.dict(os.environ, {"R2_BUCKET_NAME": "bucket"}), patch.object(sys, "argv", ["cleanup_legacy_ema_r2.py", *args]): cleanup.main()

class CleanupLegacyEmaR2Tests(unittest.TestCase):
    def test_apply_keeps_current_previous_hints_and_unknown_objects(self):
        objects, current = dataset(); fake = FakeS3(objects); run_main(fake, "--apply", "--keep-production-runs", "2", "--keep-candidate-runs", "1")
        self.assertIn(cleanup.PRODUCTION_PREFIX+"run-100-1.parquet", fake.deleted); self.assertIn(cleanup.CANDIDATE_PREFIX+"run-100-1.parquet", fake.deleted); self.assertIn(cleanup.CANDIDATE_PREFIX+"run-100-1.json", fake.deleted)
        self.assertIn(current, fake.objects); self.assertIn(cleanup.PRODUCTION_PREFIX+"run-200-1.parquet", fake.objects); self.assertIn(cleanup.CANDIDATE_PREFIX+"run-200-1.parquet", fake.objects); self.assertIn(cleanup.CANDIDATE_PREFIX+"run-300-1.parquet", fake.objects); self.assertIn(cleanup.REBUILD_HINT_KEY, fake.objects); self.assertIn(cleanup.CANDIDATE_PREFIX+"notes.txt", fake.objects)
    def test_dry_run_never_deletes(self):
        fake = FakeS3(dataset()[0]); run_main(fake); self.assertEqual(fake.deleted, [])
    def test_refuses_less_than_two_production_generations(self):
        fake = FakeS3(dataset()[0])
        with self.assertRaisesRegex(ValueError, ">= 2"): run_main(fake, "--apply", "--keep-production-runs", "1")
        self.assertEqual(fake.deleted, [])
    def test_pointer_move_aborts_before_deletion(self):
        fake = FakeS3(dataset()[0]); fake.move_pointer_on_second_read = True
        with self.assertRaisesRegex(RuntimeError, "pointer changed"): run_main(fake, "--apply")
        self.assertEqual(fake.deleted, [])

if __name__ == "__main__": unittest.main()
