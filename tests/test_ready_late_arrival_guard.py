import sys
import unittest
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ready_late_arrival_guard import is_pure_late_arrival_window_advance


def frame(rows):
    return pd.DataFrame(rows, columns=["security_id","date","ticker","open","high","low","close","adj_close","volume"])


class ReadyLateArrivalGuardTests(unittest.TestCase):
    def base(self):
        return frame([
            ["A","2026-09-14","AAA",10,11,9,10,10,100],
            ["A","2026-09-15","AAA",11,12,10,11,11,110],
            ["B","2026-09-14","BBB",20,21,19,20,20,200],
            ["B","2026-09-15","BBB",21,22,20,21,21,210],
        ])

    def test_pure_window_advance_is_recognized(self):
        new = frame([
            ["A","2026-09-15","AAA",11,12,10,11,11,110],
            ["A","2026-09-16","AAA",12,13,11,12,12,120],
            ["B","2026-09-14","BBB",20,21,19,20,20,200],
            ["B","2026-09-15","BBB",21,22,20,21,21,210],
        ])
        ok, evidence = is_pure_late_arrival_window_advance(self.base(), new, "2026-09-16")
        self.assertTrue(ok); self.assertEqual(evidence["affected_security_count"], 1)

    def test_changed_overlap_fails_closed(self):
        new = self.base(); new.loc[1, "close"] = 99
        ok, evidence = is_pure_late_arrival_window_advance(self.base(), new, "2026-09-16")
        self.assertFalse(ok); self.assertEqual(evidence["reason"], "overlap_values_changed")

    def test_security_set_change_fails_closed(self):
        new = self.base(); new.loc[new.security_id == "B", "security_id"] = "C"
        ok, evidence = is_pure_late_arrival_window_advance(self.base(), new, "2026-09-16")
        self.assertFalse(ok); self.assertEqual(evidence["reason"], "security_set_changed")

    def test_unbalanced_rows_fail_closed(self):
        new = pd.concat([self.base(), frame([["A","2026-09-16","AAA",12,13,11,12,12,120]])], ignore_index=True)
        ok, evidence = is_pure_late_arrival_window_advance(self.base(), new, "2026-09-16")
        self.assertFalse(ok); self.assertEqual(evidence["reason"], "per_security_row_counts_changed")

    def test_future_row_fails_closed(self):
        new = frame([
            ["A","2026-09-15","AAA",11,12,10,11,11,110],
            ["A","2026-09-17","AAA",12,13,11,12,12,120],
            ["B","2026-09-14","BBB",20,21,19,20,20,200],
            ["B","2026-09-15","BBB",21,22,20,21,21,210],
        ])
        ok, evidence = is_pure_late_arrival_window_advance(self.base(), new, "2026-09-16")
        self.assertFalse(ok); self.assertEqual(evidence["reason"], "added_after_as_of")

    def test_backward_replacement_fails_closed(self):
        new = frame([
            ["A","2026-09-13","AAA",9,10,8,9,9,90],
            ["A","2026-09-14","AAA",10,11,9,10,10,100],
            ["B","2026-09-14","BBB",20,21,19,20,20,200],
            ["B","2026-09-15","BBB",21,22,20,21,21,210],
        ])
        ok, evidence = is_pure_late_arrival_window_advance(self.base(), new, "2026-09-16")
        self.assertFalse(ok); self.assertEqual(evidence["reason"], "not_forward_window_advance")


if __name__ == "__main__": unittest.main()
