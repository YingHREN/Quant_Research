from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import pandas as pd

from web.services.research_universe import (
    ResearchUniverseRepository,
    UnknownResearchTicker,
)


def _create_database(path, member_count=3):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE universe_memberships (
                universe_key TEXT NOT NULL,
                ticker TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                selection_rule TEXT NOT NULL,
                close REAL,
                market_cap REAL,
                avg_volume_50d REAL,
                avg_dollar_volume_50d REAL,
                PRIMARY KEY (universe_key, ticker, effective_from)
            );
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
            );
            CREATE TABLE security_master (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                exchange TEXT,
                security_type TEXT NOT NULL,
                active INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                provider TEXT NOT NULL
            );
            """
        )
        memberships = [
            ("research", "AAA", "2020-01-01", "2026-01-01", "fixture", 8_000_000_000),
            ("research", "AAA", "2026-01-01", None, "fixture", 12_000_000_000),
            ("research", "FUTURE", "2027-01-01", None, "fixture", None),
            (
                "research",
                "ENDED",
                "2020-01-01",
                "2026-07-24",
                "fixture",
                None,
            ),
        ]
        if member_count > 4:
            memberships.extend(
                (
                    "research",
                    f"T{number:03d}",
                    "2020-01-01",
                    None,
                    "fixture",
                    None,
                )
                for number in range(member_count - 4)
            )
        connection.executemany(
            """
            INSERT INTO universe_memberships (
                universe_key, ticker, effective_from, effective_to,
                selection_rule, market_cap
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            memberships,
        )
        tickers = sorted({row[1] for row in memberships} | {"SPY", "QQQ", "SEMI"})
        dates = pd.bdate_range("2025-07-24", "2026-07-24")
        rows = []
        for ticker_number, ticker in enumerate(tickers):
            ticker_dates = dates[:-1] if ticker == "AAA" else dates
            for day_number, date in enumerate(ticker_dates):
                close = 100.0 + ticker_number + day_number / 10
                rows.append(
                    (
                        ticker,
                        date.date().isoformat(),
                        close - 1,
                        close + 1,
                        close - 2,
                        close,
                        close - 1,
                        close + 1,
                        close - 2,
                        close,
                        1.0,
                        1_000_000 + day_number,
                        1,
                        "fixture",
                        "2026-07-24",
                        "2026-07-25T00:00:00Z",
                        "none",
                    )
                )
        connection.executemany(
            "INSERT INTO daily_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.executemany(
            "INSERT INTO security_master VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    ticker,
                    f"{ticker} Incorporated",
                    "NASDAQ",
                    "Common Stock",
                    1,
                    "2026-07-24",
                    "fixture",
                )
                for ticker in tickers
            ],
        )


class ResearchUniverseRepositoryTest(unittest.TestCase):
    def test_snapshot_honors_half_open_membership_and_marks_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            _create_database(database)

            snapshot = ResearchUniverseRepository(database).snapshot(
                "2026-07-24", sessions=260
            )

        self.assertEqual(snapshot.status, "available")
        self.assertEqual(snapshot.asof, "2026-07-24")
        self.assertEqual([member.ticker for member in snapshot.members], ["AAA"])
        self.assertTrue(snapshot.members[0].stale)
        self.assertEqual(snapshot.members[0].latest_date, "2026-07-23")
        self.assertEqual(snapshot.members[0].market_cap, 12_000_000_000)
        self.assertEqual(snapshot.members[0].market_cap_asof, "2026-01-01")
        self.assertLessEqual(len(snapshot.histories["AAA"]), 260)
        self.assertEqual(snapshot.histories["AAA"].index.max().date().isoformat(), "2026-07-23")

    def test_load_market_cap_reads_only_the_latest_effective_membership(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            _create_database(database)
            repository = ResearchUniverseRepository(database)

            market_cap, market_cap_asof = repository.load_market_cap(
                "AAA",
                "2026-07-24",
            )

        self.assertEqual(market_cap, 12_000_000_000)
        self.assertEqual(market_cap_asof, "2026-01-01")

    def test_detail_loads_selected_ticker_and_explicit_benchmarks_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            _create_database(database)
            repository = ResearchUniverseRepository(database)

            detail = repository.load_detail_snapshot(
                "AAA",
                "2026-07-24",
                benchmark_tickers=("SEMI",),
            )

        self.assertEqual(detail.ticker, "AAA")
        self.assertEqual(set(detail.histories), {"AAA", "SPY", "QQQ", "SEMI"})
        self.assertNotIn("ENDED", detail.histories)

    def test_detail_rejects_unknown_or_out_of_period_ticker(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            _create_database(database)
            repository = ResearchUniverseRepository(database)

            with self.assertRaises(UnknownResearchTicker):
                repository.load_detail_snapshot("ENDED", "2026-07-24")

    def test_missing_database_returns_unavailable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing.db"
            snapshot = ResearchUniverseRepository(database).snapshot()

        self.assertEqual(snapshot.status, "unavailable")
        self.assertIsNone(snapshot.asof)
        self.assertEqual(snapshot.histories, {})

    def test_snapshot_query_count_is_constant_as_membership_grows(self):
        counts = []
        original_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as directory:
            for member_count in (3, 200):
                database = Path(directory) / f"research-{member_count}.db"
                _create_database(database, member_count=member_count)
                statements = []

                def traced_connect(*args, **kwargs):
                    connection = original_connect(*args, **kwargs)
                    connection.set_trace_callback(statements.append)
                    return connection

                with mock.patch(
                    "web.services.research_universe.sqlite3.connect",
                    side_effect=traced_connect,
                ):
                    snapshot = ResearchUniverseRepository(database).snapshot(
                        "2026-07-24"
                    )
                self.assertEqual(snapshot.status, "available")
                counts.append(
                    len(
                        [
                            statement
                            for statement in statements
                            if statement.lstrip().upper().startswith(
                                ("SELECT", "WITH")
                            )
                        ]
                    )
                )

        self.assertEqual(counts, [2, 2])

    def test_malformed_numeric_row_makes_snapshot_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            _create_database(database)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE daily_prices
                    SET adjusted_close = 'not-a-number'
                    WHERE ticker = 'AAA' AND date = '2026-07-23'
                    """
                )

            snapshot = ResearchUniverseRepository(database).snapshot("2026-07-24")

        self.assertEqual(snapshot.status, "unavailable")
        self.assertEqual(snapshot.reason, "malformed_numeric_price")

    def test_missing_schema_makes_snapshot_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            sqlite3.connect(database).close()

            snapshot = ResearchUniverseRepository(database).snapshot("2026-07-24")

        self.assertEqual(snapshot.status, "unavailable")
        self.assertEqual(snapshot.reason, "database_unavailable")


if __name__ == "__main__":
    unittest.main()
