import sqlite3
import tempfile
import unittest
from pathlib import Path

from research.expanded_market_data import ExpandedMarketDataRepository


class ExpandedMarketDataRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "research.db"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE daily_prices (
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    raw_open REAL NOT NULL,
                    raw_high REAL NOT NULL,
                    raw_low REAL NOT NULL,
                    raw_close REAL NOT NULL,
                    adjusted_open REAL NOT NULL,
                    adjusted_high REAL NOT NULL,
                    adjusted_low REAL NOT NULL,
                    adjusted_close REAL NOT NULL,
                    adjustment_factor REAL NOT NULL,
                    volume REAL NOT NULL,
                    segment_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    adjustment_method TEXT NOT NULL,
                    PRIMARY KEY (ticker, date)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE sector_classifications (
                    ticker TEXT NOT NULL,
                    taxonomy TEXT NOT NULL,
                    sector_key TEXT NOT NULL,
                    benchmark_ticker TEXT,
                    industry_code TEXT,
                    industry_label TEXT,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    asof TEXT NOT NULL,
                    PRIMARY KEY (ticker, taxonomy, rule_version, asof)
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO daily_prices VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    (
                        "AAA", "2025-01-02", 100, 102, 99, 101,
                        50, 51, 49.5, 50.5, 0.5, 1000, 1,
                        "eodhd", "2026-07-24", "2026-07-25T00:00:00Z",
                        "split_adjusted",
                    ),
                    (
                        "AAA", "2026-01-02", 200, 204, 198, 202,
                        200, 204, 198, 202, 1.0, 2000, 2,
                        "eodhd", "2026-07-24", "2026-07-25T00:00:00Z",
                        "split_adjusted",
                    ),
                    (
                        "AAA", "2026-01-05", 202, 206, 201, 205,
                        202, 206, 201, 205, 1.0, 3000, 2,
                        "eodhd", "2026-07-24", "2026-07-25T00:00:00Z",
                        "split_adjusted",
                    ),
                    (
                        "BBB", "2026-01-02", 20, 21, 19, 20,
                        20, 21, 19, 20, 1.0, 4000, 1,
                        "eodhd", "2026-07-24", "2026-07-25T00:00:00Z",
                        "split_adjusted",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO sector_classifications VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    (
                        "AAA", "sec", "technology", None, "3674",
                        "Semiconductors", 0.95, "sec", "sec_sic_v1",
                        "2026-06-30",
                    ),
                    (
                        "AAA", "market_behavior", "technology", "XLK", None,
                        None, 0.75, "price_returns", "market_behavior_v1",
                        "2026-06-30",
                    ),
                    (
                        "AAA", "market_behavior", "communication", "XLC", None,
                        None, 0.80, "price_returns", "market_behavior_v2",
                        "2026-07-24",
                    ),
                    (
                        "BBB", "sec", "industrials", None, "3500",
                        "Industrial Machinery", 0.90, "sec", "sec_sic_v1",
                        "2026-06-30",
                    ),
                ),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_loads_latest_identity_segment_with_adjusted_ohlcv(self):
        repository = ExpandedMarketDataRepository(self.database)

        histories = repository.load_universe_histories()

        self.assertEqual(set(histories), {"AAA", "BBB"})
        self.assertEqual(len(histories["AAA"]), 2)
        self.assertEqual(
            list(histories["AAA"].columns),
            ["Open", "High", "Low", "Close", "Volume"],
        )
        self.assertEqual(histories["AAA"].iloc[0]["Open"], 200)
        self.assertEqual(histories["AAA"].attrs["segment_id"], 2)
        self.assertEqual(histories["AAA"].attrs["provider"], "eodhd")
        self.assertEqual(
            histories["AAA"].attrs["source_cutoff"],
            "2026-01-05",
        )

    def test_asof_and_ticker_filters_are_applied_in_sql(self):
        repository = ExpandedMarketDataRepository(self.database)

        histories = repository.load_universe_histories(
            asof="2026-01-02",
            tickers=("AAA",),
        )

        self.assertEqual(set(histories), {"AAA"})
        self.assertEqual(histories["AAA"].index[-1].date().isoformat(), "2026-01-02")

    def test_loads_latest_classification_per_taxonomy(self):
        repository = ExpandedMarketDataRepository(self.database)

        classifications = repository.load_classifications()

        self.assertEqual(classifications["AAA"]["sec"]["sector_key"], "technology")
        self.assertEqual(
            classifications["AAA"]["sec"]["industry_label"],
            "Semiconductors",
        )
        self.assertEqual(
            classifications["AAA"]["market_behavior"]["benchmark_ticker"],
            "XLC",
        )
        self.assertEqual(
            classifications["AAA"]["market_behavior"]["rule_version"],
            "market_behavior_v2",
        )
        self.assertIsNone(classifications["BBB"].get("market_behavior"))

    def test_classification_asof_excludes_later_snapshot(self):
        repository = ExpandedMarketDataRepository(self.database)

        classifications = repository.load_classifications(
            asof="2026-06-30",
            tickers=("AAA",),
        )

        behavior = classifications["AAA"]["market_behavior"]
        self.assertEqual(behavior["benchmark_ticker"], "XLK")
        self.assertEqual(behavior["asof"], "2026-06-30")


if __name__ == "__main__":
    unittest.main()
