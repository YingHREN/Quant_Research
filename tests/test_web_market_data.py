import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from web.services.market_data import (
    InvalidTicker,
    MarketDataRepository,
    MarketDataUnavailable,
    UnknownTicker,
)


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
                volume REAL NOT NULL,
                PRIMARY KEY (ticker, date)
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

    def test_load_universe_histories_returns_all_frames_in_one_asof_snapshot(self):
        histories = self.repo.load_universe_histories("2026-07-20")

        self.assertEqual(set(histories), {"AAA", "BBB", "OLD"})
        self.assertEqual(histories["AAA"].index.max(), pd.Timestamp("2026-07-20"))
        self.assertEqual(histories["BBB"].index.max(), pd.Timestamp("2026-07-20"))
        self.assertEqual(histories["OLD"].index.max(), pd.Timestamp("2026-06-20"))
        for history in histories.values():
            self.assertEqual(
                list(history.columns), ["Open", "High", "Low", "Close", "Volume"]
            )

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

    def test_missing_database_is_not_created_and_raises_safe_error(self):
        missing_db = Path(self.tmp.name) / "missing.db"
        with self.assertRaisesRegex(MarketDataUnavailable, "^Market data is unavailable$"):
            MarketDataRepository(missing_db).freshness()
        self.assertFalse(missing_db.exists())

    def test_malformed_database_raises_safe_error_without_sqlite_text(self):
        malformed_db = Path(self.tmp.name) / "malformed.db"
        malformed_db.write_text("not a sqlite database")
        with self.assertRaises(MarketDataUnavailable) as context:
            MarketDataRepository(malformed_db).freshness()
        self.assertEqual(str(context.exception), "Market data is unavailable")
        self.assertNotIn("file is not a database", str(context.exception).lower())

    def test_bulk_read_malformed_database_raises_safe_market_data_error(self):
        malformed_db = Path(self.tmp.name) / "malformed-bulk.db"
        malformed_db.write_text("not a sqlite database")

        with self.assertRaises(MarketDataUnavailable) as context:
            MarketDataRepository(malformed_db).load_universe_histories("2026-07-21")

        self.assertEqual(str(context.exception), "Market data is unavailable")
        self.assertNotIn("file is not a database", str(context.exception).lower())

    def test_upsert_history_commits_replaced_and_new_rows_for_read_paths(self):
        frame = pd.DataFrame(
            {
                "Open": [11.5, 12.5],
                "High": [13.0, 14.0],
                "Low": [11.0, 12.0],
                "Close": [12.5, 13.5],
                "Volume": [175.0, 225.0],
            },
            index=pd.DatetimeIndex(["2026-07-21", "2026-07-22"], name="Date"),
        )

        self.repo.upsert_history("AAA", frame)
        persisted = self.repo.load_history("AAA")

        self.assertEqual(len(persisted), 3)
        self.assertEqual(persisted.loc["2026-07-21", "Close"], 12.5)
        self.assertEqual(persisted.loc["2026-07-22", "Volume"], 225.0)

    def test_upsert_history_validates_ticker_and_frame_before_writing(self):
        valid = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.5],
                "Volume": [100.0],
            },
            index=pd.DatetimeIndex(["2026-07-22"], name="Date"),
        )

        with self.assertRaises(InvalidTicker):
            self.repo.upsert_history("AAA; DROP TABLE prices", valid)
        with self.assertRaises(ValueError):
            self.repo.upsert_history("AAA", valid.drop(columns="Volume"))

        self.assertNotIn(pd.Timestamp("2026-07-22"), self.repo.load_history("AAA").index)

    def test_upsert_history_rejects_impossible_or_empty_bars_atomically(self):
        before = self.repo.load_history("AAA")
        index = pd.DatetimeIndex(["2026-07-21", "2026-07-22"], name="Date")
        invalid_frames = {
            "empty": pd.DataFrame(columns=("Open", "High", "Low", "Close", "Volume"),
                                  index=pd.DatetimeIndex([], name="Date")).astype(float),
            "non_positive_price": pd.DataFrame(
                {"Open": [11.5, 12.0], "High": [13.0, 13.0], "Low": [11.0, 0.0],
                 "Close": [12.5, 12.5], "Volume": [175.0, 200.0]}, index=index),
            "negative_volume": pd.DataFrame(
                {"Open": [11.5, 12.0], "High": [13.0, 13.0], "Low": [11.0, 11.5],
                 "Close": [12.5, 12.5], "Volume": [175.0, -1.0]}, index=index),
            "high_below_price": pd.DataFrame(
                {"Open": [11.5, 14.0], "High": [13.0, 13.0], "Low": [11.0, 11.5],
                 "Close": [12.5, 12.5], "Volume": [175.0, 200.0]}, index=index),
            "low_above_price": pd.DataFrame(
                {"Open": [11.5, 12.0], "High": [13.0, 14.0], "Low": [11.0, 12.25],
                 "Close": [12.5, 12.5], "Volume": [175.0, 200.0]}, index=index),
            "high_below_low": pd.DataFrame(
                {"Open": [11.5, 12.0], "High": [13.0, 11.0], "Low": [11.0, 11.5],
                 "Close": [12.5, 11.75], "Volume": [175.0, 200.0]}, index=index),
        }

        for label, frame in invalid_frames.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "^Provider history is invalid$") as context:
                    self.repo.upsert_history("AAA", frame)
                self.assertEqual(context.exception.__class__.__name__, "InvalidMarketData")
                pd.testing.assert_frame_equal(self.repo.load_history("AAA"), before)
