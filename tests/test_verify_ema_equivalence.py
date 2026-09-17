import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verify_ema_equivalence import history_through_ready_cutoff, select_equivalence_ids


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

    def test_history_is_cut_at_ready_as_of_not_newer_provider_bar(self):
        history = pd.DataFrame({
            "date": ["2026-09-15", "2026-09-16"],
            "adj_close": [100.0, 999.0],
        })
        result = history_through_ready_cutoff(history, "2026-09-15", "SEC1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[-1]["date"], pd.Timestamp("2026-09-15"))
        self.assertEqual(result.iloc[-1]["adj_close"], 100.0)

    def test_history_cutoff_fails_closed_when_no_reference_rows_exist(self):
        history = pd.DataFrame({"date": ["2026-09-16"], "adj_close": [100.0]})
        with self.assertRaisesRegex(RuntimeError, "no rows through READY cutoff"):
            history_through_ready_cutoff(history, "2026-09-15", "SEC1")


if __name__ == "__main__":
    unittest.main()
