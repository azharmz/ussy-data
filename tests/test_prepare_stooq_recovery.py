import sys, tempfile, unittest
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_stooq_recovery import read_stooq


class StooqRecoveryTest(unittest.TestCase):
    def test_normalizes_and_preserves_prices(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "aapl.us.txt"
            p.write_text("<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\nAAPL.US,D,20260922,000000,340.135,345.34,338.75,339.75,40711786,0\n")
            frame = read_stooq(p, "US0378331005", "AAPL")
            self.assertEqual(list(frame.columns), ["date", "security_id", "ticker", "open", "high", "low", "close", "adj_close", "volume"])
            self.assertEqual(frame.loc[0, "close"], 339.75)
            self.assertEqual(frame.loc[0, "adj_close"], 339.75)
            self.assertEqual(int(frame.loc[0, "volume"]), 40711786)

    def test_rejects_bad_ohlc(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.us.txt"
            p.write_text("<DATE>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>\n20260922,10,9,8,10,1\n")
            frame = read_stooq(p, "X", "X")
            self.assertEqual(len(frame.attrs.get("rejected_bars", [])), 1)


if __name__ == "__main__":
    unittest.main()
