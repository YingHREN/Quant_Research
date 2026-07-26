from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from tests.test_web_api import price_history
from web.services.entry_signals import (
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
