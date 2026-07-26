from datetime import date
import sqlite3
import unittest
from unittest import mock

import pandas as pd

from build_local_db import backfill, update_tickers
from data.fetch import StockData


class BuildLocalDatabaseTest(unittest.TestCase):
    def test_incremental_update_includes_new_reference_tickers(self):
        tickers = update_tickers(["AAPL", "QQQ"])

        self.assertIn("AAPL", tickers)
        self.assertIn("IGV", tickers)
        self.assertIn("XSW", tickers)
        self.assertEqual(tickers, sorted(set(tickers)))

    def test_backfill_requests_ten_year_window_and_reports_short_coverage(self):
        connection = sqlite3.connect(":memory:")
        history = pd.DataFrame(
            {
                "Open": [10.0, 11.0],
                "High": [11.0, 12.0],
                "Low": [9.0, 10.0],
                "Close": [10.5, 11.5],
                "Volume": [100.0, 110.0],
            },
            index=pd.DatetimeIndex(["2025-01-02", "2026-01-02"], name="Date"),
        )
        calls = []

        def fake_fetch(ticker, *, period, use_cache):
            calls.append((ticker, period, use_cache))
            return StockData(ticker, history, ok=True)

        with mock.patch("build_local_db.time.sleep"):
            summary = backfill(
                years=10,
                tickers=("AAA",),
                connection=connection,
                fetcher=fake_fetch,
                asof=date(2026, 1, 3),
            )

        self.assertEqual(calls, [("AAA", "10y", False)])
        self.assertEqual(summary.requested, 1)
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.below_eight_year_floor, 1)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
            2,
        )


if __name__ == "__main__":
    unittest.main()
