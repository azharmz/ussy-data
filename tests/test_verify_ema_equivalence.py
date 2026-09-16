import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verify_ema_equivalence import select_equivalence_ids


class EMAEquivalenceSelectionTests(unittest.TestCase):
    def state(self):
        return pd.DataFrame({"security_id": ["D", "B", "A", "C"]})

    def test_required_ids_are_always_verified_plus_deterministic_sample(self):
        manifest = {"bootstrap_security_ids": ["D"], "rebuild_security_ids": ["C"]}
        selected, mode = select_equivalence_ids(self.state(), manifest, 1)
        self.assertEqual(selected, ["A", "C", "D"])
        self.assertEqual(mode, "required_plus_sample")

    def test_no_required_ids_uses_sorted_deterministic_sample(self):
        selected, mode = select_equivalence_ids(self.state(), {}, 2)
        self.assertEqual(selected, ["A", "B"])
        self.assertEqual(mode, "sample")

    def test_initial_bootstrap_verifies_all(self):
        manifest = {"bootstrap_security_ids": ["A", "B", "C", "D"], "rebuild_security_ids": []}
        selected, mode = select_equivalence_ids(self.state(), manifest, 1)
        self.assertEqual(selected, ["A", "B", "C", "D"])
        self.assertEqual(mode, "all_required")

    def test_unknown_required_id_fails_closed(self):
        manifest = {"bootstrap_security_ids": ["X"], "rebuild_security_ids": []}
        with self.assertRaises(RuntimeError):
            select_equivalence_ids(self.state(), manifest, 2)


if __name__ == "__main__":
    unittest.main()
