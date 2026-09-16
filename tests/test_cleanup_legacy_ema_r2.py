import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location("cleanup_legacy_ema_r2", SRC / "cleanup_legacy_ema_r2.py")
cleanup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.deleted = []
        self.pointer_reads = 0
        self.move_pointer_on_second_read = False

    def get_object(self, *, Bucket, Key):
        if Key == cleanup.POINTER_KEY:
            self.pointer_reads += 1
            value = self.objects[Key]
            if self.move_pointer_on_second_read and self.pointer_reads >= 2:
                value = json.dumps({"parquet_key": cleanup.PRODUCTION_PREFIX + "run-999-1.parquet"}).encode()
            return {"Body": io.BytesIO(value)}
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        contents = [
            {"Key": key, "Size": len(value)}
            for key, value in sorted(self.objects.items())
            if key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def delete_object(self, *, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)


def dataset():
    p = cleanup.PRODUCTION_PREFIX
    c = cleanup.CANDIDATE_PREFIX
    current = p + "run-300-1.parquet"
    objects = {
        cleanup.POINTER_KEY: json.dumps({"parquet_key": current}).encode(),
        p + "run-100-1.parquet": b"old-prod",
        p + "run-200-1.parquet": b"prev-prod",
        current: b"current-prod",
        c + "run-100-1.parquet": b"old-candidate",
        c + "run-100-1.json": b"old-evidence",
        c + "run-200-1.parquet": b"prev-candidate",
        c + "run-200-1.json": b"prev-evidence",
        c + "run-300-1.parquet": b"current-candidate",
        c + "run-300-1.json": b"current-evidence",
        cleanup.REBUILD_HINT_KEY: b'{"security_ids":["X"]}',
        c + "notes.txt": b"unknown-object",
    }
    return objects, current


def run_main(monkeypatch, fake, *args):
    monkeypatch.setattr(cleanup, "make_s3_client", lambda: fake)
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")
    monkeypatch.setattr(sys, "argv", ["cleanup_legacy_ema_r2.py", *args])
    cleanup.main()


def test_apply_keeps_current_previous_hints_and_unknown_objects(monkeypatch):
    objects, current = dataset()
    fake = FakeS3(objects)
    run_main(monkeypatch, fake, "--apply", "--keep-production-runs", "2", "--keep-candidate-runs", "1")

    assert cleanup.PRODUCTION_PREFIX + "run-100-1.parquet" in fake.deleted
    assert cleanup.CANDIDATE_PREFIX + "run-100-1.parquet" in fake.deleted
    assert cleanup.CANDIDATE_PREFIX + "run-100-1.json" in fake.deleted
    assert current in fake.objects
    assert cleanup.PRODUCTION_PREFIX + "run-200-1.parquet" in fake.objects
    assert cleanup.CANDIDATE_PREFIX + "run-200-1.parquet" in fake.objects
    assert cleanup.CANDIDATE_PREFIX + "run-300-1.parquet" in fake.objects
    assert cleanup.REBUILD_HINT_KEY in fake.objects
    assert cleanup.CANDIDATE_PREFIX + "notes.txt" in fake.objects


def test_dry_run_never_deletes(monkeypatch):
    objects, _ = dataset()
    fake = FakeS3(objects)
    run_main(monkeypatch, fake)
    assert fake.deleted == []


def test_refuses_less_than_two_production_generations(monkeypatch):
    fake = FakeS3(dataset()[0])
    with pytest.raises(ValueError, match=">= 2"):
        run_main(monkeypatch, fake, "--apply", "--keep-production-runs", "1")
    assert fake.deleted == []


def test_pointer_move_aborts_before_deletion(monkeypatch):
    fake = FakeS3(dataset()[0])
    fake.move_pointer_on_second_read = True
    with pytest.raises(RuntimeError, match="pointer changed"):
        run_main(monkeypatch, fake, "--apply")
    assert fake.deleted == []
