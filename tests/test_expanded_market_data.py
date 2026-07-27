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
            connection.execute(
                """
                CREATE TABLE universe_memberships (
                    universe_key TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    selection_rule TEXT NOT NULL,
                    source TEXT,
                    source_snapshot_date TEXT,
                    imported_at TEXT,
                    is_delisted INTEGER,
                    security_name TEXT,
                    PRIMARY KEY (universe_key, ticker, effective_from)
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
                INSERT INTO universe_memberships VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    (
                        "sp500_historical_eodhd_v1", "AAPL", "2010-01-01",
                        None, "sp500_historical_eodhd_v1", "eodhd",
                        "2026-07-27", "2026-07-27T10:00:00Z", 0,
                        "Apple Inc",
                    ),
                    (
                        "sp500_historical_eodhd_v1", "TWTR", "2018-06-07",
                        "2022-10-28", "sp500_historical_eodhd_v1", "eodhd",
                        "2026-07-27", "2026-07-27T10:00:00Z", 1,
                        "Twitter Inc",
                    ),
                    (
                        "sp500_historical_eodhd_v1", "META", "2022-06-09",
                        None, "sp500_historical_eodhd_v1", "eodhd",
                        "2026-07-27", "2026-07-27T10:00:00Z", 0,
                        "Meta Platforms Inc",
                    ),
                    (
                        "other_universe", "IGNORED", "2010-01-01",
                        None, "other_universe", "fixture", "2026-07-27",
                        "2026-07-27T10:00:00Z", 0, "Ignored",
                    ),
                    (
                        "legacy_v1", "UNKNOWN", "2010-01-01",
                        None, "legacy_v1", None, None, None, None,
                        "Unknown status",
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

    def test_universe_members_use_half_open_effective_intervals(self):
        repository = ExpandedMarketDataRepository(self.database)

        before_removal = repository.load_universe_members(
            universe_key="sp500_historical_eodhd_v1",
            asof="2022-10-27",
        )
        on_removal = repository.load_universe_members(
            universe_key="sp500_historical_eodhd_v1",
            asof="2022-10-28",
        )

        self.assertEqual(set(before_removal), {"AAPL", "META", "TWTR"})
        self.assertEqual(set(on_removal), {"AAPL", "META"})
        self.assertTrue(before_removal["TWTR"]["is_delisted"])
        self.assertEqual(before_removal["TWTR"]["source"], "eodhd")
        self.assertEqual(
            before_removal["TWTR"]["effective_to"],
            "2022-10-28",
        )

    def test_universe_members_do_not_backfill_later_additions(self):
        repository = ExpandedMarketDataRepository(self.database)

        before_twitter = repository.load_universe_members(
            universe_key="sp500_historical_eodhd_v1",
            asof="2018-06-06",
        )
        on_twitter_entry = repository.load_universe_members(
            universe_key="sp500_historical_eodhd_v1",
            asof="2018-06-07",
        )

        self.assertEqual(set(before_twitter), {"AAPL"})
        self.assertEqual(set(on_twitter_entry), {"AAPL", "TWTR"})
        self.assertEqual(
            repository.load_universe_members(
                universe_key="unknown",
                asof="2020-01-01",
            ),
            {},
        )

    def test_batch_universe_members_match_single_date_reads(self):
        repository = ExpandedMarketDataRepository(self.database)
        dates = ("2018-06-06", "2018-06-07", "2022-10-28")

        batch = repository.load_universe_members_by_date(
            universe_key="sp500_historical_eodhd_v1",
            observation_dates=dates,
        )

        self.assertEqual(
            batch,
            {
                date: frozenset(
                    repository.load_universe_members(
                        universe_key="sp500_historical_eodhd_v1",
                        asof=date,
                    )
                )
                for date in dates
            },
        )

    def test_universe_member_reads_require_observation_dates(self):
        repository = ExpandedMarketDataRepository(self.database)

        with self.assertRaises(TypeError):
            repository.load_universe_members(
                universe_key="sp500_historical_eodhd_v1"
            )
        with self.assertRaises(ValueError):
            repository.load_universe_members_by_date(
                universe_key="sp500_historical_eodhd_v1",
                observation_dates=(),
            )

    def test_legacy_membership_does_not_invent_delisted_status(self):
        repository = ExpandedMarketDataRepository(self.database)

        members = repository.load_universe_members(
            universe_key="legacy_v1",
            asof="2020-01-01",
        )

        self.assertIsNone(members["UNKNOWN"]["is_delisted"])


if __name__ == "__main__":
    unittest.main()
