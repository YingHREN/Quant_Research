from __future__ import annotations

import sqlite3
import unittest

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
