from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.regime_threshold_direction import (
    RegimeThresholdDataUnavailable,
    attach_absolute_and_qqq_relative_targets,
)


def _history(*, exit_close, minimum_low, start="2026-01-02"):
    dates = pd.bdate_range(start, periods=12)
    opens = np.full(len(dates), 100.0)
    closes = np.full(len(dates), 100.0)
    closes[5] = float(exit_close)
    lows = np.full(len(dates), 99.0)
    lows[1:6] = np.asarray(
        [98.0, float(minimum_low), 97.0, 96.0, 95.0]
    )
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) + 1.0,
            "Low": lows,
            "Close": closes,
            "Adj Close": closes,
            "Volume": np.full(len(dates), 1_000_000.0),
        },
        index=dates,
    )


def _feature_frame(histories):
    rows = []
    for ticker in ("AAA",):
        for date in histories[ticker].index:
            rows.append(
                {
                    "ticker": ticker,
                    "observation_date": date,
                    "feature": 1.0,
                }
            )
    return pd.DataFrame(rows).set_index(
        ["ticker", "observation_date"]
    )


class TargetAlignmentTest(unittest.TestCase):
    def test_targets_share_exact_entry_exit_and_keep_semantics_separate(self):
        histories = {
            "AAA": _history(exit_close=102.0, minimum_low=90.0),
            "QQQ": _history(exit_close=105.0, minimum_low=94.0),
        }
        frame = _feature_frame(histories)
        frame_before = frame.copy(deep=True)
        histories_before = {
            ticker: history.copy(deep=True)
            for ticker, history in histories.items()
        }
        date = histories["AAA"].index[0]

        result = attach_absolute_and_qqq_relative_targets(frame, histories)

        self.assertAlmostEqual(
            result.loc[("AAA", date), "absolute_return_5"],
            0.02,
        )
        self.assertEqual(
            result.loc[("AAA", date), "absolute_direction_5"],
            "up",
        )
        self.assertAlmostEqual(
            result.loc[("AAA", date), "qqq_relative_return_5"],
            -0.03,
        )
        self.assertEqual(
            result.loc[("AAA", date), "qqq_relative_direction_5"],
            "down",
        )
        self.assertAlmostEqual(
            result.loc[("AAA", date), "maximum_adverse_excursion_5"],
            -0.10,
        )
        self.assertEqual(
            result.loc[("AAA", date), "entry_date_5"],
            histories["AAA"].index[1],
        )
        self.assertEqual(
            result.loc[("AAA", date), "label_end_date_5"],
            histories["AAA"].index[5],
        )
        pd.testing.assert_frame_equal(frame, frame_before)
        for ticker in histories:
            pd.testing.assert_frame_equal(
                histories[ticker],
                histories_before[ticker],
            )

    def test_immature_tail_and_incomplete_low_path_remain_missing(self):
        histories = {
            "AAA": _history(exit_close=102.0, minimum_low=90.0),
            "QQQ": _history(exit_close=105.0, minimum_low=94.0),
        }
        histories["AAA"].loc[
            histories["AAA"].index[3],
            "Low",
        ] = np.nan
        frame = _feature_frame(histories)

        result = attach_absolute_and_qqq_relative_targets(frame, histories)

        first = ("AAA", histories["AAA"].index[0])
        tail = ("AAA", histories["AAA"].index[-3])
        self.assertTrue(
            pd.isna(result.loc[first, "maximum_adverse_excursion_5"])
        )
        self.assertTrue(pd.isna(result.loc[tail, "absolute_return_5"]))
        self.assertTrue(pd.isna(result.loc[tail, "absolute_direction_5"]))
        self.assertTrue(pd.isna(result.loc[tail, "qqq_relative_return_5"]))
        self.assertTrue(
            pd.isna(result.loc[tail, "qqq_relative_direction_5"])
        )

    def test_missing_or_misaligned_qqq_fails_closed(self):
        stock = _history(exit_close=102.0, minimum_low=90.0)
        frame = _feature_frame({"AAA": stock})
        with self.assertRaisesRegex(
            RegimeThresholdDataUnavailable,
            "QQQ",
        ):
            attach_absolute_and_qqq_relative_targets(
                frame,
                {"AAA": stock},
            )

        qqq = _history(
            exit_close=105.0,
            minimum_low=94.0,
            start="2026-03-02",
        )
        with self.assertRaisesRegex(
            RegimeThresholdDataUnavailable,
            "aligned",
        ):
            attach_absolute_and_qqq_relative_targets(
                frame,
                {"AAA": stock, "QQQ": qqq},
            )


if __name__ == "__main__":
    unittest.main()
