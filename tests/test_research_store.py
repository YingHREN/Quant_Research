from __future__ import annotations

import sqlite3
import unittest

from data.point_in_time_universe import HistoricalMembership, SymbolChange
from data.research_store import ResearchPriceStore, normalize_daily_rows


def daily(date, open_, high, low, close, adjusted_close, volume=1000):
    return {
        "date": date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "adjusted_close": adjusted_close,
        "volume": volume,
    }


class ResearchStoreTest(unittest.TestCase):
    def test_initialize_migrates_old_memberships_without_losing_rows(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE security_master (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                security_type TEXT NOT NULL,
                active INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                provider TEXT NOT NULL
            );
            CREATE TABLE universe_memberships (
                universe_key TEXT NOT NULL,
                ticker TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                selection_rule TEXT NOT NULL,
                PRIMARY KEY (universe_key, ticker, effective_from)
            );
            INSERT INTO security_master VALUES (
                'AAA', 'Example', 'Common Stock', 1, '2026-07-24', 'fixture'
            );
            INSERT INTO universe_memberships VALUES (
                'legacy_v1', 'AAA', '2026-07-24', NULL, 'legacy_v1'
            );
            """
        )

        ResearchPriceStore(connection).initialize()

        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(universe_memberships)"
            )
        }
        self.assertTrue(
            {
                "source",
                "source_snapshot_date",
                "imported_at",
                "is_delisted",
                "security_name",
            }.issubset(columns)
        )
        self.assertEqual(
            connection.execute(
                "SELECT ticker FROM universe_memberships"
            ).fetchall(),
            [("AAA",)],
        )

    def test_replace_historical_memberships_is_atomic_and_idempotent(self):
        connection = sqlite3.connect(":memory:")
        store = ResearchPriceStore(connection)
        store.initialize()
        initial = (
            HistoricalMembership(
                ticker="AAPL",
                security_name="Apple Inc",
                effective_from="1982-11-30",
                effective_to=None,
                is_active_now=True,
                is_delisted=False,
            ),
            HistoricalMembership(
                ticker="TWTR",
                security_name="Twitter Inc",
                effective_from="2018-06-07",
                effective_to="2022-10-28",
                is_active_now=False,
                is_delisted=True,
            ),
        )

        first = store.replace_universe_memberships(
            "sp500_historical_eodhd_v1",
            initial,
            snapshot_date="2026-07-27",
            imported_at="2026-07-27T10:00:00Z",
        )
        second = store.replace_universe_memberships(
            "sp500_historical_eodhd_v1",
            initial,
            snapshot_date="2026-07-27",
            imported_at="2026-07-27T10:00:00Z",
        )

        self.assertEqual((first, second), (2, 2))
        rows = connection.execute(
            """
            SELECT ticker, effective_from, effective_to, is_delisted, source
            FROM universe_memberships
            ORDER BY ticker
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("AAPL", "1982-11-30", None, 0, "eodhd"),
                ("TWTR", "2018-06-07", "2022-10-28", 1, "eodhd"),
            ],
        )
        self.assertEqual(
            connection.execute(
                "SELECT active FROM security_master WHERE ticker = 'TWTR'"
            ).fetchone()[0],
            0,
        )

        with self.assertRaises(ValueError):
            store.replace_universe_memberships(
                "sp500_historical_eodhd_v1",
                (),
                snapshot_date="2026-07-27",
                imported_at="2026-07-27T10:00:00Z",
            )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM universe_memberships"
            ).fetchone()[0],
            2,
        )

        connection.execute(
            """
            CREATE TRIGGER reject_bad_member
            BEFORE INSERT ON universe_memberships
            WHEN NEW.ticker = 'BAD'
            BEGIN
                SELECT RAISE(ABORT, 'rejected fixture');
            END
            """
        )
        failed = (
            HistoricalMembership(
                ticker="BAD",
                security_name="Bad Fixture",
                effective_from="2020-01-01",
                effective_to=None,
                is_active_now=False,
                is_delisted=True,
            ),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            store.replace_universe_memberships(
                "sp500_historical_eodhd_v1",
                failed,
                snapshot_date="2026-07-27",
                imported_at="2026-07-27T10:00:00Z",
            )
        self.assertEqual(
            connection.execute(
                """
                SELECT ticker FROM universe_memberships
                WHERE universe_key = 'sp500_historical_eodhd_v1'
                ORDER BY ticker
                """
            ).fetchall(),
            [("AAPL",), ("TWTR",)],
        )

    def test_symbol_changes_are_idempotent_identity_hints(self):
        connection = sqlite3.connect(":memory:")
        store = ResearchPriceStore(connection)
        store.initialize()
        changes = (
            SymbolChange(
                old_symbol="FB",
                new_symbol="META",
                effective_date="2022-06-09",
                exchange="US",
                company_name="Meta Platforms Inc",
            ),
        )

        self.assertEqual(
            store.upsert_symbol_changes(
                changes,
                snapshot_date="2026-07-27",
                imported_at="2026-07-27T10:00:00Z",
            ),
            1,
        )
        self.assertEqual(
            store.upsert_symbol_changes(
                changes,
                snapshot_date="2026-07-27",
                imported_at="2026-07-27T10:00:00Z",
            ),
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT old_symbol, new_symbol FROM security_symbol_changes"
            ).fetchall(),
            [("FB", "META")],
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM daily_prices"
            ).fetchone()[0],
            0,
        )

    def test_normalization_preserves_raw_prices_and_builds_adjusted_ohlc(self):
        rows, segments = normalize_daily_rows(
            [daily("2026-01-02", 98, 104, 96, 100, 25, 1234)]
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["raw_open"], 98)
        self.assertEqual(row["raw_close"], 100)
        self.assertEqual(row["adjusted_close"], 25)
        self.assertAlmostEqual(row["adjustment_factor"], 0.25)
        self.assertAlmostEqual(row["adjusted_open"], 24.5)
        self.assertAlmostEqual(row["adjusted_high"], 26)
        self.assertAlmostEqual(row["adjusted_low"], 24)
        self.assertEqual(row["volume"], 1234)
        self.assertEqual(row["segment_id"], 1)
        self.assertTrue(segments[0]["is_current_segment"])

    def test_gap_larger_than_180_days_creates_a_new_current_segment(self):
        rows, segments = normalize_daily_rows(
            [
                daily("2022-02-25", 10, 11, 9, 10, 10),
                daily("2024-10-21", 20, 21, 19, 20, 20),
                daily("2024-10-22", 21, 22, 20, 21, 21),
            ]
        )

        self.assertEqual([row["segment_id"] for row in rows], [1, 2, 2])
        self.assertEqual(len(segments), 2)
        self.assertFalse(segments[0]["is_current_segment"])
        self.assertTrue(segments[1]["is_current_segment"])
        self.assertGreater(segments[1]["break_before_days"], 180)

    def test_duplicate_dates_and_invalid_price_structure_are_rejected(self):
        duplicate = daily("2026-01-02", 10, 11, 9, 10, 10)
        with self.assertRaisesRegex(ValueError, "duplicate date"):
            normalize_daily_rows([duplicate, duplicate])
        with self.assertRaisesRegex(ValueError, "invalid raw OHLC"):
            normalize_daily_rows(
                [daily("2026-01-02", 10, 9, 8, 10, 10)]
            )

    def test_provider_leading_all_zero_placeholder_is_ignored_only_before_listing(self):
        leading_placeholder = daily("2025-05-21", 0, 0, 0, 0, 0, 0)
        valid = daily("2025-05-22", 39.25, 40.26, 37.02, 37.56, 37.56)

        rows, segments = normalize_daily_rows([leading_placeholder, valid])

        self.assertEqual([row["date"] for row in rows], ["2025-05-22"])
        self.assertEqual(segments[0]["first_date"], "2025-05-22")
        with self.assertRaisesRegex(ValueError, "invalid raw OHLC"):
            normalize_daily_rows([valid, daily("2025-05-23", 0, 0, 0, 0, 0, 0)])

    def test_import_is_atomic_and_idempotent(self):
        connection = sqlite3.connect(":memory:")
        store = ResearchPriceStore(connection)
        store.initialize()
        security = {
            "ticker": "AAA",
            "name": "Example",
            "exchange": "NASDAQ",
            "isin": "US0000000001",
            "cik": 1,
            "asof": "2026-07-24",
            "selection_rule": "liquid_us_common_v1",
            "classification": {
                "sector_key": "technology",
                "sic": "7372",
                "industry_description": "Software",
                "confidence": 1.0,
                "source": "sec",
                "rule_version": "sec_sic_v1",
            },
        }
        prices = [
            daily("2026-01-02", 10, 11, 9, 10, 10),
            daily("2026-01-05", 11, 12, 10, 11, 11),
        ]
        splits = [{"date": "2026-01-05", "split": "2.000000/1.000000"}]
        dividends = [
            {
                "date": "2026-01-02",
                "declarationDate": "2025-12-01",
                "recordDate": "2026-01-03",
                "paymentDate": "2026-01-10",
                "period": "Quarterly",
                "value": 0.1,
                "unadjustedValue": 0.1,
                "currency": "USD",
            }
        ]

        first = store.import_security(
            security,
            prices,
            splits,
            dividends,
            snapshot_date="2026-07-26",
            imported_at="2026-07-26T12:00:00Z",
        )
        second = store.import_security(
            security,
            prices,
            splits,
            dividends,
            snapshot_date="2026-07-26",
            imported_at="2026-07-26T12:00:00Z",
        )

        self.assertEqual(first.daily_rows, 2)
        self.assertEqual(second.daily_rows, 2)
        self.assertEqual(
            connection.execute("select count(*) from daily_prices").fetchone()[0],
            2,
        )
        self.assertEqual(
            connection.execute("select count(*) from splits").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("select count(*) from dividends").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "select count(*) from sector_classifications "
                "where taxonomy = 'sec'"
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
