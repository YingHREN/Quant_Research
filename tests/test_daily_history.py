from __future__ import annotations

from datetime import date, datetime, timezone
import sqlite3
import unittest

import numpy as np
import pandas as pd

from data.daily_history import (
    InvalidDailyHistory,
    audit_history,
    coverage_report,
    history_start,
    persist_history,
)


def frame(dates, closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": np.full(len(closes), 1_000.0),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


class DailyHistoryAuditTest(unittest.TestCase):
    def test_history_start_handles_leap_day_without_rolling_into_march(self):
        self.assertEqual(history_start(date(2024, 2, 29), 10), date(2014, 2, 28))

    def test_audit_reports_duplicates_gaps_and_suspicious_adjusted_returns(self):
        values = frame(
            ["2026-01-02", "2026-01-02", "2026-01-20"],
            [100.0, 100.0, 170.0],
        )

        result = audit_history(values)

        self.assertEqual(result.duplicate_dates, 1)
        self.assertEqual(result.long_gaps, 1)
        self.assertEqual(result.suspicious_returns, 1)
        self.assertEqual(result.invalid_rows, 0)

    def test_persistence_rejects_invalid_bars_atomically(self):
        connection = sqlite3.connect(":memory:")
        values = frame(["2026-01-02"], [100.0])
        values.loc[:, "High"] = 99.0

        with self.assertRaises(InvalidDailyHistory):
            persist_history(
                connection,
                "AAA",
                values,
                provider="test",
                adjustment="split_dividend_adjusted",
                requested_start=date(2016, 1, 1),
                fetched_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            )

        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='prices'"
        ).fetchone()
        if table:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0], 0)

    def test_persistence_is_non_destructive_and_records_reproducible_coverage(self):
        connection = sqlite3.connect(":memory:")
        old = frame(["2015-12-31"], [90.0])
        persist_history(
            connection,
            "AAA",
            old,
            provider="seed",
            adjustment="split_dividend_adjusted",
            requested_start=date(2015, 1, 1),
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        refreshed = frame(["2016-01-04", "2026-01-02"], [100.0, 150.0])

        result = persist_history(
            connection,
            "AAA",
            refreshed,
            provider="tiingo",
            adjustment="split_dividend_adjusted",
            requested_start=date(2016, 1, 1),
            fetched_at=datetime(2026, 1, 3, 8, 30, tzinfo=timezone.utc),
        )

        dates = [
            row[0]
            for row in connection.execute(
                "SELECT date FROM prices WHERE ticker='AAA' ORDER BY date"
            )
        ]
        self.assertEqual(dates, ["2015-12-31", "2016-01-04", "2026-01-02"])
        self.assertEqual(result.ticker, "AAA")
        self.assertEqual(result.first_date, "2015-12-31")
        self.assertEqual(result.last_date, "2026-01-02")
        self.assertEqual(result.row_count, 3)
        self.assertGreater(result.coverage_years, 10.0)
        self.assertTrue(result.meets_eight_year_floor)
        self.assertEqual(result.provider, "tiingo")
        self.assertEqual(result.adjustment, "split_dividend_adjusted")
        self.assertEqual(result.source_cutoff, "2026-01-02")
        self.assertRegex(result.revision, r"^[0-9a-f]{64}$")

        ingestion = connection.execute(
            """
            SELECT requested_start, fetched_at, source_cutoff, source_rows,
                   provider, adjustment, revision
            FROM price_ingestions
            WHERE ticker='AAA' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        self.assertEqual(ingestion[0], "2016-01-01")
        self.assertEqual(ingestion[2:6], ("2026-01-02", 2, "tiingo", "split_dividend_adjusted"))
        self.assertIn("+00:00", ingestion[1])
        self.assertEqual(ingestion[6], result.revision)
        self.assertEqual(coverage_report(connection), [result])

    def test_duplicate_dates_are_rejected_before_any_rows_are_written(self):
        connection = sqlite3.connect(":memory:")
        values = frame(["2026-01-02", "2026-01-02"], [100.0, 101.0])

        with self.assertRaisesRegex(InvalidDailyHistory, "duplicate"):
            persist_history(
                connection,
                "AAA",
                values,
                provider="test",
                adjustment="split_dividend_adjusted",
                requested_start=date(2016, 1, 1),
                fetched_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
