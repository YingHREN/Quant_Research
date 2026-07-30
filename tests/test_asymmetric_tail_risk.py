import unittest

import numpy as np
import pandas as pd

from research.asymmetric_tail_risk import attach_asymmetric_tail_targets


def _feature_frame(dates):
    return pd.DataFrame(
        {"feature": np.arange(len(dates), dtype=float)},
        index=pd.MultiIndex.from_product(
            (("AAA",), dates),
            names=("ticker", "observation_date"),
        ),
    )


def _history(dates, *, opens=None, lows=None, closes=None):
    size = len(dates)
    open_values = (
        np.full(size, 100.0)
        if opens is None
        else np.asarray(opens, dtype=float)
    )
    low_values = (
        np.full(size, 99.0)
        if lows is None
        else np.asarray(lows, dtype=float)
    )
    close_values = (
        np.full(size, 100.0)
        if closes is None
        else np.asarray(closes, dtype=float)
    )
    return pd.DataFrame(
        {
            "Open": open_values,
            "High": np.maximum(open_values, close_values) + 1.0,
            "Low": low_values,
            "Close": close_values,
            "Volume": 1_000_000.0,
        },
        index=dates,
    )


class AsymmetricTailTargetTest(unittest.TestCase):
    def test_uses_next_open_terminal_close_and_complete_future_low_path(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        opens = [90.0, 100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0]
        lows = [89.0, 99.0, 96.0, 92.0, 93.0, 94.0, 94.0, 93.0]
        closes = [91.0, 99.0, 97.0, 94.0, 96.0, 94.0, 95.0, 93.0]

        result = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {
                "AAA": _history(
                    dates,
                    opens=opens,
                    lows=lows,
                    closes=closes,
                )
            },
        )
        first = result.loc[("AAA", dates[0])]

        self.assertAlmostEqual(first["terminal_return_5"], -0.06)
        self.assertAlmostEqual(first["path_mae_5"], -0.08)
        self.assertEqual(first["down_event_5"], 1.0)
        self.assertEqual(first["extreme_rebound_5"], 0.0)
        self.assertEqual(first["tail_label_end_date_5"], dates[5])

    def test_terminal_loss_alone_can_trigger_down_event(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 98.0)
        closes = np.full(8, 100.0)
        closes[5] = 95.0

        first = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {"AAA": _history(dates, lows=lows, closes=closes)},
        ).loc[("AAA", dates[0])]

        self.assertAlmostEqual(first["path_mae_5"], -0.02)
        self.assertAlmostEqual(first["terminal_return_5"], -0.05)
        self.assertEqual(first["down_event_5"], 1.0)

    def test_extreme_rebound_is_separate_from_downside_path(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 99.0)
        lows[2] = 92.0
        closes = np.full(8, 100.0)
        closes[5] = 112.0

        first = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {"AAA": _history(dates, lows=lows, closes=closes)},
        ).loc[("AAA", dates[0])]

        self.assertEqual(first["down_event_5"], 1.0)
        self.assertEqual(first["extreme_rebound_5"], 1.0)

    def test_missing_path_or_immature_tail_remains_missing(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 99.0)
        lows[3] = np.nan

        result = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {"AAA": _history(dates, lows=lows)},
        )
        target_columns = [
            "terminal_return_5",
            "path_mae_5",
            "down_event_5",
            "extreme_rebound_5",
            "tail_label_end_date_5",
        ]

        self.assertTrue(
            result.loc[("AAA", dates[0]), target_columns].isna().all()
        )
        self.assertTrue(result[target_columns].iloc[-5:].isna().all().all())

    def test_rejects_duplicate_history_dates_and_invalid_horizon(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        duplicate = _history(dates)
        duplicate = pd.concat((duplicate, duplicate.iloc[[0]]))

        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_asymmetric_tail_targets(
                _feature_frame(dates),
                {"AAA": duplicate},
            )
        with self.assertRaisesRegex(ValueError, "horizon"):
            attach_asymmetric_tail_targets(
                _feature_frame(dates),
                {"AAA": _history(dates)},
                horizon=True,
            )


if __name__ == "__main__":
    unittest.main()
