from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from tests.test_web_api import (
    FakeManager,
    FakeRepository,
    InjectedForecastService,
    price_history,
)
from web.app import create_app
from web.services.entry_signals import (
    EntrySignalArtifactStore,
    EntrySignalService,
    merge_entry_signal_rows,
)


def built_rows(history):
    return [
        {
            "time": pd.Timestamp(timestamp).date().isoformat(),
            "strict_vcp_active": False,
        }
        for timestamp in history.index
    ]


class EntrySignalServiceTest(unittest.TestCase):
    def test_app_factory_enables_persistent_entry_artifacts_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(
                {
                    "TESTING": True,
                    "FORECAST_SERVICE": InjectedForecastService(),
                    "ENTRY_SIGNAL_ARTIFACT_CACHE_ENABLED": True,
                    "ENTRY_SIGNAL_ARTIFACT_CACHE_PATH": str(
                        Path(temporary) / "analysis_cache.db"
                    ),
                },
                FakeRepository(),
                FakeManager(),
            )

        service = app.extensions["dashboard_entry_signal_service"]
        self.assertIsInstance(
            service._artifact_store,
            EntrySignalArtifactStore,
        )

    def test_persistent_artifact_is_reused_by_a_new_service(self):
        history = price_history(periods=80)
        with tempfile.TemporaryDirectory() as temporary, patch(
            "web.services.entry_signals.build_entry_signal_rows",
            side_effect=built_rows,
        ) as build:
            path = Path(temporary) / "analysis_cache.db"
            first = EntrySignalService(
                artifact_store=EntrySignalArtifactStore(path)
            )
            second = EntrySignalService(
                artifact_store=EntrySignalArtifactStore(path)
            )

            first.build("AAA", history)
            restored = second.build("AAA", history.copy())

        self.assertEqual(build.call_count, 1)
        self.assertEqual(restored, built_rows(history))

    def test_persistent_artifact_misses_after_history_correction(self):
        history = price_history(periods=80)
        corrected = history.copy()
        corrected.iloc[20, corrected.columns.get_loc("Close")] += 0.25
        with tempfile.TemporaryDirectory() as temporary, patch(
            "web.services.entry_signals.build_entry_signal_rows",
            side_effect=built_rows,
        ) as build:
            store = EntrySignalArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            EntrySignalService(artifact_store=store).build("AAA", history)
            EntrySignalService(artifact_store=store).build("AAA", corrected)

        self.assertEqual(build.call_count, 2)

    def test_corrupt_persistent_artifact_safely_rebuilds(self):
        history = price_history(periods=80)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "analysis_cache.db"
            store = EntrySignalArtifactStore(path)
            EntrySignalService(artifact_store=store).build("AAA", history)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE entry_signal_artifacts SET payload = ?",
                    (sqlite3.Binary(b"corrupt"),),
                )
            with patch(
                "web.services.entry_signals.build_entry_signal_rows",
                side_effect=built_rows,
            ) as build:
                restored = EntrySignalService(
                    artifact_store=store
                ).build("AAA", history)

        self.assertEqual(build.call_count, 1)
        self.assertEqual(restored, built_rows(history))

    def test_identical_history_is_cached_and_results_are_isolated(self):
        history = price_history(periods=80)
        with patch(
            "web.services.entry_signals.build_entry_signal_rows",
            side_effect=built_rows,
        ) as build:
            service = EntrySignalService()
            first = service.build("aaa", history)
            first[-1]["strict_vcp_active"] = True
            second = service.build("AAA", history.copy())

        self.assertEqual(build.call_count, 1)
        self.assertFalse(second[-1]["strict_vcp_active"])

    def test_append_correction_and_ticker_each_invalidate_identity(self):
        history = price_history(periods=80)
        appended = price_history(periods=81, end="2026-07-22")
        corrected = history.copy()
        corrected.iloc[20, corrected.columns.get_loc("Close")] += 0.25
        with patch(
            "web.services.entry_signals.build_entry_signal_rows",
            side_effect=built_rows,
        ) as build:
            service = EntrySignalService()
            service.build("AAA", history)
            service.build("AAA", appended)
            service.build("AAA", corrected)
            service.build("BBB", corrected)

        self.assertEqual(build.call_count, 4)

    def test_cache_is_lru_bounded(self):
        history = price_history(periods=80)
        with patch(
            "web.services.entry_signals.build_entry_signal_rows",
            side_effect=built_rows,
        ) as build:
            service = EntrySignalService(max_cache_size=2)
            service.build("AAA", history)
            service.build("BBB", history)
            service.build("CCC", history)
            service.build("AAA", history)

        self.assertEqual(build.call_count, 4)

    def test_rejects_invalid_cache_size(self):
        for value in (True, 0, -1, 1.5):
            with self.subTest(value=value), self.assertRaises(
                (TypeError, ValueError)
            ):
                EntrySignalService(max_cache_size=value)

    def test_merge_matches_dates_not_positions_and_allows_signal_superset(self):
        chart = [
            {"time": "2026-07-21", "close": 10.0},
            {"time": "2026-07-22", "close": 11.0},
        ]
        signals = [
            {"time": "2026-07-20", "strict_vcp_active": False},
            {"time": "2026-07-22", "strict_vcp_active": True},
            {"time": "2026-07-21", "strict_vcp_active": False},
        ]

        merged = merge_entry_signal_rows(chart, signals)

        self.assertFalse(merged[0]["strict_vcp_active"])
        self.assertTrue(merged[1]["strict_vcp_active"])

    def test_merge_rejects_missing_chart_date(self):
        with self.assertRaises(ValueError):
            merge_entry_signal_rows(
                [{"time": "2026-07-22"}],
                [{"time": "2026-07-21"}],
            )


if __name__ == "__main__":
    unittest.main()
