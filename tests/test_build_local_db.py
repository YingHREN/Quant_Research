from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import pandas as pd

from build_local_db import (
    backfill,
    latest_eodhd_raw_root,
    update,
    update_tickers,
)
from data.fetch import StockData


class BuildLocalDatabaseTest(unittest.TestCase):
    def test_latest_eodhd_raw_root_ignores_non_snapshot_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "2026-07-28").mkdir()
            (root / "2026-07-29").mkdir()
            (root / "partial").mkdir()

            selected = latest_eodhd_raw_root(root)

        self.assertEqual(selected, root / "2026-07-29")

    def test_update_defaults_to_eodhd_rebuild_for_existing_main_tickers(self):
        with (
            mock.patch(
                "build_local_db.rebuild_from_eodhd",
                return_value="eodhd-summary",
            ) as rebuild,
            mock.patch(
                "build_local_db.local_tickers",
                return_value=["AAA", "QQQ"],
            ),
            mock.patch(
                "build_local_db.update_tickers",
                return_value=["AAA", "QQQ", "XLK"],
            ),
        ):
            result = update(
                research_database="research.db",
                output_database="prices.db",
                raw_root="eodhd-raw",
            )

        self.assertEqual(result, "eodhd-summary")
        rebuild.assert_called_once_with(
            "research.db",
            "prices.db",
            tickers=("AAA", "QQQ", "XLK"),
            raw_root="eodhd-raw",
        )

    def test_update_keeps_tiingo_only_as_explicit_provider(self):
        with mock.patch(
            "build_local_db.backfill",
            return_value="tiingo-summary",
        ) as legacy:
            result = update(provider="tiingo")

        self.assertEqual(result, "tiingo-summary")
        legacy.assert_called_once_with(years=1, workers=1)

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

    def test_explicit_backfill_fetches_in_parallel_but_persists_safely(self):
        connection = sqlite3.connect(":memory:")
        history = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.5],
                "Volume": [100.0],
            },
            index=pd.DatetimeIndex(["2026-01-02"], name="Date"),
        )
        gate = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def fake_fetch(ticker, *, period, use_cache):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active >= 2:
                    gate.set()
            if not gate.wait(timeout=1):
                raise AssertionError("fetches did not overlap")
            with lock:
                active -= 1
            return StockData(ticker, history, ok=True)

        with mock.patch("build_local_db.time.sleep"):
            summary = backfill(
                years=10,
                workers=2,
                tickers=("AAA", "BBB"),
                connection=connection,
                fetcher=fake_fetch,
                asof=date(2026, 1, 3),
            )

        self.assertEqual(summary.succeeded, 2)
        self.assertEqual(peak, 2)
        self.assertEqual(
            connection.execute("SELECT COUNT(DISTINCT ticker) FROM prices").fetchone()[0],
            2,
        )

    def test_backfill_resume_skips_successful_same_window_ingestions(self):
        connection = sqlite3.connect(":memory:")
        history = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.5],
                "Volume": [100.0],
            },
            index=pd.DatetimeIndex(["2026-01-02"], name="Date"),
        )
        calls = []

        def fake_fetch(ticker, *, period, use_cache):
            calls.append(ticker)
            return StockData(ticker, history, ok=True)

        arguments = {
            "years": 10,
            "workers": 1,
            "tickers": ("AAA",),
            "connection": connection,
            "fetcher": fake_fetch,
            "asof": date(2026, 1, 3),
        }
        with mock.patch("build_local_db.time.sleep"):
            first = backfill(**arguments)
            resumed = backfill(**arguments)

        self.assertEqual(first.succeeded, 1)
        self.assertEqual(first.skipped, 0)
        self.assertEqual(resumed.succeeded, 0)
        self.assertEqual(resumed.skipped, 1)
        self.assertEqual(calls, ["AAA"])


if __name__ == "__main__":
    unittest.main()
