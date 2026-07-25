import unittest

from build_local_db import update_tickers


class BuildLocalDatabaseTest(unittest.TestCase):
    def test_incremental_update_includes_new_reference_tickers(self):
        tickers = update_tickers(["AAPL", "QQQ"])

        self.assertIn("AAPL", tickers)
        self.assertIn("IGV", tickers)
        self.assertIn("XSW", tickers)
        self.assertEqual(tickers, sorted(set(tickers)))


if __name__ == "__main__":
    unittest.main()
