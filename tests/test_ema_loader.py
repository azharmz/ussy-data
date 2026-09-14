import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ema_state import PERIODS, bootstrap_state
from load_ema_state import EMA_POINTER_KEY, load_ema_state


class FakeBody:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key])}


class EMALoaderTests(unittest.TestCase):
    def payloads(self):
        prices = np.linspace(20, 40, 250)
        source = pd.DataFrame({
            "date": pd.bdate_range("2024-01-01", periods=250),
            "security_id": "A", "ticker": "AAA", "adj_close": prices,
        })
        state = pd.DataFrame([bootstrap_state(source)])
        body = b"valid-parquet-placeholder"
        key = "production/indicators/ema/runs/test.parquet"
        manifest = {
            "schema_version": 1,
            "created_at": "2026-09-14T00:00:00+00:00",
            "price_basis": "adj_close",
            "periods": list(PERIODS),
            "securities": 1,
            "security_ids": ["A"],
            "parquet_key": key,
            "sha256": hashlib.sha256(body).hexdigest(),
            "source_ready_parquet_key": "production/ready/runs/source.parquet",
            "source_ready_sha256": "abc",
            "update_method": "test",
        }
        return {EMA_POINTER_KEY: json.dumps(manifest).encode(), key: body}, key, state

    def test_loads_valid_state(self):
        objects, _, state = self.payloads()
        with patch("pandas.read_parquet", return_value=state):
            frame, manifest = load_ema_state(FakeS3(objects), "bucket")
        self.assertEqual(len(frame), 1)
        self.assertEqual(manifest["price_basis"], "adj_close")

    def test_rejects_checksum_mismatch(self):
        objects, key, _ = self.payloads()
        objects[key] += b"corrupt"
        with self.assertRaises(ValueError):
            load_ema_state(FakeS3(objects), "bucket")


if __name__ == '__main__':
    unittest.main()
