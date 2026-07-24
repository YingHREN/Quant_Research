from __future__ import annotations

import copy
import unittest

import pandas as pd

from research.early_reversal import (
    build_early_reversal_rows,
    compare_next_open_entries,
)


def watch_history(
    *,
    prior_close: float = 94.0,
    prior_volume: float = 1_300_000.0,
    current_close: float = 96.0,
    current_volume: float = 1_500_000.0,
) -> pd.DataFrame:
    index = pd.bdate_range("2026-06-18", periods=22)
    frame = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Volume": 1_000_000.0,
        },
        index=index,
    )
    frame.iloc[-2] = [99.0, 100.0, 93.0, prior_close, prior_volume]
    frame.iloc[-1] = [95.0, 97.0, 94.0, current_close, current_volume]
    return frame


def reversal_rows(frame: pd.DataFrame, *, trendline: float | None = 96.8):
    rows = [
        {
            "descending_trendline": None,
            "trendline_breakout": False,
            "reversal_signal_count": 0,
        }
        for _ in range(len(frame))
    ]
    rows[-1]["descending_trendline"] = trendline
    return rows


class EarlyReversalWatchTest(unittest.TestCase):
    def test_entry_comparison_uses_each_signals_next_open_and_marks_unmatured_horizons(self):
        index = pd.bdate_range("2026-07-01", periods=8)
        close = pd.Series([100, 101, 102, 103, 104, 110, 112, 115], index=index)
        frame = pd.DataFrame(
            {
                "Open": [99, 100, 101, 102, 105, 111, 113, 116],
                "High": close + 2,
                "Low": close - 2,
                "Close": close,
                "Volume": 1_000_000.0,
            },
            index=index,
        )

        comparison = compare_next_open_entries(
            frame,
            early_observation_date=index[2],
            confirmation_date=index[4],
            horizons=(2, 5),
        )

        self.assertEqual(comparison["early"]["entry_date"], index[3].date().isoformat())
        self.assertEqual(comparison["early"]["entry_price"], 102.0)
        self.assertEqual(comparison["confirmed"]["entry_date"], index[5].date().isoformat())
        self.assertEqual(comparison["confirmed"]["entry_price"], 111.0)
        self.assertEqual(comparison["confirmation_delay_sessions"], 2)
        self.assertAlmostEqual(
            comparison["confirmation_entry_premium"],
            111.0 / 102.0 - 1.0,
        )
        self.assertAlmostEqual(comparison["early"]["returns"]["2"], 104.0 / 102.0 - 1)
        self.assertAlmostEqual(comparison["confirmed"]["returns"]["2"], 112.0 / 111.0 - 1)
        self.assertIsNone(comparison["confirmed"]["returns"]["5"])
        self.assertFalse(comparison["confirmed"]["matured"]["5"])

    def test_four_atomic_conditions_produce_a_watch_without_confirmation(self):
        frame = watch_history()
        source_reversal = reversal_rows(frame)

        row = build_early_reversal_rows(frame, source_reversal)[-1]

        self.assertEqual(row["early_reversal_score"], 100)
        self.assertTrue(row["early_reversal_watch"])
        self.assertEqual(
            row["early_reversal_conditions"],
            [
                "prior_session_selloff",
                "current_price_acceptance",
                "descending_trendline_proximity",
                "current_volume_support",
            ],
        )
        self.assertTrue(row["early_prior_session_selloff"])
        self.assertTrue(row["early_current_price_acceptance"])
        self.assertTrue(row["early_descending_trendline_proximity"])
        self.assertTrue(row["early_current_volume_support"])
        self.assertEqual(source_reversal[-1]["reversal_signal_count"], 0)

    def test_exactly_one_supporting_condition_produces_a_75_point_watch(self):
        frame = watch_history(current_volume=1_000_000.0)

        row = build_early_reversal_rows(frame, reversal_rows(frame))[-1]

        self.assertEqual(row["early_reversal_score"], 75)
        self.assertTrue(row["early_reversal_watch"])
        self.assertFalse(row["early_current_volume_support"])

    def test_required_selloff_and_acceptance_gates_cannot_be_replaced_by_support(self):
        no_selloff = watch_history(prior_close=96.0)
        no_acceptance = watch_history(current_close=94.0)

        selloff_row = build_early_reversal_rows(
            no_selloff, reversal_rows(no_selloff)
        )[-1]
        acceptance_row = build_early_reversal_rows(
            no_acceptance, reversal_rows(no_acceptance)
        )[-1]

        self.assertFalse(selloff_row["early_reversal_watch"])
        self.assertFalse(selloff_row["early_prior_session_selloff"])
        self.assertFalse(acceptance_row["early_reversal_watch"])
        self.assertFalse(acceptance_row["early_current_price_acceptance"])

    def test_future_rows_cannot_change_prior_early_watch(self):
        prefix = watch_history()
        future = pd.DataFrame(
            {
                "Open": [97.0, 92.0],
                "High": [99.0, 94.0],
                "Low": [90.0, 88.0],
                "Close": [91.0, 89.0],
                "Volume": [2_000_000.0, 1_700_000.0],
            },
            index=pd.bdate_range(prefix.index[-1] + pd.Timedelta(days=1), periods=2),
        )
        extended = pd.concat([prefix, future])
        prefix_reversal = reversal_rows(prefix)
        extended_reversal = reversal_rows(extended)
        extended_reversal[len(prefix) - 1]["descending_trendline"] = 96.8

        expected = build_early_reversal_rows(prefix, prefix_reversal)
        actual = build_early_reversal_rows(extended, extended_reversal)[: len(prefix)]

        self.assertEqual(actual, expected)

    def test_inputs_are_not_mutated(self):
        frame = watch_history()
        source_frame = frame.copy(deep=True)
        source_reversal = reversal_rows(frame)
        source_reversal_copy = copy.deepcopy(source_reversal)

        build_early_reversal_rows(frame, source_reversal)

        pd.testing.assert_frame_equal(frame, source_frame)
        self.assertEqual(source_reversal, source_reversal_copy)


if __name__ == "__main__":
    unittest.main()
