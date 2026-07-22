import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from web.services.market_data import InvalidTicker, MarketDataRepository, UnknownTicker


def create_price_db(path, rows_by_ticker):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (ticker, *row)
                for ticker, rows in rows_by_ticker.items()
                for row in rows
            ],
        )


class MarketDataRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prices.db"
        create_price_db(
            self.db,
            {
                "AAA": [
                    ("2026-07-20", 10, 11, 9, 10.5, 100),
                    ("2026-07-21", 10.5, 12, 10, 11.5, 150),
                ],
                "BBB": [("2026-07-20", 20, 21, 19, 20.5, 200)],
                "OLD": [("2026-06-20", 30, 31, 29, 30.5, 300)],
            },
        )
        self.repo = MarketDataRepository(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_freshness_counts_latest_dates_without_mixing_tickers(self):
        self.assertEqual(
            self.repo.freshness()["by_date"],
            [
                {"date": "2026-07-21", "tickers": 1},
                {"date": "2026-07-20", "tickers": 1},
                {"date": "2026-06-20", "tickers": 1},
            ],
        )

    def test_asof_truncates_future_rows(self):
        history = self.repo.load_history("AAA", "2026-07-20")
        self.assertEqual(history.index.max(), pd.Timestamp("2026-07-20"))
        self.assertEqual(list(history.columns), ["Open", "High", "Low", "Close", "Volume"])

    def test_rejects_ticker_before_query(self):
        with self.assertRaises(InvalidTicker):
            self.repo.load_history("AAA' OR 1=1 --")

    def test_unknown_ticker_is_distinct_from_empty_history(self):
        with self.assertRaises(UnknownTicker):
            self.repo.load_history("ZZZ")

    def test_summary_marks_lagging_ticker_inactive_with_actual_lag(self):
        summaries = {summary.ticker: summary for summary in self.repo.list_summaries()}
        self.assertFalse(summaries["AAA"].inactive)
        self.assertTrue(summaries["OLD"].inactive)
        self.assertEqual(summaries["OLD"].lag_days, 31)
        self.assertEqual(summaries["OLD"].latest_date, "2026-06-20")
